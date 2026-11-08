#!/usr/bin/env python3
"""Verify library artifacts without executing them; publish with credentials only in a separate job."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile

import libs
import container_build


def filenames(package):
    """Return the exact expected release assets; each build revision has its own checksum file."""
    return [package['stem'] + suffix for suffix in ['.tar.gz', '.json', '.sha256']]


def verify_bundle(directory, package, plan, root=libs.ROOT):
    """Check hashes, immutable plan identity and every archive entry before restore or publication."""
    archive_name, manifest_name, checksums_name = filenames(package)
    expected_names = {archive_name, manifest_name}
    checksums = {}
    for line in (directory / checksums_name).read_text().splitlines():
        digest, name = line.split('  ', 1)
        if name not in expected_names or name in checksums or not libs.SHA256.fullmatch(digest):
            raise ValueError('Invalid artifact checksum list')
        checksums[name] = digest
    if set(checksums) != expected_names: raise ValueError('Incomplete artifact checksums')
    for name, digest in checksums.items():
        path = directory / name
        if path.is_symlink() or libs.hash_file(path) != digest: raise ValueError('Artifact digest mismatch')
    manifest_path = directory / manifest_name
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 1 or manifest.get('package') != package or manifest.get('sdk') != plan['sdk'] or manifest.get('target') != libs.TARGET or manifest.get('abi') != libs.ABI:
        raise ValueError('Artifact does not match the frozen source/SDK/recipe plan')
    library = libs.read_catalog(root)['libraries'][package['name']]
    path = libs.relative(root, library['recipe']); specification = libs.recipe(root, library)
    expected_recipe = dict(path=library['recipe'], sha256=libs.hash_file(path), probe_sha256=libs.hash_file(libs.relative(path.parent, specification['probe'])), patches={name: libs.hash_file(libs.relative(path.parent, name)) for name in specification.get('patches', [])})
    if specification.get('overlays'): expected_recipe['overlays']={name:libs.hash_file(libs.relative(path.parent,name)) for name in specification['overlays']}
    if manifest.get('source_repository') != library['git'] or manifest.get('recipe') != expected_recipe or not libs.SHA.fullmatch(manifest.get('registry_commit', '')):
        raise ValueError('Missing/mismatched source and recipe provenance')
    if manifest.get('runtime_tested') is not False or manifest.get('verification') != 'compile-and-link-only':
        raise ValueError('Unsupported verification claim')
    expected = {}
    for entry in manifest['files']:
        path = PurePosixPath(entry['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in entry['path'] or not libs.allowed_payload(entry['path'],specification) or entry['path'] in expected:
            raise ValueError('Invalid manifest payload path')
        if 'link' in entry:
            link = PurePosixPath(entry['link'])
            if link.is_absolute(): raise ValueError('External payload link')
            parts = list(path.parent.parts)
            for part in link.parts:
                if part == '..':
                    if not parts: raise ValueError('External payload link')
                    parts.pop()
                elif part != '.': parts.append(part)
        elif not libs.SHA256.fullmatch(entry['sha256']) or entry['mode'] & 0o7000:
            raise ValueError('Invalid payload file binding')
        expected['sysroot/' + entry['path']] = entry
    for helper in specification.get('executables',[]):
        entry=expected.get('sysroot/'+helper)
        if not entry or 'link' in entry:raise ValueError('Missing/nonregular native helper: '+helper)
    seen = set()
    elf_count = 0
    with tarfile.open(directory / archive_name) as tar:
        for member in libs.safe_members(tar):
            if member.isdir(): continue
            if member.name in seen: raise ValueError('Duplicate archive payload')
            seen.add(member.name)
            if member.name == 'manifest.json':
                if not member.isfile() or json.load(tar.extractfile(member)) != manifest:
                    raise ValueError('Embedded/external manifest disagreement')
                continue
            if member.name not in expected: raise ValueError('Unexpected archive payload')
            entry = expected[member.name]
            if 'link' in entry:
                if not member.issym() or member.linkname != entry['link']: raise ValueError('Symlink binding mismatch')
            else:
                if not member.isfile() or member.size != entry['size'] or member.mode != entry['mode']:
                    raise ValueError('File binding mismatch')
                with tar.extractfile(member) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']: raise ValueError('Payload digest mismatch')
                with tar.extractfile(member) as stream:
                    if entry['path'] in specification.get('config_scripts', []):
                        if not libs.config_script_header(stream.read(128)):raise ValueError('Configuration helper is not a POSIX script')
                        stream.seek(0)
                    helper=entry['path'] in specification.get('executables',[])
                    magic = stream.read(4)
                    if helper and magic!=b'\x7fELF':raise ValueError('Declared native helper is not ELF')
                    if magic != b'\x7fELF' and re.fullmatch(r'.+\.so(?:\.[0-9]+)*', PurePosixPath(member.name).name):
                        raise ValueError('Shared-library output is not ELF')
                    if magic == b'\x7fELF':
                        elf_count += 1
                        stream.seek(0)
                        with tempfile.TemporaryDirectory(prefix='libs-elf-') as temporary:
                            binary = Path(temporary) / 'library.so'
                            with binary.open('wb') as output: shutil.copyfileobj(stream, output)
                            container_build.validate_elf(binary, shared=not helper, executable=helper)
    if seen != set(expected) | {'manifest.json'}: raise ValueError('Incomplete archive')
    if not elf_count or not any(path.endswith('.so') or '.so.' in path for path in expected):
        raise ValueError('Library archive has no shared-library output')
    return manifest


def prepare_build_cache(plan, output):
    """Keep only current frozen input IDs in the bounded Actions bundle snapshot."""
    cache = output / 'verified-cache'
    if cache.is_symlink(): raise ValueError('Build cache root must not be a symlink')
    cache.mkdir(parents=True, exist_ok=True)
    wanted = {package['input_id'] for package in plan['packages']}
    if any(not libs.SHA256.fullmatch(key) for key in wanted):
        raise ValueError('Invalid build cache input ID')
    for entry in cache.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not libs.SHA256.fullmatch(entry.name):
            raise ValueError('Unexpected build cache entry')
        if entry.name not in wanted: shutil.rmtree(entry)
    return cache


def verify_build_cache(root, directory, package, plan, builder_id):
    """Revalidate an untrusted cached bundle against this exact plan and builder."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Invalid build cache directory')
    wanted = set(filenames(package))
    if {p.name for p in directory.iterdir()} != wanted:
        raise ValueError('Incomplete or unexpected build cache files')
    for name in wanted:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Build cache assets must be regular files')
    manifest = verify_bundle(directory, package, plan, root=root)
    builder = manifest.get('builder', {})
    if builder.get('image') != builder_id or builder.get('reference') != plan['sdk']['builder_image']:
        raise ValueError('Build cache builder identity mismatch')
    return manifest


