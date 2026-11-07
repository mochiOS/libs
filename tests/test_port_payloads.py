"""Port files and native helpers stay inside the ordinary signed-input pipeline."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import libs, releases, build_backends, container_build

class PortInputs(unittest.TestCase):
    def fixture(self, root):
        for name in ['Dockerfile','libraries.toml','sources.lock.json','sdk.lock.toml']:
            shutil.copy2(ROOT/name,root/name)
        shutil.copytree(ROOT/'tools',root/'tools',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'tooling',root/'tooling',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'recipes/zlib',root/'recipes/zlib')
        catalog=copy.deepcopy(libs.read_catalog(ROOT));catalog['libraries']={'zlib':catalog['libraries']['zlib']}
        asset=root/'recipes/zlib/assets/bridge.c';asset.parent.mkdir();asset.write_text('int bridge(void){return 1;}\n')
        path=root/'recipes/zlib/recipe.json';spec=json.loads(path.read_text());spec['overlays']={'assets/bridge.c':'Source/MochiOS/bridge.c'}
        spec['executables']=['usr/libexec/example/Worker'];path.write_text(json.dumps(spec))
        return catalog,asset,spec

    def test_overlay_changes_invalidate_the_frozen_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);catalog,asset,_=self.fixture(root)
            with patch.object(libs,'read_catalog',return_value=catalog):
                plan=libs.make_plan(root);libs.validate_plan(root,plan)
                before=plan['packages'][0]['input_id'];asset.write_text('int bridge(void){return 2;}\n')
                with self.assertRaises(ValueError):libs.validate_plan(root,plan)
                self.assertNotEqual(before,libs.make_plan(root)['packages'][0]['input_id'])

    def test_overlays_refuse_symlink_escapes_and_existing_upstream_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';assets=root/'assets';outside=root/'outside'
            for p in [source,assets,outside]:p.mkdir()
            (assets/'bridge.c').write_text('reviewed')
            spec={'overlays':{'bridge.c':'Source/MochiOS/bridge.c'}}
            build_backends.apply_overlays(spec,source,assets)
            self.assertEqual((source/'Source/MochiOS/bridge.c').read_text(),'reviewed')
            with self.assertRaisesRegex(ValueError,'collides'):build_backends.apply_overlays(spec,source,assets)
            (source/'escape').symlink_to(outside,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'escapes'):
                build_backends.apply_overlays({'overlays':{'bridge.c':'escape/file'}},source,assets)
            (assets/'external.c').symlink_to(root/'outside/file');(outside/'file').write_text('unreviewed')
            with self.assertRaisesRegex(ValueError,'escapes'):
                build_backends.apply_overlays({'overlays':{'external.c':'new.c'}},source,assets)
            for name in ['../file','/absolute','bad\\name']:
                with self.assertRaises(ValueError):build_backends.apply_overlays({'overlays':{'bridge.c':name}},source,assets)
            self.assertFalse((outside/'new.c').exists())

    @unittest.skipUnless(shutil.which('cc') and shutil.which('readelf'),'Host ELF tools required')
    def test_only_declared_freestanding_native_helpers_can_be_packaged_and_reverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);catalog,asset,spec=self.fixture(root)
            stage=root/'stage';(stage/'usr/lib').mkdir(parents=True)
            helper=stage/spec['executables'][0];helper.parent.mkdir(parents=True)
            c=root/'fixture.c';c.write_text('int fixture(void){return 42;} void fixture_entry(void){for(;;){}}\n')
            # Classification fixtures only: neither host fixture is run or
            # described as a target SDK/runtime compatibility verification.
            subprocess.run(['cc','-shared','-nostdlib','-fPIC',c,'-o',stage/'usr/lib/libz.so'],check=True)
            subprocess.run(['cc','-nostdlib','-no-pie','-fno-pie','-Wl,--no-dynamic-linker','-Wl,-e,fixture_entry',c,'-o',helper],check=True)
            container_build.validate_installed_tree(stage,spec)
            with self.assertRaisesRegex(ValueError,'Not a shared library'):
                container_build.validate_installed_tree(stage,{})
            with patch.object(libs,'read_catalog',return_value=catalog):
                plan=libs.make_plan(root);package=plan['packages'][0]
                manifest=libs.package_build(root,package,stage,dict(sdk=plan['sdk'],registry_commit='0'*40,builder={'fixture_only':True}),root/'releases')
                directory=root/'releases'/package['release'];releases.verify_bundle(directory,package,plan,root=root)
                self.assertIn('overlays',manifest['recipe'])
                asset.write_text('changed')
                with self.assertRaisesRegex(ValueError,'provenance'):releases.verify_bundle(directory,package,plan,root=root)
            helper.write_text('#!/bin/sh\n')
            with self.assertRaisesRegex(ValueError,'not ELF'):container_build.validate_installed_tree(stage,spec)
            helper.unlink()
            with self.assertRaisesRegex(ValueError,'Missing'):container_build.validate_installed_tree(stage,spec)

    def test_payload_scope_does_not_accept_arbitrary_helper_paths(self):
        spec={'executables':['usr/libexec/example/Worker']}
        self.assertTrue(libs.allowed_payload('usr/libexec/example/Worker',spec))
        self.assertFalse(libs.allowed_payload('usr/libexec/example/Other',spec))
        self.assertFalse(libs.allowed_payload('usr/bin/Worker',spec))

if __name__=='__main__':unittest.main()
