#!/usr/bin/env python3
"""Track upstream Git tags, freeze inputs, build isolated target libraries and publish verified releases."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'x86_64-unknown-mochios'
ABI = 'mochios-1'
NAME = re.compile(r'[a-z][a-z0-9-]{0,62}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


class Blocked(ValueError):
    """A required port/SDK is unavailable; no successful build is implied."""


def hash_file(path):
    """Return a streaming SHA-256 digest."""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(value):
    """Return a stable identity for JSON-compatible build inputs."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    """Publish metadata atomically after its corresponding operation succeeds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def relative(root, name):
    """Resolve a declared path without escaping its owning tree, including through symlinks."""
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in name:
        raise ValueError(f'Unsafe relative path: {name}')
    candidate = root / name
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes its owning tree: {name}')
    return candidate


def https_url(value):
    """Accept stable HTTPS locations without embedded credentials, tokens or fragments."""
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme == 'https' and bool(parsed.hostname) and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    """Reject HTTP redirects before the redirected SDK request is issued."""
    def redirect_request(self, request, fp, code, msg, headers, url):
        if urllib.parse.urlsplit(url).scheme != 'https': raise ValueError('Insecure SDK redirect')
        return super().redirect_request(request, fp, code, msg, headers, url)


def read_catalog(root=ROOT):
    """Validate the tracked library registry and every enabled recipe/dependency."""
    catalog = tomllib.loads((root / 'libraries.toml').read_text())
    if catalog.get('format') != 1:
        raise ValueError('Unsupported libraries.toml format')
    distribution = catalog['distribution']
    if distribution['repository'] != 'mochiOS/libs' or distribution['target'] != TARGET or distribution['abi'] != ABI:
        raise ValueError('Unknown distribution identity/target/ABI')
    for name, library in catalog['libraries'].items():
        if not NAME.fullmatch(name) or not https_url(library['git']):
            raise ValueError(f'Invalid library/upstream: {name}')
        expression = re.compile(library['tag_pattern'])
        if 'version' not in expression.groupindex:
            raise ValueError(f'Tag expression must capture version: {name}')
        if type(library['revision']) is not int or library['revision'] < 1:
            raise ValueError(f'Invalid build revision: {name}')
        if type(library['enabled']) is not bool:
            raise ValueError(f'enabled must be boolean: {name}')
        if library['enabled']:
            recipe(root, library)
        elif not library.get('reason'):
            raise ValueError(f'Planned library needs a reason: {name}')
    order(catalog)
    return catalog


def recipe(root, library):
    """Load a repository-owned recipe; arbitrary shell command strings are not accepted."""
    path = relative(root, library['recipe'])
    value = json.loads(path.read_text())
    if value.get('format') != 1 or value.get('backend') != 'cmake':
        raise ValueError(f'Unsupported recipe: {path}')
    for key, option in value['options'].items():
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or type(option) not in [str, int, bool]:
            raise ValueError('Invalid CMake recipe option')
        if key.startswith('CMAKE_') or key in ['FETCHCONTENT_FULLY_DISCONNECTED', 'FETCHCONTENT_UPDATES_DISCONNECTED']:
            raise ValueError('Recipes cannot replace toolchain/isolation settings')
    for license_path in value['licenses']:
        relative(root, license_path)  # Lexical validation; source location is checked after checkout.
    relative(path.parent, value['probe']).read_text()
    if not value['shared_libraries'] or any('/' in item or '..' in item for item in value['shared_libraries']):
        raise ValueError('Shared library patterns must be basenames')
    if not value['link_libraries'] or any(not re.fullmatch(r'[A-Za-z0-9_+-]+', item) for item in value['link_libraries']):
        raise ValueError('Invalid link library name')
    return value


