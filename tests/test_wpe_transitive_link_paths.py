"""The native SDK must resolve transitive libraries from explicit target prefixes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('MOCHIOS_TEST_SDK') and shutil.which('cmake'), 'set MOCHIOS_TEST_SDK and install CMake')
class TransitiveTargetDependencies(unittest.TestCase):
    def test_private_dependency_prefix_is_forwarded_without_runtime_rpath(self):
        sdk = Path(os.environ['MOCHIOS_TEST_SDK']).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            prefix = work / 'target-prefix'
            lib = prefix / 'usr/lib'
            lib.mkdir(parents=True)
            framework = work / 'framework'
            framework.mkdir()
            cc = sdk / 'bin/mochios-cc'
            (work / 'dependency.c').write_text('int mochi_transitive_value(void) { return 42; }\n')
            (work / 'framework.c').write_text('extern int mochi_transitive_value(void); int mochi_framework_value(void) { return mochi_transitive_value(); }\n')
            (work / 'helper.c').write_text('extern int mochi_framework_value(void); int mochi_helper(void) { return mochi_framework_value(); }\n')
            (work / 'main.c').write_text('extern int mochi_framework_value(void); int main(void) { return mochi_framework_value() != 42; }\n')
            def run(arguments):
                return subprocess.run(list(map(str, arguments)), check=True, capture_output=True, text=True)
            run([cc, '-shared', work / 'dependency.c', '-Wl,-soname,libmochi-transitive.so.1', '-o', lib / 'libmochi-transitive.so.1'])
            (lib / 'libmochi-transitive.so').symlink_to('libmochi-transitive.so.1')
            main = framework / 'libmochi-framework.so'
            run([cc, '-shared', work / 'framework.c', '-L'+str(lib), '-lmochi-transitive', '-Wl,-soname,libmochi-framework.so', '-o', main])
            # Reproduce the WPE helper failure: an absolute direct dependency
            # does not declare the prefix holding its PRIVATE DT_NEEDED object.
            missing = subprocess.run(list(map(str, [cc, '-shared', work / 'helper.c', main, '-o', work / 'missing.so'])), capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('Missing dependency for import validation: libmochi-transitive.so.1', missing.stderr)
            options = (ROOT / 'recipes/wpewebkit/OptionsMochiOS.cmake').read_text()
            start = options.index('function(_mochios_target_link_directories)')
            end = options.index('endfunction()', start) + len('endfunction()')
            (work / 'Paths.cmake').write_text(options[start:end])
            (work / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(TransitiveNativeLink C)
set(CMAKE_FIND_ROOT_PATH "relative;;/;${TARGET_PREFIX}")
include(Paths.cmake)
_mochios_target_link_directories()
add_library(helper SHARED helper.c)
target_link_libraries(helper PRIVATE "${FRAMEWORK_LIBRARY}")
add_executable(consumer main.c)
target_link_libraries(consumer PRIVATE "${FRAMEWORK_LIBRARY}")
''')
            build = work / 'build'
            run(['cmake', '-S', work, '-B', build, '-DCMAKE_TOOLCHAIN_FILE='+str(sdk / 'share/cmake/mochios.cmake'),
                 '-DTARGET_PREFIX='+str(prefix), '-DFRAMEWORK_LIBRARY='+str(main), '-DCMAKE_SKIP_RPATH=ON'])
            result = run(['cmake', '--build', build, '--verbose'])
            self.assertIn('-L'+str(lib), result.stdout)
            self.assertNotIn(' -L/usr/lib', result.stdout)
            self.assertNotIn(' -Lrelative', result.stdout)
            helper = build / 'libhelper.so'
            self.assertTrue(helper.is_file())
            self.assertTrue((build / 'consumer').is_file())
            dynamic = run([sdk / 'host/bin/readelf', '-d', helper]).stdout
            self.assertIn('[libmochi-framework.so]', dynamic)
            self.assertNotIn('(RPATH)', dynamic)
            self.assertNotIn('(RUNPATH)', dynamic)
