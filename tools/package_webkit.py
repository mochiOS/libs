"""Stage a native engine component for the immutable System image; never invent a signing identity.

Input is an assembled, verified mochiOS target prefix. This tool checks the ELF
closure and file bindings again, but is not a substitute for artifact provenance
verification or a native execution test. It creates no signatures or releases.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

LIBRARY_ROOT = '/system/libraries/webkit'
PUBLIC_LIBRARY = '/system/libraries/libwebkit.so.1'
HELPERS = {'WPEWebProcess': 'web-process', 'WPENetworkProcess': 'network-process'}
FONT_FILES = ('InterVariable.ttf', 'IBMPlexSansJP-Regular.ttf', 'IBMPlexSansJP-Medium.ttf',
              'IBMPlexSansJP-SemiBold.ttf', 'UDEVGothic-Regular.ttf', 'UDEVGothic-Bold.ttf')
FONT_NOTICES = ('Inter.txt', 'IBMPlexSansJP.txt', 'UDEVGothic.txt')
PORT = json.loads((Path(__file__).resolve().parents[1] / 'engines/webkit/port.json').read_text())

def elf(path):
    """Read target dynamic metadata without loading or executing target code."""
    with path.open('rb') as stream: header = stream.read(64)
    if len(header) != 64 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62:
        raise ValueError('Not an x86-64 ELF: ' + str(path))
    kind = struct.unpack_from('<H', header, 16)[0]
    if kind not in (2, 3): raise ValueError('Not an executable or shared ELF')
    dynamic = subprocess.run(['readelf', '-d', str(path)], check=True, text=True, capture_output=True).stdout
    needed = re.findall(r'\(NEEDED\).*\[([^\]]+)\]', dynamic)
    sonames = re.findall(r'\(SONAME\).*\[([^\]]+)\]', dynamic)
    if len(sonames) > 1 or any(not re.fullmatch(r'[A-Za-z0-9_.+-]+', n) for n in needed + sonames):
        raise ValueError('Invalid dynamic library name')
    return kind, sonames[0] if sonames else None, needed


def validate_process_hook(selected):
    """Reject pre-hook artifacts even when their SONAME and version match."""
    required = {'wpe_process_manager_get_type', 'wpe_process_manager_set_default'}
    def symbols(path, flag):
        output = subprocess.run(['nm', '-D', flag, str(path)], check=True,
                                text=True, capture_output=True).stdout
        return {line.split()[-1].split('@', 1)[0] for line in output.splitlines() if line.split()}
    if not required.issubset(symbols(selected['libwebkit.so.1'], '--undefined-only')):
        raise ValueError('Engine predates host-owned process integration; rebuild WebKit')
    providers = set()
    for name, path in selected.items():
        if name.startswith('libWPEWebKit-'):
            providers.update(symbols(path, '--defined-only'))
    if not required.issubset(providers):
        raise ValueError('Missing native WPE process manager exports; rebuild wpewebkit')


def stage(prefix, output, version, package='org.mochios.webkit', *, fonts=None, font_licenses=None):
    """Bind the complete code closure to one independently signed binary package."""
    prefix = prefix.resolve(strict=True)
    if output.exists(): raise ValueError('Output must not exist')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', package) or not version: raise ValueError('Invalid package identity/version')
    if output.resolve().is_relative_to(prefix): raise ValueError('Output must be outside the target prefix')
    inventory = {}; helpers = {}
    for path in sorted(prefix.rglob('*')):
        if path.is_symlink():
            if not path.resolve().is_relative_to(prefix): raise ValueError('External prefix symlink')
            continue
        if not path.is_file(): continue
        with path.open('rb') as stream: magic = stream.read(4)
        if magic != b'\x7fELF': continue
        kind, soname, needed = elf(path)
        if path.name == 'WPEWebProcess' and path.relative_to(prefix).as_posix() != 'usr/libexec/mochios-webkit/WPEWebProcess':
            continue  # The upstream entry lacks the native resource bootstrap.
        if path.name in HELPERS:
            if path.name in helpers: raise ValueError('Ambiguous helper')
            helpers[path.name] = (path, needed)
        elif soname:
            if kind != 3 or soname in inventory: raise ValueError('Ambiguous/non-shared SONAME')
            inventory[soname] = (path, needed)
    if set(helpers) != set(HELPERS): raise ValueError('Missing WebKit helper executable')
    selected = {}; pending = ['libwebkit.so.1']
    for _, needed in helpers.values(): pending.extend(needed)
    while pending:
        name = pending.pop()
        if name in selected: continue
        if name not in inventory: raise ValueError('Unresolved native library: ' + name)
        selected[name] = inventory[name][0]
        pending.extend(inventory[name][1])
    validate_process_hook(selected)
    licenses = [p for p in (prefix / 'usr/share/licenses').rglob('*') if p.is_file() and not p.is_symlink()]
    if not licenses: raise ValueError('Missing upstream license inventory')
    if fonts is None or font_licenses is None:
        raise ValueError('Provide reviewed font data and original font license notices')
    fonts = fonts.resolve(strict=True); font_licenses = font_licenses.resolve(strict=True)
    for name in FONT_FILES:
        path = fonts / name
        if path.is_symlink() or not path.is_file() or not 12 <= path.stat().st_size <= 128 * 1024 * 1024:
            raise ValueError('Missing/unsafe font data: ' + name)
        with path.open('rb') as stream: magic = stream.read(4)
        if magic not in (b'\x00\x01\x00\x00', b'OTTO', b'ttcf'):
            raise ValueError('Font input is not SFNT/OpenType: ' + name)
    for name in FONT_NOTICES:
        path = font_licenses / name
        if path.is_symlink() or not path.is_file() or not path.read_text(encoding='utf-8').strip():
            raise ValueError('Missing/unsafe font notice: ' + name)
    # All validation precedes product creation. Do not consume filesystem aliases
    # as executable authority; every installed code path is a measured file.
    output.mkdir(parents=True)
    text = 'format = 1\n\n[package]\n'
    for key, value in dict(provenance='built-in', id=package, name='WebKit', version=version, vendor='mochiOS', kind='binary', architecture='x86_64', abi='mochios-1').items():
        text += key + ' = ' + json.dumps(value) + '\n'
    text += 'revision = 1\n'
    count = 0
    def record(source, target, kind=None, caps=None):
        nonlocal count, text
        destination = output / 'payload/root' / target.lstrip('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        mode = '0755' if kind == 'application' else '0644'
        destination.chmod(int(mode, 8))
        fid = 'f' + str(count); count += 1
        text += '\n[[file]]\nid = ' + json.dumps(fid) + '\npath = ' + json.dumps(target) + '\n'
        with destination.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        text += 'digest = "sha256:' + digest + '"\nsize = ' + str(destination.stat().st_size) + '\nmode = "' + mode + '"\n'
        if kind:
            text += '\n[[binary]]\npath = ' + json.dumps(target) + '\nfile = ' + json.dumps(fid) + '\nkind = ' + json.dumps(kind) + '\nrequires = ' + json.dumps(caps or []) + '\n'
        return fid
    for name, source in sorted(selected.items()):
        target = PUBLIC_LIBRARY if name == 'libwebkit.so.1' else LIBRARY_ROOT + '/' + name
        record(source, target, 'library')
    for name, (source, _) in sorted(helpers.items()):
        target = LIBRARY_ROOT + '/helpers/' + name
        caps = PORT['helper_capabilities'][name]
        record(source, target, 'application', caps)
        if name == 'WPEWebProcess':
            text += 'resource_exports = [' + json.dumps(PORT['font_resource_export']) + ']\n'
        text += '\n[[executable_export]]\nname = ' + json.dumps(HELPERS[name]) + '\nbinary = ' + json.dumps(target) + '\n'
    font_bindings = {'fonts.conf': record(Path(__file__).resolve().parents[1] / 'engines/webkit/runtime/fonts.conf', LIBRARY_ROOT + '/fonts.conf')}
    for name in FONT_FILES:
        font_bindings['fonts/' + name] = record(fonts / name, LIBRARY_ROOT + '/fonts/' + name)
    for name in FONT_NOTICES:
        record(font_licenses / name, '/system/share/licenses/' + package + '/fonts/' + name)
    text += '\n[[resource_export]]\nname = ' + json.dumps(PORT['font_resource_export']) + '\n[resource_export.files]\n'
    for name, fid in font_bindings.items():
        text += json.dumps(name) + ' = ' + json.dumps(fid) + '\n'
    for source in sorted(licenses):
        record(source, '/system/share/licenses/' + package + '/' + str(source.relative_to(prefix / 'usr/share/licenses')))
    (output / 'manifest.toml').write_text(text)
    (output / 'artifacts.json').write_text(json.dumps(dict(format=1, delivery='system-image', update_owner='os', runtime_tested=False, signed=False,
        code_libraries=sorted(selected), helpers=sorted(helpers),
        limitations=['Full native execution remains separately required.']), indent=2) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--fonts', type=Path, required=True, help='Reviewed installed OS font files')
    parser.add_argument('--font-licenses', type=Path, required=True, help='Original Inter.txt, IBMPlexSansJP.txt and UDEVGothic.txt notices')
    args = parser.parse_args()
    stage(args.prefix, args.output, args.version, fonts=args.fonts, font_licenses=args.font_licenses)

if __name__ == '__main__': main()
