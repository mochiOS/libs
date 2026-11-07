#!/usr/bin/env python3
"""Stage a normal built-in application for an isolated development test image."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import libs
import container_build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os-root', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--untrusted-port', type=int, default=32895)
    parser.add_argument('--stalled-port', type=int, default=45329)
    args = parser.parse_args()
    os_root, prefix, work = args.os_root.resolve(), args.prefix.resolve(), args.output.resolve()
    if work.exists():
        parser.error('Choose a fresh output directory')
    if not all(1 <= p <= 65535 for p in [args.untrusted_port, args.stalled_port]):
        parser.error('Ports must be in 1..65535')
    if 'DEVELOPMENT_SYSTEM_SIGNATURES=y' not in (os_root / '.config').read_text().splitlines():
        parser.error('Use an existing development image; production trust is never modified')
    lock = libs.sdk_lock(ROOT)
    sdk = ROOT / 'out/sdks' / lock['sha256']
    libs.validate_sdk(sdk, lock)
    work.mkdir(parents=True)
    stage = work / 'stage'
    bundle = stage / 'system/applications/NativeTlsTest.app'
    bundle.mkdir(parents=True)
    target = stage / 'system/bin/sdk-tls-smoke'
    target.parent.mkdir(parents=True)
    env = libs.clean_environment()
    env.update(PKG_CONFIG_SYSROOT_DIR=str(prefix),
               PKG_CONFIG_LIBDIR=str(prefix / 'usr/lib/pkgconfig'), PKG_CONFIG_PATH='')
    flags = shlex.split(subprocess.check_output(
        ['pkg-config', '--cflags', '--libs', 'libsoup-3.0'], env=env, text=True))
    subprocess.run([sdk / 'bin/mochios-cc', ROOT / 'tests/mochios-tls/suite.c', '-O2',
                    f'-DNATIVE_TLS_UNTRUSTED_PORT={args.untrusted_port}',
                    f'-DNATIVE_TLS_STALLED_PORT={args.stalled_port}',
                    '-o', target, '-L' + str(prefix / 'usr/lib'), *flags], env=env, check=True)
    target.chmod(0o755)
    container_build.validate_elf(target)

    # AppCore/Binder's normal scoped launch protocol authorizes the exact DSO graph.
    # This is an ordinary application, not a service or a privilege grant.
    launcher = work / 'launcher'
    (launcher / 'src').mkdir(parents=True)
    shutil.copyfile(ROOT / 'tests/mochios-tls/launcher.rs', launcher / 'src/main.rs')
    dependencies = '\n'.join([
        'mochi-user-platform = { path = ' + json.dumps(str(os_root / 'user/crates/platform')) +
        ', default-features = false, features = ["std"] }',
        'mochios-posix-client = { path = ' + json.dumps(str(os_root / 'user/crates/posix-client')) +
        ', default-features = false, features = ["syscall-endpoint"] }',
        'mochios-posix-protocol = { path = ' + json.dumps(str(os_root / 'user/crates/posix-protocol')) + ' }'])
    (launcher / 'Cargo.toml').write_text(
        '[package]\nname = "native-tls-scoped-launcher"\nversion = "0.1.0"\nedition = "2024"\n'
        '[dependencies]\n' + dependencies + '\n[profile.release]\npanic = "abort"\n')
    # Preserve shell parameter expansions when using the mmake toolchain from GNU make.
    toolchain = work / 'toolchain.make'
    toolchain.write_text((os_root / 'build/mmake/toolchain.mk').read_text().replace('${', '$${'))
    makefile = work / 'build.make'
    makefile.write_text(f'ROOT := {os_root}\nOUT := $(ROOT)/out\nJOBS := 2\ninclude {toolchain}\n'
                        f'all:\n\t@$(MOCHIOS_CARGO) --manifest-path {launcher / "Cargo.toml"}\n')
    subprocess.run(['make', '-f', makefile], env=os.environ, check=True)
    shutil.copyfile(os_root / 'out/rust-std/target/x86_64-unknown-mochios/release/native-tls-scoped-launcher',
                    bundle / 'entry.elf')
    (bundle / 'entry.elf').chmod(0o755)
    capabilities = ['process.spawn', 'fs.read.all', 'fs.write.tmp', 'ipc.client', 'ipc.server',
                    'net.connect', 'system.random.read', 'system.time.read']
    manifest = '''format = 1
[package]
provenance = "built-in"
id = "org.mochios.sdk.native-tls-test"
name = "Native TLS Test"
version = "0.1.0"
vendor = "mochiOS development test fixture"
kind = "application"
architecture = "x86_64"
abi = "mochios-1"
[application]
entry = "entry.elf"
description = "Development-only native TLS validation"
resources = ["setup.json"]
'''
    for path in ['/system/applications/NativeTlsTest.app/entry.elf', '/system/bin/sdk-tls-smoke']:
        manifest += '\n[[binary]]\npath = ' + json.dumps(path) + '\nkind = "application"\nrequires = ' + json.dumps(capabilities) + '\n'
    queue, seen = [target, bundle / 'entry.elf'], set()
    while queue:
        path = queue.pop()
        dynamic = subprocess.check_output([sdk / 'host/bin/readelf', '-d', path], text=True)
        for name in re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic):
            if name in seen:
                continue
            if '/' in name or '\\' in name or len(seen) >= 64:
                raise ValueError('Invalid dependency closure')
            source = prefix / 'usr/lib' / name
            if not source.resolve().is_relative_to(prefix):
                raise ValueError('Dependency escaped supplied prefix')
            container_build.validate_elf(source, shared=True)
            seen.add(name)
            relative = '/system/libraries/sdk-tls-smoke/' + name
            destination = stage / relative[1:]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o644)
            queue.append(destination)
            manifest += '\n[[binary]]\npath = ' + json.dumps(relative) + '\nkind = "library"\n'
    (bundle / 'manifest.toml').write_text(manifest)
    source_manifest = work / 'manifest.toml'
    source_manifest.write_text(manifest)
    package = stage / 'system/packages/native-tls-test'
    package.mkdir(parents=True)
    msign = os_root / 'out/mmake/host-tools/release/msign'
    subprocess.run([msign, 'package', 'bind-built-in', source_manifest, '--rootfs', stage,
                    '--output', package / 'manifest.toml'], check=True)
    subprocess.run([msign, 'package', 'built-in-record', package / 'manifest.toml',
                    '--output', package / 'verification.bin'], check=True)
    policy = stage / 'system/setup/session-startup.json'
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(json.dumps({'format': 1, 'rules': [{
        'handler': 'org.mochios.sdk.native-tls-test',
        'requirements': '/system/applications/NativeTlsTest.app/setup.json'}]}))
    (bundle / 'setup.json').write_text(json.dumps({'format': 1, 'applications': [{
        'bundle_id': 'org.mochios.sdk.test-missing', 'name': 'Test fixture'}]}))
    (work / 'build.json').write_text(json.dumps({
        'sdk': lock['version'], 'sdk_sha256': lock['sha256'], 'libraries': sorted(seen),
        'untrusted_port': args.untrusted_port, 'stalled_port': args.stalled_port,
        'runtime_tested': False}, indent=2) + '\n')
    print('Native test application staged:', stage)


if __name__ == '__main__':
    main()
