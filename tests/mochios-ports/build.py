#!/usr/bin/env python3
"""Build an authenticated C library probe fixture with an explicitly selected SDK candidate."""
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


def runtime_library_names(names):
    """Validate explicit dlopen dependencies; file paths and traversal are forbidden."""
    for name in names:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+.-]*\.so(?:\.[0-9]+)*',name):
            raise ValueError('Invalid runtime library name')
    return list(dict.fromkeys(names))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',help='Recipe whose target modules are linked')
    parser.add_argument('--probe',type=Path,help='Explicit C probe; optionally inherit the selected recipe modules')
    parser.add_argument('--probe-entry',default='main',help='Zero-argument int probe function (default: main)')
    parser.add_argument('--os-root', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--capability-binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-library', action='append', default=[], help='Explicit additional SONAME loaded through dlopen')
    args = parser.parse_args()
    if args.library is None and args.probe is None: parser.error('Select a library recipe or an explicit C probe')
    runtime_libraries = runtime_library_names(args.runtime_library)
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
    repository=Path(__file__).resolve().parents[2]
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',args.probe_entry):
        parser.error('Invalid C probe entry point')
    if args.library is not None:
        if not re.fullmatch(r'[a-z][a-z0-9-]*',args.library): parser.error('Invalid library name')
        if args.probe_entry!='main': parser.error('Recipe probes must use main')
        recipe_path=repository/'recipes'/args.library/'recipe.json'
        recipe=json.loads(recipe_path.read_text())
        probe=args.probe.resolve() if args.probe else (recipe_path.parent/recipe['probe']).resolve()
        if args.probe is None and not probe.is_relative_to(recipe_path.parent.resolve()):
            parser.error('Recipe probe escapes its assets')
        modules=recipe['pkg_config_modules']
    else:
        probe=args.probe.resolve()
        modules=[]
    if not probe.is_file() or probe.suffix!='.c':
        parser.error('Native runner accepts only existing C probes')
    if not re.search(r'\bint\s+'+re.escape(args.probe_entry)+r'\s*\(\s*void\s*\)',probe.read_text()):
        parser.error('Native probe must define its zero-argument int entry point')
    source = Path(__file__).resolve().parent
    work.mkdir(parents=True)
    stage = work / 'stage'
    bundle = stage / 'system/applications/PortSmoke.app'
    bundle.mkdir(parents=True)
    target = stage / 'system/bin/port-smoke'
    target.parent.mkdir(parents=True)
    env = dict(os.environ)
    env.update(PKG_CONFIG_SYSROOT_DIR=str(prefix), PKG_CONFIG_LIBDIR=str(prefix / 'usr/lib/pkgconfig'), PKG_CONFIG_PATH='')
    versions={m: subprocess.check_output(['pkg-config','--modversion',m],env=env,text=True).strip() for m in modules}
    cflags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', *modules], env=env, text=True)) if modules else []
    flags = cflags + (shlex.split(subprocess.check_output(['pkg-config', '--libs', *modules], env=env, text=True)) if modules else [])
    cc = sdk / 'bin/mochios-cc'
    probe_object=work/'probe.o'
    subprocess.run([cc,probe,'-D'+args.probe_entry+'=port_probe_main','-O2','-g0','-c','-o',probe_object,*cflags],env=env,check=True)
    subprocess.run([cc,source/'wrapper.c',probe_object,'-O2','-g0','-o',target,
                    '-L'+str(prefix/'usr/lib'),*flags],env=env,check=True)
    subprocess.run([cc, source / 'launcher.c', '-O2', '-g0', '-o', bundle / 'entry.elf'], env=env, check=True)
    capabilities = ['process.spawn', 'fs.read.all', 'fs.write.tmp', 'ipc.client', 'ipc.server',
                    'system.random.read', 'system.time.read']
    manifest = '''format = 1
[package]
provenance = "built-in"
id = "org.mochios.sdk.port-smoke"
name = "Library Port Smoke"
version = "0.1.0"
vendor = "mochiOS development fixture"
kind = "application"
architecture = "x86_64"
abi = "mochios-1"
[application]
entry = "entry.elf"
'''
    for path in ['/system/applications/PortSmoke.app/entry.elf', '/system/bin/port-smoke']:
        (stage / path[1:]).chmod(0o755)
        manifest += '\n[[binary]]\npath = ' + json.dumps(path) + '\nkind = "application"\nrequires = ' + json.dumps(capabilities) + '\n'
    queue, seen = [target, bundle / 'entry.elf'], set()
    pending = list(runtime_libraries)
    while queue or pending:
        if queue:
            binary = queue.pop()
            dynamic = subprocess.check_output([sdk / 'host/bin/readelf', '-d', binary], text=True)
            pending.extend(re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic))
            continue
        name = pending.pop()
        if name in seen: continue
        if '/' in name or '\\' in name or len(seen) >= 64: raise ValueError('Invalid dependency closure')
        library = (prefix / 'usr/lib' / name).resolve()
        if not library.is_relative_to(prefix): raise ValueError('Dependency escaped prefix')
        seen.add(name)
        relative = '/system/libraries/port-smoke/' + name
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
    for name, manifest_path in [('port-smoke', source_manifest), ('capability', root / 'services/capability/manifest.toml')]:
        package = stage / 'system/packages' / name
        package.mkdir(parents=True)
        subprocess.run([msign, 'package', 'bind-built-in', manifest_path, '--rootfs', stage,
                        '--output', package / 'manifest.toml'], check=True)
        subprocess.run([msign, 'package', 'built-in-record', package / 'manifest.toml',
                        '--output', package / 'verification.bin'], check=True)
    startup = stage / 'system/setup/session-startup.json'
    startup.parent.mkdir(parents=True)
    startup.write_text(json.dumps({'format': 1, 'rules': [{
        'handler': 'org.mochios.sdk.port-smoke',
        'requirements': '/system/applications/PortSmoke.app/setup.json'}]}) + '\n')
    (bundle / 'setup.json').write_text(json.dumps({'format': 1, 'applications': [
        {'bundle_id': 'org.mochios.sdk.test-missing'}]}) + '\n')
    report = {'format': 1, 'sdk_status': 'explicit development candidate; not a published SDK verification',
              'base_sdk_version': metadata['version'], 'runtime_archive_sha256': runtime_digest,
              'candidate': candidate, 'library': args.library, 'probe_entry':args.probe_entry, 'module_versions': versions,
              'libraries': sorted(seen), 'explicit_runtime_libraries': runtime_libraries, 'runtime_tested': False,
              'sources': {p.name: digest(p) for p in [probe, source/'wrapper.c', source/'launcher.c']},
              'inputs': {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
    (work / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Native library fixture staged:', stage)


if __name__ == '__main__':
    main()
