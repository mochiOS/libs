"""Exercise the libxml2 patch against conflicting SDK/dependency headers."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(all(shutil.which(t) for t in ['cc', 'cmake', 'patch']),
                     'CMake, patch and host fixture compiler required')
class Libxml2HeaderTests(unittest.TestCase):
    def test_imported_iconv_header_precedes_sdk_header(self):
        # Host-only ABI fixture: no target binaries are executed. The wrapper
        # reproduces the SDK driver's prepended -isystem include directory.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root/'sdk'
            deps = root/'deps'
            sdk.mkdir()
            deps.mkdir()
            (sdk/'iconv.h').write_text('int iconv_open(void);\n')
            (deps/'iconv.h').write_text('#define iconv_open libiconv_open\nint iconv_open(void);\n')
            (root/'provider.c').write_text('int libiconv_open(void) { return 1; }\n')
            subprocess.run(['cc', '-shared', '-fPIC', str(root/'provider.c'),
                            '-o', str(deps/'libiconv.so')], check=True, capture_output=True)
            wrapper = root/'cc'
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(shutil.which('cc')) +
                               ' -isystem ' + shlex.quote(str(sdk)) + ' "$@"\n')
            wrapper.chmod(0o755)
            (root/'fixture.c').write_text('#include <iconv.h>\nint convert(void) { return iconv_open(); }\n')
            (root/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(header_order C)
add_library(Iconv::Iconv SHARED IMPORTED)
set_target_properties(Iconv::Iconv PROPERTIES IMPORTED_LOCATION "${CMAKE_CURRENT_SOURCE_DIR}/deps/libiconv.so" INTERFACE_INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}/deps")
set(LIBXML2_SRCS fixture.c)
add_library(LibXml2 ${LIBXML2_HDRS} ${LIBXML2_SRCS} ${LIBXML2_PRIVATE_HDRS})
add_library(LibXml2::LibXml2 ALIAS LibXml2)

target_include_directories(
    LibXml2
    PUBLIC
    "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(LibXml2 PRIVATE Iconv::Iconv)
target_link_options(LibXml2 PRIVATE -nostdlib -Wl,--no-undefined)
''')
            def build(name):
                directory = root/name
                subprocess.run(['cmake', '-S', str(root), '-B', str(directory),
                                '-DCMAKE_C_COMPILER='+str(wrapper), '-DBUILD_SHARED_LIBS=ON'],
                               check=True, capture_output=True, text=True)
                return subprocess.run(['cmake', '--build', str(directory)],
                                      capture_output=True, text=True)
            before = build('before')
            self.assertNotEqual(before.returncode, 0)
            self.assertIn('iconv_open', before.stdout + before.stderr)
            recipe = json.loads((ROOT/'recipes/libxml2/recipe.json').read_text())
            for patch in recipe['patches']:
                subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i',
                                str(ROOT/'recipes/libxml2'/patch)], cwd=root,
                               check=True, capture_output=True)
            after = build('after')
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
