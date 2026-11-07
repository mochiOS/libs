"""The C ABI contract and a readiness plan must not masquerade as a browser engine."""
import copy
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import webkit_ci
import webkit_container

class Readiness(unittest.TestCase):
    def test_native_readiness_lists_real_missing_ports_and_never_claims_runtime(self):
        plan=webkit_ci.plan(ROOT)
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['build_status'],'blocked')
        self.assertFalse(plan['runtime_tested'])
        self.assertTrue({'epoxy','egl'}.issubset(plan['missing_modules']))
        self.assertNotIn('glib-2.0',plan['missing_modules'])
        self.assertNotIn('libjpeg',plan['missing_modules'])
        self.assertNotIn('xkbcommon',plan['missing_modules'])
        self.assertTrue(any('backend' in s for s in plan['blockers']))
        self.assertTrue(any('Capability' in s for s in plan['blockers']))
        self.assertEqual(plan['product'],'libwebkit.so.1')
        self.assertTrue(all(not key.startswith('/') for key in plan['inputs']))

    def test_blocked_build_never_fetches_builds_or_generates_an_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'build'
            with patch.object(webkit_ci.libs,'build') as build,patch.object(webkit_ci.webkit,'fetch') as fetch:
                with self.assertRaisesRegex(ValueError,'blocked'):webkit_ci.build(ROOT,output)
                build.assert_not_called();fetch.assert_not_called()
            self.assertTrue((output/'plan.json').is_file())
            self.assertFalse(list(output.rglob('libwebkit.so*')))
            self.assertFalse(list(output.rglob('candidate.json')))

    def test_dependency_or_api_changes_change_the_plan_identity(self):
        before=webkit_ci.plan(ROOT)
        original=webkit_ci.libs.hash_file
        def altered(path):
            return '1'*64 if path.name=='webkit.h' else original(path)
        with patch.object(webkit_ci.libs,'hash_file',altered):after=webkit_ci.plan(ROOT)
        self.assertNotEqual(before['identity'],after['identity'])

    @unittest.skipUnless(shutil.which('cmake') and shutil.which('c++'), 'Host CMake toolchain required')
    def test_production_configuration_rejects_a_host_reference_build(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run(['cmake','-S',str(ROOT/'engines/webkit'),'-B',directory,
                '-DMWK_BUILD_ENGINE=ON'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('host reference builds are not distributable',' '.join((result.stdout+result.stderr).split()))
            self.assertFalse(list(Path(directory).rglob('libwebkit.so*')))

    @unittest.skipUnless(shutil.which('patch'), 'Host patch tool required')
    def test_native_patches_apply_once_and_changed_inputs_require_a_fresh_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);engine=root/'engine';source=root/'source';logs=root/'logs'
            engine.mkdir();source.mkdir();(source/'file.txt').write_text('old\n')
            patchfile=engine/'native.patch'
            patchfile.write_text('--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n')
            webkit_container.apply_port_patches(engine,source,['native.patch'],os.environ.copy(),logs)
            self.assertEqual((source/'file.txt').read_text(),'new\n')
            webkit_container.apply_port_patches(engine,source,['native.patch'],os.environ.copy(),logs)
            patchfile.write_text(patchfile.read_text().replace('+new','+changed'))
            with self.assertRaisesRegex(ValueError,'changed'):
                webkit_container.apply_port_patches(engine,source,['native.patch'],os.environ.copy(),logs)
            self.assertEqual((source/'file.txt').read_text(),'new\n')

    def test_native_patches_cannot_read_outside_the_reviewed_engine_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);engine=root/'engine';engine.mkdir()
            (root/'outside.patch').write_text('not a reviewed patch')
            with self.assertRaisesRegex(ValueError,'Invalid'):
                webkit_container.apply_port_patches(engine,root,['../outside.patch'],{},root/'logs')
            self.assertFalse((root/'logs/native-patches.json').exists())

    def test_workflow_has_no_publication_credentials_or_release_command(self):
        text=(ROOT/'.github/workflows/webkit.yml').read_text()
        self.assertIn("needs.contract.outputs.ready == 'true'",text)
        self.assertNotIn('contents: write',text)
        self.assertNotIn('secrets.',text)
        self.assertNotIn('releases.py publish',text)
        self.assertIn('if: always()',text)

if __name__=='__main__':unittest.main()
