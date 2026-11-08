"""Real bundle verification/restore fixtures; no target runtime or GitHub writes."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import libs
import releases

BUILDER = 'sha256:' + '1' * 64


@unittest.skipUnless(shutil.which('cc') and shutil.which('readelf'), 'Host ELF tools required')
class CompletedBundleCache(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.catalog = copy.deepcopy(libs.read_catalog(ROOT))
        self.catalog['libraries'] = {'zlib': self.catalog['libraries']['zlib']}
        catalog_patch = patch.object(libs, 'read_catalog', return_value=self.catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        sdk = copy.deepcopy(libs.sdk_lock(ROOT))
        sdk['development_only'] = True
        sdk_patch = patch.object(libs, 'sdk_lock', return_value=sdk)
        sdk_patch.start()
        self.addCleanup(sdk_patch.stop)
        self.plan = libs.make_plan(ROOT)
        self.package = self.plan['packages'][0]
        stage = self.work / 'stage'
        (stage / 'usr/lib').mkdir(parents=True)
        source = self.work / 'fixture.c'
        source.write_text('int cache_classification_fixture(void) { return 42; }\n')
        # An unexecuted freestanding ELF classification fixture, not a native SDK build.
        subprocess.run(['cc', '-shared', '-nostdlib', '-fPIC', source,
                        '-o', stage / 'usr/lib/libz.so'], check=True)
        self.output = self.work / 'first'
        libs.package_build(ROOT, self.package, stage, dict(sdk=self.plan['sdk'],
            registry_commit='0' * 40, builder=dict(image=BUILDER,
            reference=self.plan['sdk']['builder_image'])), self.output / 'releases')

    def save(self):
        releases.save_build_cache(ROOT, self.package, self.plan, self.output, BUILDER)
        return self.output / 'verified-cache' / self.package['input_id']

    def fresh_output(self):
        result = self.work / 'next'
        shutil.copytree(self.output / 'verified-cache', result / 'verified-cache')
        return result

    def test_completed_dependency_survives_an_incomplete_overall_plan_and_fresh_runner(self):
        self.save()
        self.assertTrue((self.output / 'verified-cache-updated').is_file())
        output = self.fresh_output()  # No sources, objects, complete.json or SDK copied.
        releases.prepare_build_cache(self.plan, output)
        stage = releases.restore_build_cache(ROOT, self.package, self.plan, output, BUILDER)
        self.assertEqual(libs.inventory(stage), libs.inventory(self.work / 'stage'))
        releases.verify_bundle(output / 'releases' / self.package['release'], self.package,
                               self.plan, root=ROOT)
        self.assertFalse((output / 'verified-cache-updated').exists())

    def test_actual_build_loop_skips_source_checkout_and_compilation_on_cache_hit(self):
        self.save()
        output = self.fresh_output()
        plan = self.plan
        def command(args, **kwargs):
            if args[:3] == ['docker', 'image', 'inspect']:
                if args[-1] == '{{json .Config.Labels}}':
                    return json.dumps({'org.mochios.libs.runner-sha256': libs.runner_identity(ROOT)})
                return BUILDER
            if args[:2] == ['docker', 'pull']: return ''
            self.fail('Cache hit attempted a build command: ' + str(args))
        with patch.dict(os.environ, MOCHIOS_LIBS_VERIFIED_CACHE='1'), \
             patch.object(libs, 'sdk_lock', return_value=plan['sdk']), \
             patch.object(libs.shutil, 'which', return_value='/fixture/docker'), \
             patch.object(libs, 'run', side_effect=command), \
             patch.object(libs, 'install_sdk', return_value=self.work / 'sdk'), \
             patch.object(libs, 'validate_sdk'), \
             patch.object(libs, 'checkout_source') as checkout:
            libs.build(ROOT, plan, output, None)
            checkout.assert_not_called()

    def test_corruption_is_rejected_before_extraction(self):
        cached = self.save()
        (cached / releases.filenames(self.package)[0]).write_bytes(b'corrupted archive')
        output = self.fresh_output()
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            releases.restore_build_cache(ROOT, self.package, self.plan, output, BUILDER)
        self.assertFalse((output / 'cache-stages').exists())

    def test_mismatched_sdk_and_builder_are_rejected(self):
        self.save()
        output = self.fresh_output()
        plan = copy.deepcopy(self.plan)
        plan['sdk']['version'] += '-different'
        with self.assertRaisesRegex(ValueError, 'frozen'):
            releases.restore_build_cache(ROOT, self.package, plan, output, BUILDER)
        with self.assertRaisesRegex(ValueError, 'builder identity'):
            releases.restore_build_cache(ROOT, self.package, self.plan, output, 'sha256:' + '2' * 64)
        self.assertFalse((output / 'cache-stages').exists())

    def test_cache_miss_does_not_create_a_stage_or_claim_success(self):
        self.assertIsNone(releases.restore_build_cache(ROOT, self.package, self.plan,
                                                       self.work / 'empty', BUILDER))
        self.assertFalse((self.work / 'empty').exists())

    def test_incomplete_bundle_is_never_saved_or_marked(self):
        (self.output / 'releases' / self.package['release'] / releases.filenames(self.package)[0]).unlink()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.save()
        self.assertFalse((self.output / 'verified-cache-updated').exists())
        self.assertEqual(list((self.output / 'verified-cache').iterdir()), [])

    def test_copy_failure_never_exposes_a_partial_cache_bundle(self):
        original = shutil.copy2
        def corrupt_copy(source, destination, **kwargs):
            result = original(source, destination, **kwargs)
            if str(source).endswith('.tar.gz'):
                Path(destination).write_bytes(b'incomplete cached copy')
            return result
        with patch.object(releases.shutil, 'copy2', side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                self.save()
        self.assertEqual(list((self.output / 'verified-cache').iterdir()), [])
        self.assertFalse((self.output / 'verified-cache-updated').exists())
        self.assertEqual(list(self.output.glob('cache-copy-*')), [])

    def test_symlink_asset_and_root_are_rejected_without_following_them(self):
        cache = self.save()
        asset = cache / releases.filenames(self.package)[2]
        outside = self.work / 'outside'
        outside.write_text('untouched')
        asset.unlink()
        asset.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'regular files'):
            releases.restore_build_cache(ROOT, self.package, self.plan, self.output, BUILDER)
        self.assertEqual(outside.read_text(), 'untouched')
        output = self.work / 'symlink-output'
        output.mkdir()
        (output / 'verified-cache').symlink_to(self.output / 'verified-cache', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            releases.prepare_build_cache(self.plan, output)

    def test_old_inputs_are_pruned_but_symlink_entries_are_not_deleted(self):
        self.save()
        cache = self.output / 'verified-cache'
        old = cache / ('f' * 64)
        old.mkdir()
        (old / 'old.tar.gz').write_text('stale')
        releases.prepare_build_cache(self.plan, self.output)
        self.assertFalse(old.exists())
        self.assertTrue((cache / self.package['input_id']).is_dir())
        outside = self.work / 'outside-directory'
        outside.mkdir()
        (cache / ('e' * 64)).symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            releases.prepare_build_cache(self.plan, self.output)
        self.assertTrue(outside.is_dir())


class CacheInvalidationAndWorkflow(unittest.TestCase):
    def test_webkit_patch_does_not_invalidate_mesa_but_sdk_invalidates_both(self):
        catalog = copy.deepcopy(libs.read_catalog(ROOT))
        for name in ['mesa', 'epoxy', 'wpewebkit']:
            catalog['libraries'][name]['enabled'] = True
        with patch.object(libs, 'read_catalog', return_value=catalog):
            before = libs.make_plan(ROOT)
            original = libs.hash_file
            with patch.object(libs, 'hash_file', side_effect=lambda p: 'a' * 64
                    if p == ROOT / 'recipes/wpewebkit/mochios.patch' else original(p)):
                after = libs.make_plan(ROOT)
            with patch.object(libs, 'hash_file', side_effect=lambda p: 'c' * 64
                    if p == ROOT / 'recipes/pcre2/probe.c' else original(p)):
                new_dependency = libs.make_plan(ROOT)
            sdk = copy.deepcopy(before['sdk'])
            sdk['sha256'] = 'b' * 64
            with patch.object(libs, 'sdk_lock', return_value=sdk):
                new_sdk = libs.make_plan(ROOT)
        ids = lambda plan: {p['name']: p['input_id'] for p in plan['packages']}
        self.assertEqual(ids(before)['mesa'], ids(after)['mesa'])
        self.assertNotEqual(ids(before)['wpewebkit'], ids(after)['wpewebkit'])
        for name in ['mesa', 'epoxy', 'wpewebkit']:
            self.assertNotEqual(ids(before)[name], ids(new_sdk)[name])
            self.assertNotEqual(ids(before)[name], ids(new_dependency)[name])

    def test_workflow_keeps_completed_bundles_after_failure_without_caching_build_trees(self):
        workflow = (ROOT / '.github/workflows/libraries.yml').read_text()
        self.assertIn('actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830', workflow)
        self.assertIn('actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830', workflow)
        self.assertIn("!cancelled() && inputs.sdk_candidate == true && hashFiles('out/verified-cache-updated') != ''", workflow)
        self.assertEqual(workflow.count('path: out/verified-cache'), 2)
        self.assertIn("libs.identity(plan['sdk'])", workflow)
        self.assertIn('MOCHIOS_LIBS_VERIFIED_CACHE:', workflow)


if __name__ == '__main__':
    unittest.main()
