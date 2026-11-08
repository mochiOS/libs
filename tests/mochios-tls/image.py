#!/usr/bin/env python3
"""Build a standalone development VM image without modifying the OS image."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def run(command):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def extract(source, target, offset, size):
    if source.suffix == '.qcow2':
        if any(c in str(source) for c in [',', '\n']):
            raise ValueError('Image paths must not contain commas or newlines')
        options = (f'driver=raw,offset={offset},size={size},file.driver=qcow2,'
                   f'file.file.driver=file,file.file.filename={source}')
        run(['qemu-img', 'convert', '--image-opts', options, '-O', 'raw', target])
        return
    run(['dd', 'if=' + str(source), 'of=' + str(target), 'bs=1M',
         'iflag=skip_bytes,count_bytes', 'skip=' + str(offset), 'count=' + str(size),
         'conv=sparse', 'status=none'])



def write_partition(disk, source, offset, size):
    """Copy exact partition bytes through a bounded raw view, preserving holes.

    Without --target-is-zero, zero input also clears previously allocated data.
    Adjacent partitions are outside the raw view and cannot be overwritten.
    """
    if offset < 0 or size <= 0 or source.stat().st_size != size:
        raise ValueError('Partition source size or offset mismatch')
    if any(c in str(disk) for c in [',', '\n']):
        raise ValueError('Image paths must not contain commas or newlines')
    target=(f'driver=raw,offset={offset},size={size},file.driver=qcow2,'
            f'file.file.driver=file,file.file.filename={disk}')
    run(['qemu-img','convert','-n','--target-image-opts','-f','raw',source,target])


def debug(image, command, allow_missing=False):
    result = run(['debugfs', '-w', '-R', command, image])
    text = result.stdout + result.stderr
    failures = ['Could not allocate', 'No space left', 'write: short write', 'Filesystem is read-only']
    if not allow_missing:
        failures += ['not found by ext2_lookup', 'File not found']
    if any(value in text for value in failures):
        raise RuntimeError(text)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os-root', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kernel-directory', type=Path)
    parser.add_argument('--base-image-directory', type=Path,
                        help='Read-only standalone development qcow2 with image.json from a previous composition')
    args = parser.parse_args()
    root, stage, output = args.os_root.resolve(), args.stage.resolve(), args.output.resolve()
    if output.exists():
        parser.error('Choose a fresh output directory')
    if 'DEVELOPMENT_SYSTEM_SIGNATURES=y' not in (root / '.config').read_text().splitlines():
        parser.error('An existing development configuration is required')
    if args.base_image_directory:
        base = args.base_image_directory.resolve()
        source = base / 'disk.qcow2'
        metadata = json.loads((base / 'image.json').read_text())
        parts, virtual_size = metadata['partitions'], metadata['virtual_bytes']
        info = json.loads(run(['qemu-img', 'info', '--output=json', source]).stdout)
        if info.get('backing-filename') or info['virtual-size'] != virtual_size:
            parser.error('Base image must be standalone and match its metadata')
    else:
        source = root / 'out/artifacts/disk.img'
        table = json.loads(run(['sfdisk', '-J', source]).stdout)['partitiontable']
        parts = [{key: part[key] for key in ['start', 'size', 'type', 'uuid', 'name']}
                 for part in table['partitions']]
        if table['sectorsize'] != 512:
            parser.error('Unsupported image sector size')
        virtual_size = source.stat().st_size
    source_stat = source.stat()
    if [p.get('name') for p in parts] != ['mochiOS ESP', 'mochiOS System', 'mochiOS Data']:
        parser.error('This fixture supports the normal ESP/System/Data development layout only')
    files = sorted(stage.rglob('*'))
    if any(p.is_symlink() or not p.relative_to(stage).parts[0] == 'system' for p in files):
        parser.error('Only regular staged System files and directories are accepted')
    output.mkdir(parents=True)
    disk = output / 'disk.qcow2'
    evidence = {'format': 1, 'runtime_tested': False,
                'source_image_sha256': hashlib.file_digest(source.open('rb'), 'sha256').hexdigest(),
                'partitions': parts, 'virtual_bytes': virtual_size}
    with tempfile.TemporaryDirectory(prefix='compose-', dir=output) as temporary:
        work = Path(temporary)
        esp, system, data = parts
        espimage = work / 'esp.img'
        extract(source, espimage, esp['start'] * 512, esp['size'] * 512)
        systemimage = work / 'system.img'
        extract(source, systemimage, system['start'] * 512, system['size'] * 512)
        for path in sorted([p for p in files if p.is_dir()], key=lambda p: len(p.parts)):
            debug(systemimage, 'mkdir /' + str(path.relative_to(stage)), allow_missing=True)
        for path in files:
            if not path.is_file():
                continue
            relative = '/' + str(path.relative_to(stage))
            debug(systemimage, 'rm ' + relative, allow_missing=True)
            written = debug(systemimage, 'write ' + str(path) + ' ' + relative)
            if 'Allocated inode:' not in written.stdout + written.stderr:
                raise RuntimeError('File was not staged: ' + relative)
            mode = 0o100000 | (path.stat().st_mode & 0o777)
            debug(systemimage, f'set_inode_field {relative} mode 0{mode:o}')
        digest = work / 'system.sha256'
        digest.write_bytes(hashlib.file_digest(systemimage.open('rb'), 'sha256').digest())
        evidence['system_sha256'] = digest.read_bytes().hex()
        run(['qemu-img', 'create', '-f', 'qcow2', disk, virtual_size])
        write_partition(disk, systemimage, system['start'] * 512, system['size'] * 512)
        systemimage.unlink()
        initfs = work / 'initfs.img'
        run(['mcopy', '-i', espimage, '::/system/initfs.img', initfs])
        debug(initfs, 'rm /install/rootfs.sha256')
        debug(initfs, 'write ' + str(digest) + ' /install/rootfs.sha256')
        initstage = work / 'initfs'
        initstage.mkdir()
        run(['debugfs', '-R', 'rdump / ' + str(initstage), initfs])
        with initfs.open('rb') as stream:
            stream.seek(1024)
            superblock = stream.read(60)
        resource_bytes = struct.unpack_from('<I', superblock, 4)[0] * (
            1024 << struct.unpack_from('<I', superblock, 24)[0])
        run(['perl', root / 'scripts/mmake/pack-boot-module.pl', 'pack', initfs,
             resource_bytes, initstage / 'init', initstage / 'config/kernel.conf', initstage, digest])
        run(['mcopy', '-o', '-i', espimage, initfs, '::/system/initfs.img'])
        if args.kernel_directory:
            for leaf in ['kernel.elf', 'kernel.meta']:
                run(['mcopy', '-o', '-i', espimage, args.kernel_directory.resolve() / leaf,
                     '::/system/' + leaf])
        kernel = work / 'kernel.elf'
        run(['mcopy', '-i', espimage, '::/system/kernel.elf', kernel])
        evidence['kernel_sha256'] = hashlib.file_digest(kernel.open('rb'), 'sha256').hexdigest()
        write_partition(disk, espimage, esp['start'] * 512, esp['size'] * 512)
        espimage.unlink()
        dataimage = work / 'data.img'
        extract(source, dataimage, data['start'] * 512, data['size'] * 512)
        settings = debug(dataimage, 'cat /var/config/account/settings.conf').stdout
        if 'auto_login=true' not in [line.strip() for line in settings.splitlines()]:
            raise RuntimeError('A development image with existing automatic login is required; authentication settings are never changed')
        debug(dataimage, 'rm /tmp/native-tls-results.log', allow_missing=True)
        write_partition(disk, dataimage, data['start'] * 512, data['size'] * 512)
        for offset in [0, virtual_size - 1048576]:
            block = work / 'gpt.bin'
            extract(source, block, offset, 1048576)
            run(['qemu-io', '-f', 'qcow2', '-c', f'write -c -s {block} {offset} 1048576', disk])
            block.unlink()
    after = source.stat()
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (
            source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns):
        disk.unlink()
        raise RuntimeError('Source image changed while composing the fixture; rebuild from a stable image')
    shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.snakeoil.fd', output / 'OVMF_VARS.fd')
    (output / 'image.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print('Independent development VM image:', disk)


if __name__ == '__main__':
    main()