def order(catalog):
    """Topologically order enabled libraries and reject missing/disabled dependencies and cycles."""
    libraries = catalog['libraries']
    done, visiting, result = set(), set(), []
    def visit(name):
        if name not in libraries or not libraries[name]['enabled']:
            raise ValueError(f'Enabled library depends on unavailable port: {name}')
        if name in visiting:
            raise ValueError(f'Dependency cycle: {name}')
        if name in done:
            return
        visiting.add(name)
        for dependency in libraries[name]['dependencies']:
            visit(dependency)
        visiting.remove(name)
        done.add(name)
        result.append(name)
    for name, library in libraries.items():
        if library['enabled']:
            visit(name)
    return result


def version(value):
    """Compare numeric stable versions without treating development tags as releases."""
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError(f'Unsupported stable version: {value}')
    return tuple(int(part) for part in value.split('.'))


def parse_tags(text, pattern):
    """Resolve annotated tags to peeled commits and reject ambiguous versions."""
    refs = {}
    for line in text.splitlines():
        commit, ref = line.split('\t', 1)
        if not SHA.fullmatch(commit) or not ref.startswith('refs/tags/'):
            raise ValueError('Invalid upstream ls-remote response')
        refs[ref] = commit
    tags = {}
    for ref, commit in refs.items():
        if ref.endswith('^{}'):
            continue
        tag = ref[len('refs/tags/'):]
        match = re.fullmatch(pattern, tag)
        if match:
            number = match['version']
            key = version(number)
            item = dict(tag=tag, version=number, commit=refs.get(ref + '^{}', commit))
            if key in tags and tags[key] != item:
                raise ValueError(f'Ambiguous upstream version: {number}')
            tags[key] = item
    if not tags:
        raise ValueError('No matching stable upstream tag')
    return tags[max(tags)]


