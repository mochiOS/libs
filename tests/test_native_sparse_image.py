"""Exercise real QEMU partition writes: exact bytes, isolation and sparse allocation."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('image',Path(__file__).parent/'mochios-tls/image.py')
image=importlib.util.module_from_spec(spec);spec.loader.exec_module(image)
@unittest.skipUnless(shutil.which('qemu-img'),'QEMU image tools unavailable')
class SparseImage(unittest.TestCase):
    def test_sparse_partition_copy_clears_old_bytes_without_touching_neighbors(self):
        unit=1024**2
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);initial=root/'initial';part=root/'part';disk=root/'disk.qcow2';actual=root/'actual'
            with initial.open('wb') as out:
                out.write(b'prefix');out.seek(2*unit);out.write(b'old data');out.seek(18*unit-6);out.write(b'suffix')
            with part.open('wb') as out:
                out.seek(16*unit-1);out.write(b'X')
            subprocess.run(['qemu-img','convert','-f','raw','-O','qcow2',initial,disk],check=True,capture_output=True)
            image.write_partition(disk,part,unit,16*unit)
            self.assertLess(disk.stat().st_blocks*512,2*unit)
            subprocess.run(['qemu-img','convert','-O','raw',disk,actual],check=True,capture_output=True)
            with actual.open('rb') as inp:
                self.assertEqual(inp.read(unit),initial.read_bytes()[:unit])
                self.assertEqual(inp.read(16*unit),part.read_bytes())
                self.assertEqual(inp.read(),initial.read_bytes()[17*unit:])
            for offset,size in [(-1,16*unit),(0,0),(0,1)]:
                with self.assertRaises(ValueError):image.write_partition(disk,part,offset,size)
if __name__=='__main__':unittest.main()
