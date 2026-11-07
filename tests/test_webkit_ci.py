"""The C ABI contract and a readiness plan must not masquerade as a browser engine."""
import copy
import importlib.util
import json
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

class Readiness(unittest.TestCase):
    def test_native_readiness_lists_real_missing_ports_and_never_claims_runtime(self):
        plan=webkit_ci.plan(ROOT)
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['build_status'],'blocked')
        self.assertFalse(plan['runtime_tested'])
        self.assertTrue({'epoxy','egl','xkbcommon'}.issubset(plan['missing_modules']))
        self.assertNotIn('glib-2.0',plan['missing_modules'])
        self.assertNotIn('libjpeg',plan['missing_modules'])
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

    def test_workflow_has_no_publication_credentials_or_release_command(self):
        text=(ROOT/'.github/workflows/webkit.yml').read_text()
        self.assertIn("needs.contract.outputs.ready == 'true'",text)
        self.assertNotIn('contents: write',text)
        self.assertNotIn('secrets.',text)
        self.assertNotIn('releases.py publish',text)
        self.assertIn('if: always()',text)

if __name__=='__main__':unittest.main()
