"""Build split object targets before the parent: generated headers must exist."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/wpe-2.54.2'


@unittest.skipUnless(all(shutil.which(x) for x in ['cmake', 'cc', 'patch']) and any(shutil.which(x) for x in ['ninja', 'make', 'gmake']), 'CMake/cc/patch and a build tool required')
class GeneratedHeaderOrder(unittest.TestCase):
    def test_split_target_waits_for_framework_and_binding_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / 'source'
            shutil.copytree(FIXTURE, source)
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(source),
                                     '-i', str(ROOT / 'recipes/wpewebkit/mochios.patch')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            macros = (source / 'Source/cmake/WebKitMacros.cmake').read_text()
            start = macros.index('function(WEBKIT_DEFINE_SUBTARGET _target _parent)')
            end = macros.index('endfunction()', start) + len('endfunction()')
            (work / 'Split.cmake').write_text(macros[start:end])
            platform = (source / 'Source/WebKit/PlatformWPE.cmake').read_text()
            start = platform.index('if (CMAKE_SYSTEM_NAME STREQUAL "mochiOS")')
            end = platform.index('endif ()', start) + len('endif ()')
            header_rule = platform[start:end]
            (work / 'header.h.in').write_text('#define GENERATED_VALUE 42\n')
            (work / 'consumer.c').write_text('#include "WebKitDirectoryInputStreamData.h"\n#include "Binding.h"\nint result(void) { return GENERATED_VALUE; }\n')
            cmake = r"""cmake_minimum_required(VERSION 3.20)
project(GeneratedOrder C)
set(CMAKE_SYSTEM_NAME mochiOS)
include(Split.cmake)
set(WebKit_DERIVED_SOURCES_DIR "${CMAKE_BINARY_DIR}/derived")
file(MAKE_DIRECTORY "${WebKit_DERIVED_SOURCES_DIR}")
foreach(name IN ITEMS WebKitDirectoryInputStreamData Binding)
    add_custom_command(OUTPUT "${WebKit_DERIVED_SOURCES_DIR}/${name}.h"
        COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_SOURCE_DIR}/header.h.in" "${WebKit_DERIVED_SOURCES_DIR}/${name}.h"
        DEPENDS "${CMAKE_SOURCE_DIR}/header.h.in")
endforeach()
set(WebKit_DERIVED_SOURCES "${WebKit_DERIVED_SOURCES_DIR}/Binding.h")
""" + header_rule + r"""
add_library(WebKit SHARED consumer.c)
target_include_directories(WebKit PRIVATE "${WebKit_DERIVED_SOURCES_DIR}")
WEBKIT_DEFINE_SUBTARGET(WebKitNetworkProcess WebKit consumer.c)
"""
            (work / 'CMakeLists.txt').write_text(cmake)
            generator = 'Ninja' if shutil.which('ninja') else 'Unix Makefiles'
            subprocess.run(['cmake', '-S', str(work), '-B', str(work / 'build'), '-G', generator], check=True, capture_output=True)
            # Build only the split target, not WebKit: parent link ordering cannot mask the bug.
            result = subprocess.run(['cmake', '--build', str(work / 'build'), '--target', 'WebKitNetworkProcess', '--parallel', '4'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in ['WebKitDirectoryInputStreamData', 'Binding']:
                self.assertTrue((work / 'build/derived' / (name + '.h')).exists())
