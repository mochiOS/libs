#!/usr/bin/env python3
"""Container-only CMake runner. It never executes target programs or mounts the OS workspace."""
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys


def command(args, env=None):
    """Execute a build command with its exact argv and propagate any failure."""
    print('+ ' + ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), env=env, check=True)


def validate_elf(path, shared=False):
    """Reject host/glibc artifacts, the wrong architecture, executable helpers and build RPATHs."""
    with path.open('rb') as stream: header = stream.read(64)
    if len(header) < 64 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62:
        raise ValueError(f'Not an x86-64 ELF: {path}')
    if shared and struct.unpack_from('<H', header, 16)[0] != 3:
        raise ValueError(f'Not a shared library: {path}')
    program = subprocess.check_output(['readelf', '-l', str(path)], text=True)
    versions = subprocess.check_output(['readelf', '--version-info', str(path)], text=True)
    dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
    if shared and 'INTERP' in program:
        raise ValueError('Library output has an executable interpreter')
    if 'GLIBC_' in versions or any(name in dynamic for name in ['libc.so.6', 'ld-linux-x86-64', 'libpthread.so.0']):
        raise ValueError('Host glibc dependency is forbidden')
    if '(RPATH)' in dynamic or '(RUNPATH)' in dynamic:
        raise ValueError('Library output contains an unreviewed runtime search path')
    return dynamic


def safe_sdk_path(sdk, relative):
    """Keep toolchain/sysroot inputs within the read-only SDK mount."""
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Invalid SDK relative path')
    path = sdk / relative
    if not path.resolve().is_relative_to(sdk.resolve()):
        raise ValueError('SDK path escapes its mount')
    return path


def main():
    """Probe the SDK, build/install a recipe, then verify target ELF and consumer links."""
    sdk, source, work, dependencies = map(Path, ['/sdk', '/source', '/work', '/deps'])
    manifest = json.loads((sdk / 'sdk.json').read_text())
    if manifest.get('target') != 'x86_64-unknown-mochios' or manifest.get('abi') != 'mochios-1':
        raise ValueError('Wrong SDK target')
    toolchain = safe_sdk_path(sdk, manifest['cmake_toolchain'])
    sysroot = safe_sdk_path(sdk, manifest['sysroot'])
    recipe = json.loads((work / 'recipe.json').read_text())
    env = os.environ.copy()
    env.update(PKG_CONFIG_PATH='', PKG_CONFIG_SYSROOT_DIR=str(dependencies),
               PKG_CONFIG_LIBDIR='/deps/usr/lib/pkgconfig:/deps/usr/share/pkgconfig',
               CARGO_NET_OFFLINE='true', HOME='/tmp', SOURCE_DATE_EPOCH='0')
    common = ['-G', 'Ninja', f'-DCMAKE_TOOLCHAIN_FILE={toolchain}',
              f'-DCMAKE_FIND_ROOT_PATH={dependencies};{sysroot}',
              '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_SKIP_RPATH=ON',
              '-DFETCHCONTENT_FULLY_DISCONNECTED=ON', '-DFETCHCONTENT_UPDATES_DISCONNECTED=ON']
    command(['cmake', '-S', '/opt/libs/probe', '-B', work / 'sdk-probe', *common], env)
    command(['cmake', '--build', work / 'sdk-probe', '--parallel', '2'], env)
    validate_elf(work / 'sdk-probe/libsdk_probe_shared.so', shared=True)
    validate_elf(work / 'sdk-probe/sdk_probe')
    # Some upstream generators modify their source tree; only the disposable copy is writable.
    disposable = work / 'source'
    if disposable.is_symlink(): raise ValueError('Invalid writable source cache')
    if not disposable.exists():
        shutil.copytree(source, disposable, symlinks=True, ignore=shutil.ignore_patterns('.git'))
    options = []
    for name, value in recipe['options'].items():
        if isinstance(value, bool): value = 'ON' if value else 'OFF'
        options.append(f'-D{name}={value}')
    command(['cmake', '-S', disposable, '-B', work / 'build', *common,
             '-DCMAKE_INSTALL_PREFIX=/usr', '-DCMAKE_INSTALL_LIBDIR=lib',
             '-DCMAKE_POSITION_INDEPENDENT_CODE=ON', '-DBUILD_SHARED_LIBS=ON', *options], env)
    command(['cmake', '--build', work / 'build', '--parallel', '2'], env)
    stage = work / 'stage'
    if stage.is_symlink(): stage.unlink()
    elif stage.exists(): shutil.rmtree(stage)
    command(['cmake', '--install', work / 'build'], dict(env, DESTDIR=str(stage)))
    for pattern in recipe['shared_libraries']:
        matches = list((work / 'stage/usr/lib').glob(pattern))
        if not matches: raise ValueError(f'Required shared output missing: {pattern}')
        for path in matches:
            if not path.resolve().is_relative_to((work / 'stage').resolve()):
                raise ValueError('External installed library symlink')
            validate_elf(path, shared=True)
    # Link a library-specific consumer as well as the shared C++ SDK probe. Never run it.
    command(['cmake', '-S', '/opt/libs/probe', '-B', work / 'library-probe', *common,
             '-DLIBRARY_PROBE=/work/library-probe.c',
             '-DLIBRARY_LINKS=' + ';'.join(recipe['link_libraries'])], env)
    command(['cmake', '--build', work / 'library-probe', '--parallel', '2'], env)
    validate_elf(work / 'library-probe/library_probe')
    # Validate every ELF installed by the recipe, not just the declared primary library.
    for path in (work / 'stage').rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream: magic = stream.read(4)
            if magic == b'\x7fELF': validate_elf(path, shared=True)
    tools = dict(cmake=subprocess.check_output(['cmake', '--version'], text=True).splitlines()[0],
                 ninja=subprocess.check_output(['ninja', '--version'], text=True).strip(),
                 host_packages=Path('/usr/share/libs-builder-packages.txt').read_text())
    (work / 'verified.json').write_text(json.dumps(dict(format=1, target=manifest['target'], abi=manifest['abi'],
        runtime_tested=False, tools=tools), indent=2) + '\n')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Container build failed: {error}', file=sys.stderr)
        raise SystemExit(1)