def restore_build_cache(root, package, plan, output, builder_id):
    """Restore only a fully verified completed bundle; never trust cache-hit status."""
    cache_root = output / 'verified-cache'
    if cache_root.is_symlink(): raise ValueError('Build cache root must not be a symlink')
    cache = libs.relative(cache_root, package['input_id'])
    if not cache.exists() and not cache.is_symlink(): return None
    manifest = verify_build_cache(root, cache, package, plan, builder_id)
    restored = output / 'cache-stages' / package['input_id']
    if restored.exists() or restored.is_symlink():
        raise ValueError('Build cache staging output already exists')
    restored.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=restored.parent, prefix='unpack-') as temporary:
        unpack = Path(temporary)
        with tarfile.open(cache / filenames(package)[0]) as archive:
            archive.extractall(unpack, members=libs.safe_members(archive), filter='data')
        # The data filter strips group/other write bits. Restore only the exact
        # nonprivileged modes already checked in the frozen-input bundle inventory.
        for entry in manifest['files']:
            if 'mode' in entry:
                path = libs.relative(unpack / 'sysroot', entry['path'])
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Extracted build cache file type mismatch')
                path.chmod(entry['mode'])
        if libs.inventory(unpack / 'sysroot') != manifest['files']:
            raise ValueError('Extracted build cache inventory mismatch')
        unpack.rename(restored)
    release_output = output / 'releases' / package['release']
    if release_output.exists() or release_output.is_symlink():
        raise ValueError('Refusing to overwrite existing release output from cache')
    release_output.mkdir(parents=True)
    for name in filenames(package): shutil.copy2(cache / name, release_output / name)
    return restored / 'sysroot'


