#!/usr/bin/env python3
"""Build an authenticated GSubprocess fixture with an explicitly selected SDK candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess


def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os-root', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--capability-binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, sdk, prefix, work = (p.resolve() for p in [args.os_root, args.sdk, args.prefix, args.output])
    if work.exists(): parser.error('Choose a fresh output directory')
    if 'DEVELOPMENT_SYSTEM_SIGNATURES=y' not in (root / '.config').read_text().splitlines():
        parser.error('An existing development configuration is required')
    metadata = json.loads((sdk / 'sdk.json').read_text())
    if metadata['target'] != 'x86_64-unknown-mochios' or metadata['abi'] != 'mochios-1':
        parser.error('Wrong SDK target or ABI')
    candidate_file = sdk / 'candidate.json'
    candidate = json.loads(candidate_file.read_text()) if candidate_file.exists() else None
    runtime_digest = digest(sdk / 'lib/libmochi_user_newlib_runtime.a')
    if candidate is not None and candidate['runtime_archive_sha256'] != runtime_digest:
        parser.error('SDK candidate runtime changed since its provenance was recorded')
    source = Path(__file__).resolve().parent
    work.mkdir(parents=True)
    stage = work / 'stage'
    bundle = stage / 'system/applications/SubprocessTest.app'
    bundle.mkdir(parents=True)
    target = stage / 'system/bin/gsubprocess-smoke'
    target.parent.mkdir(parents=True)
    env = dict(os.environ)
    env.update(PKG_CONFIG_SYSROOT_DIR=str(prefix), PKG_CONFIG_LIBDIR=str(prefix / 'usr/lib/pkgconfig'), PKG_CONFIG_PATH='')
    glib_version = subprocess.check_output(['pkg-config', '--modversion', 'gio-2.0'], env=env, text=True).strip()
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'gio-2.0'], env=env, text=True))
    cc = sdk / 'bin/mochios-cc'
    subprocess.run([cc, source / 'client.c', '-O2', '-g0', '-o', target,
                    '-L' + str(prefix / 'usr/lib'), *flags], env=env, check=True)
    subprocess.run([cc, source / 'launcher.c', '-O2', '-g0', '-o', bundle / 'entry.elf'], env=env, check=True)
    capabilities = ['process.spawn', 'fs.read.all', 'fs.write.tmp', 'ipc.client', 'ipc.server',
                    'system.random.read', 'system.time.read']
    manifest = '''format = 1
[package]
provenance = "built-in"
id = "org.mochios.sdk.gsubprocess-test"
name = "GSubprocess Test"
version = "0.1.0"
vendor = "mochiOS development fixture"
kind = "application"
architecture = "x86_64"
abi = "mochios-1"
[application]
entry = "entry.elf"
'''
    for path in ['/system/applications/SubprocessTest.app/entry.elf', '/system/bin/gsubprocess-smoke']:
        (stage / path[1:]).chmod(0o755)
        manifest += '\n[[binary]]\npath = ' + json.dumps(path) + '\nkind = "application"\nrequires = ' + json.dumps(capabilities) + '\n'
    queue, seen = [target, bundle / 'entry.elf'], set()
    while queue:
        binary = queue.pop()
        dynamic = subprocess.check_output([sdk / 'host/bin/readelf', '-d', binary], text=True)
        for name in re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic):
            if name in seen: continue
            if '/' in name or '\\' in name or len(seen) >= 64: raise ValueError('Invalid dependency closure')
            library = (prefix / 'usr/lib' / name).resolve()
            if not library.is_relative_to(prefix): raise ValueError('Dependency escaped prefix')
            seen.add(name)
            relative = '/system/libraries/gsubprocess-smoke/' + name
            destination = stage / relative[1:]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(library, destination)
            destination.chmod(0o644)
            queue.append(destination)
            manifest += '\n[[binary]]\npath = ' + json.dumps(relative) + '\nkind = "library"\n'
    source_manifest = work / 'manifest.toml'
    source_manifest.write_text(manifest)
    (bundle / 'manifest.toml').write_text(manifest)
    service = stage / 'system/services/capability.service'
    service.parent.mkdir(parents=True)
    shutil.copyfile(args.capability_binary, service)
    service.chmod(0o755)
    msign = root / 'out/mmake/host-tools/release/msign'
    for name, manifest_path in [('gsubprocess-test', source_manifest), ('capability', root / 'services/capability/manifest.toml')]:
        package = stage / 'system/packages' / name
        package.mkdir(parents=True)
        subprocess.run([msign, 'package', 'bind-built-in', manifest_path, '--rootfs', stage,
                        '--output', package / 'manifest.toml'], check=True)
        subprocess.run([msign, 'package', 'built-in-record', package / 'manifest.toml',
                        '--output', package / 'verification.bin'], check=True)
    startup = stage / 'system/setup/session-startup.json'
    startup.parent.mkdir(parents=True)
    startup.write_text(json.dumps({'format': 1, 'rules': [{
        'handler': 'org.mochios.sdk.gsubprocess-test',
        'requirements': '/system/applications/SubprocessTest.app/setup.json'}]}) + '\n')
    (bundle / 'setup.json').write_text(json.dumps({'format': 1, 'applications': [
        {'bundle_id': 'org.mochios.sdk.test-missing'}]}) + '\n')
    report = {'format': 1, 'sdk_status': 'explicit development candidate; not a published SDK verification',
              'base_sdk_version': metadata['version'], 'runtime_archive_sha256': runtime_digest,
              'candidate': candidate, 'glib_version': glib_version,
              'libraries': sorted(seen), 'runtime_tested': False,
              'sources': {p.name: digest(p) for p in [source / 'client.c', source / 'launcher.c']},
              'inputs': {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
    (work / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print('GSubprocess development fixture staged:', stage)


if __name__ == '__main__':
    main()
