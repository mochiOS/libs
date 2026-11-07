"""Exercise Mesa's real patched endian header for both target byte orders."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class MesaPort(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc') and shutil.which('patch'), 'C preprocessor/patch required')
    def test_target_endian_is_independent_of_build_host(self):
        patch=(ROOT/'recipes/mesa/mochios.patch').read_text()
        section='--- a/src/util/u_endian.h\n'+patch.split('--- a/src/util/u_endian.h\n',1)[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); header=root/'src/util/u_endian.h';header.parent.mkdir(parents=True)
            shutil.copy2(ROOT/'tests/fixtures/mesa-26.2.6/u_endian.h',header)
            subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(root)],input=section,text=True,check=True,capture_output=True)
            for order,little,big in [('1234',1,0),('4321',0,1),('9999',None,None),(None,None,None)]:
                args=['cc','-E','-P','-nostdinc','-undef','-D__mochios__','-D__ORDER_LITTLE_ENDIAN__=1234','-D__ORDER_BIG_ENDIAN__=4321']
                if order is not None:args+=['-D__BYTE_ORDER__='+order]
                source=f'#include "{header}"\nUTIL_ARCH_LITTLE_ENDIAN UTIL_ARCH_BIG_ENDIAN\n'
                result=subprocess.run(args+['-x','c','-'],input=source,text=True,capture_output=True)
                with self.subTest(order=order):
                    if little is None:self.assertNotEqual(result.returncode,0)
                    else:
                        self.assertEqual(result.returncode,0,result.stderr)
                        self.assertEqual(result.stdout.strip(),f'{little} {big}')
