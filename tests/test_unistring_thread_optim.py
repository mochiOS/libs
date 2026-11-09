"""The native process runtime cannot be proven single-threaded by a GOT scan."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def prepare(work):
    source = work / 'source'
    shutil.copytree(ROOT / 'tests/fixtures/libunistring-1.5', source)
    patch = (ROOT / 'recipes/libunistring/mochios.patch').read_text()
    section = patch[patch.index('--- a/lib/thread-optim.h\n'):]
    patchfile = work / 'thread.patch'
    patchfile.write_text(section)
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(source), '-i', str(patchfile)],
                   check=True, capture_output=True, text=True)
    (source / 'lib/config.h').write_text('#define _GL_CONFIG_H_INCLUDED 1\n#define HAVE_SYS_SINGLE_THREADED_H 0\n#define HAVE_LINK_H 1\n#define HAVE_DL_ITERATE_PHDR 1\n')
    return source / 'lib'


@unittest.skipUnless(shutil.which('cc') and shutil.which('patch'), 'host cc and patch required')
class NativeThreadOptimization(unittest.TestCase):
    def test_native_locks_remain_required_after_an_object_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = prepare(work)
            harness = work / 'check.c'
            harness.write_text('#include "config.h"\n#include "thread-optim.h"\n'
                               '#ifndef _GL_MULTITHREADED_ALWAYS_TRUE\n#error unsafe native ELF heuristic\n#endif\n'
                               '#ifdef _GL_MULTITHREADED_VIA_ELF\n#error GOT inspection enabled\n#endif\n'
                               'int main(void) { if (!gl_multithreaded()) return 1; gl_set_multithreaded(0); return !gl_multithreaded(); }\n')
            binary = work / 'check'
            subprocess.run(['cc', '-D__mochios__=1', '-I'+str(source), str(harness),
                            str(source / 'thread-optim.c'), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)
            # The port must leave other ELF targets on their existing path.
            harness.write_text('#include "config.h"\n#include "thread-optim.h"\n'
                               '#ifndef _GL_MULTITHREADED_VIA_ELF\n#error changed non-native target\n#endif\n')
            subprocess.run(['cc', '-fsyntax-only', '-I'+str(source), str(harness)], check=True, capture_output=True)

    @unittest.skipUnless(os.environ.get('MOCHIOS_TEST_SDK'), 'set MOCHIOS_TEST_SDK for actual native compilation')
    def test_actual_sdk_compiles_the_upstream_translation_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = prepare(work)
            cc = Path(os.environ['MOCHIOS_TEST_SDK']).resolve() / 'bin/mochios-cc'
            subprocess.run([str(cc), '-Wall', '-Wextra', '-Werror', '-I'+str(source), '-c',
                            str(source / 'thread-optim.c'), '-o', str(work / 'thread-optim.o')],
                           check=True, capture_output=True, text=True)
