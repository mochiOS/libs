#!/usr/bin/env python3
"""Run GSubprocess on mochiOS; partial logs and successful boot are not test passes."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
CASES = ('pipes-argv-env-cwd', 'nonzero-exit', 'path-search', 'async-wait', 'cancelled-wait', 'missing-executable')

def outcome(text):
    complete = 'GSUBPROCESS COMPLETE failures=' in text
    failed = 'GSUBPROCESS FAIL ' in text
    cases = {case: text.count('GSUBPROCESS PASS mode=' + case + '\n') == 1 for case in CASES}
    passed = (complete and not failed and all(cases.values())
              and text.count('GSUBPROCESS PASS mode=') == len(CASES)
              and text.count('GSUBPROCESS COMPLETE failures=') == 1
              and text.count('GSUBPROCESS COMPLETE failures=0\n') == 1)
    return {'complete': complete, 'passed': passed, 'failed': failed, 'cases': cases}

def read_results(directory, data):
    image = directory / 'disk.qcow2'
    with tempfile.TemporaryDirectory(prefix='read-', dir=directory) as temporary:
        snapshot = Path(temporary) / 'data.img'
        options = (f"driver=raw,offset={data['start'] * 512},size={data['size'] * 512},"
                   f'file.driver=qcow2,file.file.driver=file,file.file.filename={image}')
        subprocess.run(['qemu-img', 'convert', '-U', '--image-opts', options, '-O', 'raw', snapshot],
                       check=True, capture_output=True)
        result = subprocess.run(['debugfs', '-R', 'cat /tmp/gsubprocess-results.log', snapshot],
                                capture_output=True, text=True, check=True)
        if 'not found by ext2_lookup' in result.stderr:
            return ''
        return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-directory', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    directory = args.image_directory.resolve()
    if any(c in str(directory) for c in [',', '\n']):
        parser.error('QEMU image paths must not contain commas or newlines')
    if args.timeout <= 0:
        parser.error('Timeout must be positive')
    metadata = json.loads((directory / 'image.json').read_text())
    build = json.loads(args.build.read_text())
    serial = directory / 'serial.log'
    if serial.exists():
        parser.error('This VM has already been run; create a fresh test image')
    process = None
    result = {'complete': False, 'passed': False, 'failed': False, 'cases': {case: False for case in CASES}}
    started = time.monotonic()
    text = ''
    try:
        # TCG clock follows guest instructions, avoiding host-load-induced boot timeouts.
        acceleration = ['-accel', 'kvm'] if Path('/dev/kvm').exists() else [
            '-accel', 'tcg,thread=single', '-icount', 'shift=0,align=off,sleep=off']
        command = ['qemu-system-x86_64', '-machine', 'q35,smm=on', *acceleration,
                   '-m', '1G', '-smp', '2', '-cpu', 'host' if Path('/dev/kvm').exists() else 'max', '-rtc', 'base=utc,clock=vm',
                   '-serial', 'file:' + str(serial), '-no-reboot', '-no-shutdown',
                   '-boot', 'order=c,menu=off,strict=on',
                   '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd',
                   '-drive', 'if=pflash,format=raw,file=' + str(directory / 'OVMF_VARS.fd'),
                   '-global', 'driver=cfi.pflash01,property=secure,value=on',
                   '-drive', 'id=osdisk,if=none,format=qcow2,file=' + str(directory / 'disk.qcow2'),
                   '-device', 'virtio-blk-pci,disable-modern=on,drive=osdisk,bootindex=1',
                   '-object', 'rng-random,id=rng0,filename=/dev/urandom', '-device', 'virtio-rng-pci,rng=rng0',
                   '-netdev', 'user,id=net0',
                   '-device', 'virtio-net-pci,disable-legacy=on,netdev=net0,mac=52:54:00:12:34:56',
                   '-device', 'virtio-gpu-pci,id=virtio-gpu,disable-legacy=on', '-display', 'none']
        with (directory / 'qemu.log').open('w') as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        next_read, last_log = 0, ''
        while process.poll() is None and time.monotonic() - started < args.timeout:
            log = serial.read_text(errors='replace') if serial.exists() else ''
            if 'PAGE FAULT' in log or 'Faulting user context:' in log:
                raise RuntimeError('Guest fault; inspect serial.log')
            if log != last_log:
                for line in log[len(last_log):].splitlines():
                    if 'native process created' in line or 'GSUBPROCESS' in line or 'Binder.app ready' in line:
                        print(line, flush=True)
                last_log = log
            if time.monotonic() >= next_read:
                text = read_results(directory, metadata['partitions'][2])
                result = outcome(text)
                (directory / 'gsubprocess-results.log').write_text(text)
                if result['complete'] or result['failed']:
                    break
                next_read = time.monotonic() + 5
            time.sleep(.5)
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        image_evidence = dict(metadata)
        image_evidence.pop('runtime_tested', None)
        report = {'format': 1, **result, 'runtime_tested': result['passed'], 'build': build, 'image': image_evidence, 'elapsed_host_seconds': round(time.monotonic() - started, 3)}
        (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(text, end='')
    if not result['passed']:
        raise SystemExit('Native GSubprocess cases failed or did not all complete; this is not a runtime pass')
    print('Native GSubprocess cases passed')


if __name__ == '__main__':
    main()