def save_build_cache(root, package, plan, output, builder_id):
    """Atomically retain a completed, reverified bundle even if a later port fails."""
    cache_root = output / 'verified-cache'
    if cache_root.is_symlink(): raise ValueError('Build cache root must not be a symlink')
    cache_root.mkdir(parents=True, exist_ok=True)
    destination = libs.relative(cache_root, package['input_id'])
    source = output / 'releases' / package['release']
    verify_build_cache(root, source, package, plan, builder_id)
    if destination.exists() or destination.is_symlink():
        verify_build_cache(root, destination, package, plan, builder_id)
        if any(libs.hash_file(source / name) != libs.hash_file(destination / name) for name in filenames(package)):
            raise ValueError('Conflicting completed build cache')
        return
    with tempfile.TemporaryDirectory(dir=output, prefix='cache-copy-') as temporary:
        temporary = Path(temporary)
        for name in filenames(package): shutil.copy2(source / name, temporary / name)
        verify_build_cache(root, temporary, package, plan, builder_id)
        temporary.rename(destination)
    (output / 'verified-cache-updated').write_text('Completed bundles added\n')


def release_metadata(tag):
    """Read both drafts and published releases; distinguish explicit absence from API/auth failures."""
    result = subprocess.run(['gh','release','view',tag,'--repo','mochiOS/libs','--json','isDraft,assets'], env=os.environ.copy(), capture_output=True, text=True)
    if result.returncode:
        if result.stderr.strip().lower() == 'release not found': return None
        raise ValueError('Cannot read release metadata: ' + result.stderr.strip())
    value=json.loads(result.stdout)
    return dict(draft=value['isDraft'],assets=value['assets'])


def read_published_manifest(package):
    """Read only release metadata during scheduling; no upstream code or archive is executed."""
    with tempfile.TemporaryDirectory(prefix='libs-release-manifest-') as temporary:
        name = filenames(package)[1]
        libs.run(['gh', 'release', 'download', package['release'], '--repo', 'mochiOS/libs', '--pattern', name, '--dir', temporary], env=os.environ.copy())
        return json.loads((Path(temporary) / name).read_text())


def needs_build(plan):
    """Detect new complete build revisions and reject changed inputs under an existing revision."""
    if not plan['sdk']['available']: return False
    if plan['sdk'].get('development_only'): return True
    pending = False
    for package in plan['packages']:
        metadata = release_metadata(package['release'])
        if metadata is None or metadata['draft'] or not set(filenames(package)).issubset({asset['name'] for asset in metadata['assets']}):
            pending = True
            continue
        manifest = read_published_manifest(package)
        if manifest.get('package') != package or manifest.get('sdk') != plan['sdk']:
            raise ValueError('Published build inputs changed; increment revision instead of overwriting')
    return pending


def restore_existing(root, package, plan, output):
    """Reuse the exact published dependency artifact rather than silently rebuilding its bytes."""
    if plan['sdk'].get('development_only'): return None
    cache = output / 'restored' / package['input_id']
    if cache.exists():
        manifest = verify_bundle(cache, package, plan, root=root)
        if libs.inventory(cache / 'unpack/sysroot') != manifest['files']:
            raise ValueError('Restored dependency cache was modified')
        release_output = output / 'releases' / package['release']; release_output.mkdir(parents=True, exist_ok=True)
        for name in filenames(package):
            target = release_output / name
            if target.exists() and libs.hash_file(target) != libs.hash_file(cache / name):
                raise ValueError('Conflicting release output')
            if not target.exists(): shutil.copy2(cache / name, target)
        return cache / 'unpack/sysroot'
    metadata = release_metadata(package['release'])
    if metadata is None or metadata['draft']: return None
    wanted = set(filenames(package))
    present = wanted.intersection(asset['name'] for asset in metadata['assets'])
    if not present: return None
    if present != wanted:
        # The checksum file is uploaded last as the completion marker. A failed
        # partial upload is not an installed/published dependency cache entry.
        return None
    cache.mkdir(parents=True)
    for name in filenames(package):
        libs.run(['gh', 'release', 'download', package['release'], '--repo', 'mochiOS/libs', '--pattern', name, '--dir', cache], env=os.environ.copy())
    verify_bundle(cache, package, plan, root=root)
    unpack = cache / 'unpack'; unpack.mkdir()
    with tarfile.open(cache / filenames(package)[0]) as tar:
        tar.extractall(unpack, members=libs.safe_members(tar), filter='data')
    libs.inventory(unpack / 'sysroot')
    release_output = output / 'releases' / package['release']; release_output.mkdir(parents=True, exist_ok=True)
    for name in filenames(package): shutil.copy2(cache / name, release_output / name)
    return unpack / 'sysroot'


