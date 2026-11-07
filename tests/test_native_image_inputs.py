"""Exercise raw and standalone-qcow2 input extraction with real QEMU image tools."""
import importlib.util
import json
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_image', Path(__file__).parent / 'mochios-tls/image.py')
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


@unittest.skipUnless(shutil.which('qemu-img') and shutil.which('qemu-io'), 'QEMU tools required')
class NativeImageInputs(unittest.TestCase):
    def test_raw_and_qcow2_extract_the_same_partition_bytes_without_writing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            raw, qcow = directory / 'source.img', directory / 'source.qcow2'
            expected = bytes(range(256)) * 4
            raw.write_bytes(b'\0' * 4096 + expected + b'\0' * 4096)
            subprocess.run(['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2', raw, qcow], check=True, capture_output=True)
            for source in [raw, qcow]:
                before = source.read_bytes()
                destination = directory / 'partition.img'
                image.extract(source, destination, 4096, len(expected))
                self.assertEqual(destination.read_bytes(), expected)
                self.assertEqual(source.read_bytes(), before)
                destination.unlink()


    def test_base_image_rejects_backing_chains_and_mismatched_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / '.config').write_text('DEVELOPMENT_SYSTEM_SIGNATURES=y\n')
            stage = directory / 'stage'
            (stage / 'system').mkdir(parents=True)
            base = directory / 'base'
            base.mkdir()
            disk = base / 'disk.qcow2'
            subprocess.run(['qemu-img', 'create', '-f', 'qcow2', disk, '1M'], check=True, capture_output=True)
            parts = [{'name': 'mochiOS ' + name} for name in ['ESP', 'System', 'Data']]
            command = [sys.executable, str(Path(image.__file__).resolve()), '--os-root', directory,
                       '--stage', stage, '--output', directory / 'output', '--base-image-directory', base]
            (base / 'image.json').write_text(json.dumps({'partitions': parts, 'virtual_bytes': 2097152}))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Base image must be standalone and match its metadata', result.stderr)
            self.assertFalse((directory / 'output').exists())
            backing = directory / 'backing.qcow2'
            disk.rename(backing)
            subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', backing, disk],
                           check=True, capture_output=True)
            (base / 'image.json').write_text(json.dumps({'partitions': parts, 'virtual_bytes': 1048576}))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Base image must be standalone and match its metadata', result.stderr)
            self.assertFalse((directory / 'output').exists())


if __name__ == '__main__':
    unittest.main()
