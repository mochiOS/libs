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
        section='--- a/src/util/u_endian.h\n'+patch.split('--- a/src/util/u_endian.h\n',1)[1].split('\n--- a/',1)[0].rstrip()+'\n'
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

    @unittest.skipUnless(shutil.which('cc') and shutil.which('patch'), 'C compiler/patch required')
    def test_unsupported_linux_fences_fail_without_touching_descriptors(self):
        patch=(ROOT/'recipes/mesa/mochios.patch').read_text()
        section='--- a/src/util/libsync.h\n'+patch.split('--- a/src/util/libsync.h\n',1)[1].split('\n--- a/',1)[0].rstrip()+'\n'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); header=root/'src/util/libsync.h';header.parent.mkdir(parents=True)
            shutil.copy2(ROOT/'tests/fixtures/mesa-26.2.6/libsync.h',header)
            subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(root)],input=section,text=True,check=True,capture_output=True)
            (header.parent/'detect_os.h').write_text('#define DETECT_OS_MOCHIOS 1\n#define DETECT_OS_ANDROID 0\n')
            (header.parent/'os_file.h').write_text('')
            source=root/'check.c'
            source.write_text('''#include "util/libsync.h"
#define CHECK(call, value) do { errno=0; if ((call)!=(value) || errno!=ENOTSUP) return __LINE__; } while(0)
int main(void) {
    int fd=123;
    CHECK(sync_wait(fd,0),-1);
    CHECK(sync_merge("test",fd,456),-1);
    CHECK(sync_valid_fd(fd),false);
    CHECK(sync_file_info(fd),NULL);
    CHECK(sync_accumulate("test",&fd,456),-1);
    if (fd!=123) return __LINE__;
    fd=-1;
    CHECK(sync_accumulate("test",&fd,456),-1);
    return fd!=-1;
}
''')
            subprocess.run(['cc','-std=c11','-D_POSIX_C_SOURCE=200809L','-Werror=implicit-function-declaration','-I'+str(root/'src'),str(source),'-o',str(root/'check')],check=True,capture_output=True)
            subprocess.run([str(root/'check')],check=True)