def publish(root, plan, artifacts):
    """Verify all inputs before creating drafts; never overwrite differing existing release assets."""
    if plan['sdk'].get('development_only'):
        raise libs.Blocked('Development SDK builds cannot be published')
    catalog = libs.validate_plan(root, plan)
    if not plan['sdk']['available']: raise libs.Blocked(plan['sdk']['reason'])
    # Verify the complete graph before making any remote mutation.
    for package in plan['packages']: verify_bundle(artifacts / package['release'], package, plan, root=root)
    expected_paths = {f'{package["release"]}/{name}' for package in plan['packages'] for name in filenames(package)}
    actual_paths = {p.relative_to(artifacts).as_posix() for p in artifacts.rglob('*') if p.is_file()}
    if actual_paths != expected_paths: raise ValueError('Unexpected/missing release artifact files')
    target = libs.run(['git', '-C', root, 'rev-parse', 'HEAD'])
    if not libs.SHA.fullmatch(target): raise ValueError('Invalid publication commit')
    for package in plan['packages']:
        directory = artifacts / package['release']; metadata = release_metadata(package['release'])
        if metadata is not None:
            existing = {asset['name']: asset for asset in metadata['assets']}
            for name in filenames(package):
                if name in existing:
                    expected = 'sha256:' + libs.hash_file(directory / name)
                    if existing[name].get('digest') != expected:
                        raise ValueError('Existing release differs; increment the build revision, never clobber assets')
            if not metadata['draft']:
                if set(filenames(package)).issubset(existing):
                    print('Already published: ' + package['release']); continue
                raise ValueError('Published release is incomplete for this build revision; choose a new revision, never append to a published release')
        else:
            notes = directory / '.notes.txt'
            notes.write_text(f"Source: {package['source']['tag']} ({package['source']['commit']})\nSDK: {plan['sdk']['version']}\nTarget: {libs.TARGET}; ABI: {libs.ABI}\nVerification: cross compile/link only; runtime has not been tested.\n")
            try:
                libs.run(['gh', 'release', 'create', package['release'], '--repo', 'mochiOS/libs', '--target', target, '--title', package['release'], '--notes-file', notes, '--draft'], env=os.environ.copy())
            finally: notes.unlink()
            existing = {}
        for name in filenames(package):
            if name not in existing:
                libs.run(['gh', 'release', 'upload', package['release'], directory / name, '--repo', 'mochiOS/libs'], env=os.environ.copy())
        # Read back GitHub's asset digests before making the draft public.
        readback=release_metadata(package['release'])
        if readback is None:raise ValueError('Cannot read back the uploaded draft; it remains unpublished')
        uploaded = {asset['name']: asset for asset in readback['assets']}
        if any(uploaded[name].get('digest') != 'sha256:' + libs.hash_file(directory / name) for name in filenames(package)):
            raise ValueError('Uploaded release digest verification failed; draft remains unpublished')
        if metadata is None or metadata['draft']:
            args = ['gh', 'release', 'edit', package['release'], '--repo', 'mochiOS/libs', '--draft=false', '--latest=false']
            if catalog['distribution']['prerelease']: args += ['--prerelease']
            libs.run(args, env=os.environ.copy())


def main():
    """Verify local release files or publish them in a dedicated credential-bearing job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'publish'])
    parser.add_argument('--plan', type=Path, default=Path('out/plan.json'))
    parser.add_argument('--artifacts', type=Path, default=Path('out/releases'))
    args = parser.parse_args(); plan = json.loads(args.plan.read_text())
    libs.validate_plan(libs.ROOT, plan)
    if args.command == 'publish': publish(libs.ROOT, plan, args.artifacts)
    else:
        for package in plan['packages']: verify_bundle(args.artifacts / package['release'], package, plan)
        print('All release artifacts verified')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print(f'Release verification/publication failed: {error}', file=sys.stderr); raise SystemExit(1)