def clean_environment():
    """Do not pass CI tokens, credential helpers, or host compiler search paths to build tools."""
    env = {k: v for k, v in os.environ.items() if not any(part in k.upper() for part in ['TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL'])}
    for key in ['SSH_AUTH_SOCK', 'CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH',
                'PKG_CONFIG_PATH', 'CMAKE_PREFIX_PATH', 'CC', 'CXX', 'CPPFLAGS', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS']:
        env.pop(key, None)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0', CARGO_NET_OFFLINE='true')
    return env


def run(args, *, cwd=None, log=None, env=None):
    """Run a command without shell interpolation, retaining complete failures when a log is supplied."""
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a') as output:
            subprocess.run([str(a) for a in args], cwd=cwd, env=env or clean_environment(), stdout=output, stderr=subprocess.STDOUT, check=True)
    else:
        return subprocess.check_output([str(a) for a in args], cwd=cwd, env=env or clean_environment(), text=True).strip()


def sdk_lock(root=ROOT):
    """Read a fixed SDK release; never substitute AppCore or a host compiler for the target SDK."""
    value = tomllib.loads((root / 'sdk.lock.toml').read_text())
    if value.get('format') != 1 or value.get('target') != TARGET or value.get('abi') != ABI or type(value.get('available')) is not bool:
        raise ValueError('Invalid SDK lock')
    if value['available']:
        if not value.get('version') or not https_url(value.get('url', '')) or not SHA256.fullmatch(value.get('sha256', '')) or not re.fullmatch(r'ghcr\.io/mochios/libs-builder@sha256:[0-9a-f]{64}', value.get('builder_image', '')):
            raise ValueError('SDK requires a fixed HTTPS URL, version, SHA-256 and builder image digest')
    elif not value.get('reason'):
        raise ValueError('Unavailable SDK needs a reason')
    return value


def runner_identity(root):
    """Identify the trusted runner/probe implementation embedded in the builder image."""
    return identity({str(p.relative_to(root)): hash_file(p) for p in [root / 'tools/container_build.py', *sorted((root / 'tooling/probe').glob('*'))]})


def make_plan(root, refresh=False):
    """Freeze selected upstream commits, recipes, SDK identity and transitive dependency identities."""
    catalog = read_catalog(root)
    sdk = sdk_lock(root)
    baseline = json.loads((root / 'sources.lock.json').read_text())
    if baseline.get('format') != 1:
        raise ValueError('Unknown source lock format')
    sources, packages = {}, []
    common = {str(p.relative_to(root)): hash_file(p) for p in [root / 'Dockerfile', root / 'tools/libs.py', root / 'tools/releases.py', root / 'tools/container_build.py', *sorted((root / 'tooling/probe').glob('*'))]}
    for name in order(catalog):
        library = catalog['libraries'][name]
        pinned = baseline['sources'][name]
        if not SHA.fullmatch(pinned['commit']) or not re.fullmatch(library['tag_pattern'], pinned['tag']) or version(pinned['version']) != version(re.fullmatch(library['tag_pattern'], pinned['tag'])['version']):
            raise ValueError(f'Invalid source lock for {name}')
        if refresh:
            remote = run(['git', 'ls-remote', '--tags', library['git']])
            refs = dict(line.split('\t', 1)[::-1] for line in remote.splitlines())
            pinned_ref = 'refs/tags/' + pinned['tag']
            if refs.get(pinned_ref + '^{}', refs.get(pinned_ref)) != pinned['commit']:
                raise ValueError(f'Pinned upstream tag moved or disappeared: {name}')
            selected = parse_tags(remote, library['tag_pattern'])
        else:
            selected = pinned
        if version(selected['version']) < version(pinned['version']):
            raise ValueError(f'Upstream tag removed or regressed: {name}')
        if selected['version'] == pinned['version'] and selected['commit'] != pinned['commit']:
            raise ValueError(f'Upstream tag moved: {name}; review the lock explicitly')
        sources[name] = selected
        specification = recipe(root, library)
        recipe_path = relative(root, library['recipe'])
        deps = {dep: next(p['input_id'] for p in packages if p['name'] == dep) for dep in library['dependencies']}
        inputs = dict(name=name, source=selected, library=library, sdk=sdk, dependencies=deps, common=common,
                      recipe=specification, probe_sha256=hash_file(relative(recipe_path.parent, specification['probe'])))
        stem = f'{name}-{selected["version"]}-r{library["revision"]}-{TARGET}-{ABI}'
        packages.append(dict(name=name, source=selected, release=f'{name}-{selected["version"]}', stem=stem,
                             input_id=identity(inputs), dependencies=deps))
    return dict(format=1, catalog_sha256=hash_file(root / 'libraries.toml'), sdk=sdk, sources=sources,
                packages=packages, ready=sdk['available'], blocked_reason=sdk.get('reason'))


def validate_plan(root, plan):
    """Recompute every frozen input identity; modified plans/configurations must be resolved again."""
    baseline_path = root / 'sources.lock.json'
    # Reuse the same calculation with the frozen source map, without touching tracked files.
    catalog = read_catalog(root)
    sdk = sdk_lock(root)
    if plan.get('format') != 1 or plan.get('catalog_sha256') != hash_file(root / 'libraries.toml') or plan.get('sdk') != sdk:
        raise ValueError('Plan does not match this checkout/SDK')
    expected_names = order(catalog)
    if [p['name'] for p in plan['packages']] != expected_names or set(plan['sources']) != set(expected_names):
        raise ValueError('Plan dependency order is invalid')
    # The local offline baseline must not be silently overwritten by resolution.
    baseline = json.loads(baseline_path.read_text())
    common = {str(p.relative_to(root)): hash_file(p) for p in [root / 'Dockerfile', root / 'tools/libs.py', root / 'tools/releases.py', root / 'tools/container_build.py', *sorted((root / 'tooling/probe').glob('*'))]}
    completed = {}
    for package in plan['packages']:
        name = package['name']; library = catalog['libraries'][name]; selected = plan['sources'][name]
        match = re.fullmatch(library['tag_pattern'], selected['tag'])
        if not match or match['version'] != selected['version'] or not SHA.fullmatch(selected['commit']) or version(selected['version']) < version(baseline['sources'][name]['version']):
            raise ValueError('Invalid frozen source')
        if selected['version'] == baseline['sources'][name]['version'] and selected['commit'] != baseline['sources'][name]['commit']:
            raise ValueError('Pinned tag changed')
        specification = recipe(root, library); path = relative(root, library['recipe'])
        deps = {dep: completed[dep] for dep in library['dependencies']}
        inputs = dict(name=name, source=selected, library=library, sdk=sdk, dependencies=deps, common=common,
                      recipe=specification, probe_sha256=hash_file(relative(path.parent, specification['probe'])))
        expected = dict(name=name, source=selected, release=f'{name}-{selected["version"]}',
                        stem=f'{name}-{selected["version"]}-r{library["revision"]}-{TARGET}-{ABI}', input_id=identity(inputs), dependencies=deps)
        if package != expected:
            raise ValueError(f'Tampered/stale build plan: {name}')
        completed[name] = expected['input_id']
    return catalog


def safe_members(tar):
    """Validate an SDK/package archive before extraction; data filters add a second path boundary."""
    seen = set()
    for member in tar.getmembers():
        name = PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or '\\' in member.name or name.as_posix() in seen:
            raise ValueError('Unsafe or duplicated archive member')
        seen.add(name.as_posix())
        if not (member.isfile() or member.isdir() or member.issym()):
            raise ValueError('Unsupported archive member')
        if member.issym():
            link = PurePosixPath(member.linkname)
            if link.is_absolute() or '\\' in member.linkname:
                raise ValueError('External archive link')
            parts = list(name.parent.parts)
            for part in link.parts:
                if part == '..':
                    if not parts: raise ValueError('External archive link')
                    parts.pop()
                elif part != '.': parts.append(part)
    return tar.getmembers()


def validate_sdk(directory, lock):
    """Check the SDK contract; actual shared/executable link probes still run inside each build."""
    manifest = json.loads((directory / 'sdk.json').read_text())
    if manifest.get('format') != 1 or manifest.get('version') != lock['version'] or manifest.get('target') != TARGET or manifest.get('abi') != ABI:
        raise ValueError('SDK manifest identity mismatch')
    if not {'pic', 'shared', 'cxx23', 'threads', 'exceptions'}.issubset(set(manifest.get('features', []))):
        raise Blocked('SDK does not declare the required shared C/C++ feature set')
    toolchain = relative(directory, manifest['cmake_toolchain'])
    sysroot = relative(directory, manifest['sysroot'])
    if not toolchain.is_file() or not sysroot.is_dir():
        raise Blocked('SDK toolchain/sysroot is missing')
    return manifest


def install_sdk(root, output, lock):
    """Fetch a SHA-256-pinned SDK into this repository's output tree, never into the OS workspace."""
    if not lock['available']:
        raise Blocked(lock['reason'])
    destination = output / 'sdks' / lock['sha256']
    if destination.exists():
        validate_sdk(destination, lock)
        return destination
    cache = output / 'downloads'; cache.mkdir(parents=True, exist_ok=True)
    archive = cache / lock['sha256']
    if not archive.exists():
        temporary = archive.with_suffix('.part')
        try:
            with urllib.request.build_opener(HTTPSRedirects()).open(lock['url'], timeout=60) as source, temporary.open('wb') as target:
                limit = min(4 * 1024**3, shutil.disk_usage(cache).free - 1024**3)
                total = 0
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit: raise ValueError('SDK archive exceeds its size/free-space budget')
                    target.write(chunk)
            if hash_file(temporary) != lock['sha256']: raise ValueError('SDK archive digest mismatch')
            temporary.replace(archive)
        finally:
            if temporary.exists(): temporary.unlink()
    if hash_file(archive) != lock['sha256']: raise ValueError('Cached SDK digest mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='sdk-') as temporary:
        temporary = Path(temporary); source = archive
        if lock['url'].endswith('.zst'):
            source = temporary / 'sdk.tar'
            limit = min(8 * 1024**3, shutil.disk_usage(temporary).free - 1024**3)
            with source.open('wb') as target:
                process = subprocess.Popen(['zstd', '-d', '-c', str(archive)], stdout=subprocess.PIPE)
                try:
                    total = 0
                    while chunk := process.stdout.read(1024 * 1024):
                        total += len(chunk)
                        if total > limit: raise ValueError('SDK decompression exceeds its size/free-space budget')
                        target.write(chunk)
                    if process.wait(): raise ValueError('SDK decompression failed')
                finally:
                    process.stdout.close()
                    if process.poll() is None: process.kill()
                    process.wait()
        with tarfile.open(source) as tar:
            members = safe_members(tar)
            required = sum(m.size for m in members)
            if required > 8 * 1024**3 or shutil.disk_usage(temporary).free < required + 1024**3:
                raise ValueError('Insufficient SDK extraction space or oversized archive')
            staged = temporary / 'tree'; staged.mkdir()
            tar.extractall(staged, members=members, filter='data')
        validate_sdk(staged, lock)
        staged.rename(destination)
    return destination


def checkout_source(output, library, selected):
    """Fetch exactly one pinned commit; do not initialize unpinned submodules or reuse altered trees."""
    source = output / 'sources' / selected['commit']
    if source.exists():
        if run(['git', '-C', source, 'rev-parse', 'HEAD']) != selected['commit'] or run(['git', '-C', source, 'status', '--porcelain', '--untracked-files=all']):
            raise ValueError('Source cache is modified')
        return source
    source.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=source.parent, prefix='git-') as temporary:
        staged = Path(temporary) / 'source'
        run(['git', '-c', 'core.hooksPath=/dev/null', 'init', '--initial-branch=source', staged])
        run(['git', '-C', staged, 'remote', 'add', 'origin', library['git']])
        run(['git', '-C', staged, '-c', 'core.hooksPath=/dev/null', 'fetch', '--depth=1', '--no-tags', 'origin', selected['commit']])
        run(['git', '-C', staged, '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', selected['commit']])
        if run(['git', '-C', staged, 'rev-parse', 'HEAD']) != selected['commit'] or (staged / '.gitmodules').exists():
            raise ValueError('Source needs an explicit submodule/source recipe')
        staged.rename(source)
    return source


def inventory(directory):
    """Describe regular files/internal symlinks; reject special files, external links and privileged modes."""
    files = []
    for path in sorted(directory.rglob('*')):
        relative_path = path.relative_to(directory).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(directory.resolve()): raise ValueError('External package symlink')
            files.append(dict(path=relative_path, link=os.readlink(path)))
        elif path.is_file():
            mode = path.stat().st_mode & 0o7777
            if mode & 0o7000: raise ValueError('Privileged package file mode')
            files.append(dict(path=relative_path, sha256=hash_file(path), size=path.stat().st_size, mode=mode))
        elif not path.is_dir():
            raise ValueError('Special package file')
    return files


def merge_dependency(source, destination):
    """Merge already-verified dependency prefixes without silently overwriting different files."""
    for entry in inventory(source):
        target = relative(destination, entry['path'])
        original = source / entry['path']; target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            same = (target.is_symlink() and 'link' in entry and os.readlink(target) == entry['link']) or (not target.is_symlink() and 'sha256' in entry and target.is_file() and hash_file(target) == entry['sha256'])
            if not same: raise ValueError(f'Conflicting dependency file: {entry["path"]}')
        elif 'link' in entry: target.symlink_to(entry['link'])
        else: shutil.copy2(original, target)


def docker_command(image, sdk, source, dependencies, work):
    """Mount only isolated inputs/output; the existing mochiOS workspace is never mounted."""
    for path in [sdk, source, dependencies, work]:
        if ',' in str(path): raise ValueError('Docker bind paths cannot contain commas')
    command = ['docker', 'run', '--rm', '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
               '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev,size=1g']
    for path, mount, readonly in [(sdk, '/sdk', True), (source, '/source', True), (dependencies, '/deps', True), (work, '/work', False)]:
        command += ['--mount', f'type=bind,src={path},dst={mount}' + (',readonly' if readonly else '')]
    return command + [image]


def package_build(root, package, stage, metadata, destination):
    """Create a developer library archive and exact hash inventory after successful link verification."""
    if stage.is_symlink() or not stage.is_dir(): raise ValueError('Install root must be a regular directory')
    files = inventory(stage)
    if not files: raise ValueError('Empty library install')
    for entry in files:
        path = entry['path']
        if not any(path.startswith(prefix) for prefix in ['usr/lib/', 'usr/include/', 'usr/share/']):
            raise ValueError(f'Unexpected library package output: {path}')
    library = read_catalog(root)['libraries'][package['name']]
    recipe_path = relative(root, library['recipe'])
    specification = recipe(root, library)
    recipe_identity = dict(path=library['recipe'], sha256=hash_file(recipe_path), probe_sha256=hash_file(relative(recipe_path.parent, specification['probe'])))
    manifest = dict(format=1, package=package, sdk=metadata['sdk'], builder=metadata['builder'], target=TARGET, abi=ABI,
                    source_repository=library['git'], recipe=recipe_identity, registry_commit=metadata['registry_commit'],
                    verification='compile-and-link-only', runtime_tested=False, files=files)
    target = destination / package['release']; target.mkdir(parents=True, exist_ok=True)
    archive = target / (package['stem'] + '.tar.gz'); external = target / (package['stem'] + '.json')
    if any(target.iterdir()): raise ValueError('Release output exists; choose a fresh output directory')
    with tempfile.TemporaryDirectory(dir=target, prefix='pack-') as temporary:
        temporary = Path(temporary); payload = temporary / 'payload'; payload.mkdir()
        shutil.copytree(stage, payload / 'sysroot', symlinks=True)
        write_json(payload / 'manifest.json', manifest)
        temporary_archive = temporary / archive.name
        with temporary_archive.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(payload.rglob('*')):
                    info = tar.gettarinfo(path, arcname=path.relative_to(payload).as_posix())
                    info.uid = info.gid = info.mtime = 0; info.uname = info.gname = ''
                    if info.isfile():
                        with path.open('rb') as stream: tar.addfile(info, stream)
                    else: tar.addfile(info)
        temporary_archive.replace(archive)
    write_json(external, manifest)
    (target / (package['stem'] + '.sha256')).write_text(''.join(f'{hash_file(p)}  {p.name}\n' for p in [archive, external]))
    return manifest


def build(root, plan, output, image):
    """Build in dependency order inside containers; unavailable SDK/daemon never falls back to the host."""
    catalog = validate_plan(root, plan)
    if not plan['sdk']['available']: raise Blocked(plan['sdk']['reason'])
    if not shutil.which('docker'): raise Blocked('Docker is required; host builds are deliberately unsupported')
    image = image or plan['sdk']['builder_image']
    if image != plan['sdk']['builder_image']: raise ValueError('Builder must match the SDK-pinned image digest')
    run(['docker', 'pull', image])
    labels = json.loads(run(['docker', 'image', 'inspect', image, '--format', '{{json .Config.Labels}}']))
    if not labels or labels.get('org.mochios.libs.runner-sha256') != runner_identity(root):
        raise ValueError('Builder contains a different runner/probe; publish and pin the matching image')
    builder_id = run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', builder_id): raise ValueError('Invalid builder image identity')
    sdk = install_sdk(root, output, plan['sdk']); validate_sdk(sdk, plan['sdk'])
    from releases import restore_existing, verify_bundle
    installed = {}
    for package in plan['packages']:
        name = package['name']; library = catalog['libraries'][name]
        work = output / 'builds' / package['input_id']
        release_directory = output / 'releases' / package['release']
        if (work / 'complete.json').is_file() and release_directory.is_dir():
            cached = verify_bundle(release_directory, package, plan, root=root)
            if inventory(work / 'stage') != cached['files']:
                raise ValueError('Completed build cache was modified')
            installed[name] = work / 'stage'
            print('Reusing verified local build: ' + package['release'])
            continue
        restored = restore_existing(root, package, plan, output)
        if restored is not None:
            installed[name] = restored
            print('Reusing verified release: ' + package['release'])
            continue
        ownership = output / 'builds' / (package['input_id'] + '.owner.json')
        expected_owner = dict(input_id=package['input_id'], builder_id=builder_id)
        if work.exists() and (not ownership.is_file() or json.loads(ownership.read_text()) != expected_owner):
            raise ValueError('Refusing to reuse an unowned/different build directory')
        work.mkdir(parents=True, exist_ok=True)
        write_json(ownership, expected_owner)
        if (work / 'complete.json').exists(): (work / 'complete.json').unlink()
        dependencies = output / 'dependencies' / package['input_id']; dependencies.mkdir(parents=True, exist_ok=True)
        for dependency in library['dependencies']: merge_dependency(installed[dependency], dependencies)
        source = checkout_source(output, library, package['source'])
        specification = recipe(root, library)
        write_json(work / 'recipe.json', specification)
        shutil.copy2(relative(root, library['recipe']).parent / specification['probe'], work / 'library-probe.c')
        run(docker_command(builder_id, sdk, source, dependencies, work), log=work / 'container.log')
        verified = json.loads((work / 'verified.json').read_text())
        if verified.get('format') != 1 or verified.get('target') != TARGET or verified.get('abi') != ABI:
            raise ValueError('Missing target link verification')
        stage = work / 'stage'
        if stage.is_symlink() or not stage.is_dir() or not stage.resolve().is_relative_to(work.resolve()):
            raise ValueError('Container install escaped its output directory')
        inventory(stage)
        for license_path in specification['licenses']:
            original = relative(source, license_path)
            if not original.is_file(): raise ValueError(f'Missing license notice: {license_path}')
            target = relative(stage, 'usr/share/licenses/' + name + '/' + license_path)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(original, target)
        package_build(root, package, stage, dict(sdk=plan['sdk'], registry_commit=run(['git', '-C', root, 'rev-parse', 'HEAD']), builder=dict(image=builder_id, reference=image, tools=verified['tools'])), output / 'releases')
        installed[name] = stage
        write_json(work / 'complete.json', dict(input_id=package['input_id'], verified=verified))


def main():
    """Run configuration checks, source resolution or isolated builds from the CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    scan = commands.add_parser('plan'); scan.add_argument('--refresh', action='store_true'); scan.add_argument('--output', type=Path, default=Path('out/plan.json')); scan.add_argument('--github-output', type=Path); scan.add_argument('--check-releases', action='store_true', help='Query published manifests to avoid rebuilding unchanged versions')
    compile_command = commands.add_parser('build'); compile_command.add_argument('--plan', type=Path, default=Path('out/plan.json')); compile_command.add_argument('--output', type=Path, default=Path('out')); compile_command.add_argument('--image', help='Must equal the SDK-pinned builder digest')
    args = parser.parse_args(); root = args.root.resolve()
    if sys.version_info < (3, 12): parser.error('Python 3.12+ is required')
    if args.command == 'check':
        catalog = read_catalog(root); sdk = sdk_lock(root); plan = make_plan(root); validate_plan(root, plan)
        print(f'Configuration valid: {len(order(catalog))} enabled recipes, {len(catalog["libraries"]) - len(order(catalog))} planned ports')
        print('SDK: ready' if sdk['available'] else 'SDK BLOCKED: ' + sdk['reason'])
    elif args.command == 'plan':
        plan = make_plan(root, args.refresh)
        plan['needs_build'] = plan['ready']
        if args.check_releases and plan['ready']:
            from releases import needs_build
            plan['needs_build'] = needs_build(plan)
        write_json(args.output.resolve(), plan)
        if args.github_output:
            with args.github_output.open('a') as output: output.write(f'sdk_ready={str(plan["ready"]).lower()}\nneeds_build={str(plan["needs_build"]).lower()}\n')
        for package in plan['packages']: print(f'{package["release"]}: {package["source"]["commit"]}')
        if not plan['ready']: print('BUILD BLOCKED: ' + plan['blocked_reason'])
    else:
        build(root, json.loads(args.plan.read_text()), args.output.resolve(), args.image)


if __name__ == '__main__':
    try: main()
    except Blocked as error:
        print(f'Build unavailable: {error}', file=sys.stderr); raise SystemExit(3)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print(f'libs failed: {error}', file=sys.stderr); raise SystemExit(1)
