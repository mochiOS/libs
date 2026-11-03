"""Cross-build backends. Target programs are never used as configure/build helpers."""
import json
from pathlib import Path
import shutil
import subprocess


def invoke(argv, env, cwd=None):
    """Run fixed argv without a shell and propagate the original failing command."""
    print('+ ' + ' '.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), env=env, cwd=cwd, check=True)


def cross_file(sdk, sysroot, dependencies, destination):
    """Describe the real mochiOS ABI, isolating pkg-config and forbidding target execution."""
    value = lambda item: json.dumps(str(item))
    text = '[binaries]\n'
    for name, path in [('c', sdk/'bin/mochios-cc'), ('cpp', sdk/'bin/mochios-c++'),
                       ('ar', sdk/'host/bin/ar'), ('strip', sdk/'host/bin/strip'),
                       ('pkg-config', Path('/usr/bin/pkg-config'))]:
        text += f'{name} = {value(path)}\n'
    text += "[host_machine]\nsystem = 'mochios'\ncpu_family = 'x86_64'\ncpu = 'x86_64'\nendian = 'little'\n"
    text += '[properties]\nneeds_exe_wrapper = true\nsys_root = ' + value(sysroot) + '\n'
    text += 'pkg_config_libdir = [' + ', '.join(value(dependencies/p) for p in ['usr/lib/pkgconfig', 'usr/share/pkgconfig']) + ']\n'
    text += "[built-in options]\nc_args = ['-ffile-prefix-map=/work=/build', '-ffile-prefix-map=/source=/src']\n"
    text += "cpp_args = ['-ffile-prefix-map=/work=/build', '-ffile-prefix-map=/source=/src']\n"
    destination.write_text(text)
    return destination


def build(recipe, source, work, sdk, sysroot, dependencies, common, env):
    """Build into one private prefix; SDK import checks and caller ELF/link probes remain mandatory."""
    backend = recipe['backend']
    build = work/'build'
    stage = work/'stage'
    if backend == 'autotools' and recipe.get('bootstrap') == 'autoreconf':
        invoke(['autoreconf', '--install'], env, cwd=source)
    for patch in recipe.get('patches', []):
        invoke(['patch', '--batch', '--fuzz=0', '-p1', '-i', work/'recipe-assets'/patch], env, cwd=source)
    source = source/recipe.get('subdirectory', '.')
    if backend == 'cmake':
        options = [f'-D{name}=' + (('ON' if value else 'OFF') if isinstance(value, bool) else str(value))
                   for name, value in recipe['options'].items()]
        invoke(['cmake', '-S', source, '-B', build, *common, '-DCMAKE_INSTALL_PREFIX=/usr',
                '-DCMAKE_INSTALL_LIBDIR=lib', '-DCMAKE_POSITION_INDEPENDENT_CODE=ON', '-DBUILD_SHARED_LIBS=ON', *options], env)
        invoke(['cmake', '--build', build, '--parallel', '2'], env)
    elif backend == 'meson':
        cross = cross_file(sdk, sysroot, dependencies, work/'meson-cross.ini')
        options = [f'-D{name}=' + (str(value).lower() if isinstance(value, bool) else str(value)) for name, value in recipe['options'].items()]
        invoke(['meson', 'setup', build, source, '--cross-file', cross, '--wrap-mode=nodownload',
                '--prefix=/usr', '--libdir=lib', '--buildtype=release', '--default-library=shared', '-Db_lundef=false', *options], env)
        invoke(['meson', 'compile', '-C', build, '-j', '2'], env)
    elif backend == 'autotools':
        build.mkdir(exist_ok=True)
        env = dict(env, **{name: str(sdk/path) for name, path in {
            'CC':'bin/mochios-cc', 'CXX':'bin/mochios-c++', 'AR':'host/bin/ar', 'RANLIB':'host/bin/ranlib',
            'NM':'host/bin/nm', 'STRIP':'host/bin/strip', 'READELF':'host/bin/readelf', 'LD':'host/bin/ld.lld'}.items()})
        env.update(CFLAGS='-O2 -fPIC -ffile-prefix-map=/work=/build -ffile-prefix-map=/source=/src',
                   CXXFLAGS='-O2 -fPIC -ffile-prefix-map=/work=/build -ffile-prefix-map=/source=/src',
                   CPPFLAGS='-I'+str(dependencies/'usr/include'), LDFLAGS='-L'+str(dependencies/'usr/lib'))
        invoke([source/'configure', '--host=x86_64-unknown-mochios', '--build=x86_64-pc-linux-gnu',
                '--prefix=/usr', '--libdir=/usr/lib', '--enable-shared', '--disable-static', *recipe['configure']], env, cwd=build)
        targets = recipe.get('build_targets', [])
        subdirs = recipe.get('build_subdirs', [])
        if targets or not subdirs: invoke(['make', '-j2', *targets], env, cwd=build)
        for directory in subdirs: invoke(['make', '-C', directory, '-j2'], env, cwd=build)
    else:
        raise ValueError('Unsupported build backend')
    if stage.is_symlink(): raise ValueError('Invalid install root')
    if stage.exists(): shutil.rmtree(stage)
    install_env = dict(env, DESTDIR=str(stage))
    if backend == 'cmake': invoke(['cmake', '--install', build], install_env)
    elif backend == 'meson':
        invoke(['meson', 'install', '-C', build, '--no-rebuild', '--tags', ','.join(recipe['install_tags'])], install_env)
    else:
        for directory in recipe.get('install_subdirs', ['.']):
            invoke(['make', '-C', directory, 'install'], install_env, cwd=build)
        for original, target in recipe.get('install_headers', {}).items():
            path = stage/target; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build/original, path)
