"""Tests use disposable fixtures only; they never build or install into a mochiOS checkout."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import libs
import releases
import container_build


class RegistryTests(unittest.TestCase):
    def test_real_catalog_and_offline_plan_are_valid_and_explicitly_blocked(self):
        catalog = libs.read_catalog()
        self.assertEqual(libs.order(catalog), ['zlib', 'libpng'])
        self.assertFalse(catalog['libraries']['webkit']['enabled'])
        plan = libs.make_plan(ROOT)
        libs.validate_plan(ROOT, plan)
        self.assertFalse(plan['ready'])
        self.assertIn('AppCore', plan['blocked_reason'])

    def test_annotated_tags_are_peeled_and_versions_sort_numerically(self):
        text = '\n'.join([f'{"a"*40}\trefs/tags/v1.9.0', f'{"b"*40}\trefs/tags/v1.10.0', f'{"c"*40}\trefs/tags/v1.10.0^{{}}', f'{"d"*40}\trefs/tags/v2.0.0-rc1'])
        selected = libs.parse_tags(text, r'^v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$')
        self.assertEqual(selected, dict(tag='v1.10.0', version='1.10.0', commit='c'*40))

    def test_missing_or_ambiguous_versions_fail(self):
        for text in ['', f'{"a"*40}\trefs/tags/v1.0.0\n{"b"*40}\trefs/tags/1.0.0']:
            with self.assertRaises(ValueError):
                libs.parse_tags(text, r'^v?(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$')

    def test_cycles_and_unported_dependencies_fail(self):
        catalog = libs.read_catalog()
        catalog['libraries']['zlib']['dependencies'] = ['libpng']
        with self.assertRaisesRegex(ValueError, 'cycle'): libs.order(catalog)
        catalog['libraries']['zlib']['dependencies'] = ['webkit']
        with self.assertRaisesRegex(ValueError, 'unavailable'): libs.order(catalog)

    def test_plan_tampering_and_recipe_identity_mismatch_fail(self):
        plan = libs.make_plan(ROOT)
        for mutate in [lambda p: p['packages'][0].update(input_id='0'*64), lambda p: p['sources']['zlib'].update(commit='0'*40), lambda p: p['packages'].reverse()]:
            altered = copy.deepcopy(plan); mutate(altered)
            with self.assertRaises(ValueError): libs.validate_plan(ROOT, altered)

    def test_upstream_tag_movement_is_not_silently_adopted(self):
        plan = libs.make_plan(ROOT)
        selected = plan['sources']['zlib']
        text = f'{"0"*40}\trefs/tags/{selected["tag"]}'
        with patch.object(libs, 'run', return_value=text):
            with self.assertRaisesRegex(ValueError, 'moved'): libs.make_plan(ROOT, refresh=True)

    def test_unavailable_sdk_stops_before_sources_or_containers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(libs, 'run') as command:
            with self.assertRaises(libs.Blocked): libs.build(ROOT, libs.make_plan(ROOT), Path(tmp), None)
            command.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])


class SchedulingTests(unittest.TestCase):
    def test_unavailable_sdk_does_not_query_or_publish_releases(self):
        with patch.object(releases, 'release_metadata') as api:
            self.assertFalse(releases.needs_build(libs.make_plan(ROOT)))
            api.assert_not_called()

    def test_new_release_requires_a_build(self):
        plan=libs.make_plan(ROOT); plan['sdk']['available']=True
        with patch.object(releases, 'release_metadata', return_value=None):
            self.assertTrue(releases.needs_build(plan))

    def test_published_inputs_skip_build_but_changed_inputs_require_new_revision(self):
        plan=libs.make_plan(ROOT); plan['sdk']['available']=True
        def metadata(tag):
            package=next(p for p in plan['packages'] if p['release']==tag)
            return {'draft':False,'assets':[{'name':name} for name in releases.filenames(package)]}
        def manifest(package): return {'package':package,'sdk':plan['sdk']}
        with patch.object(releases,'release_metadata',side_effect=metadata), patch.object(releases,'read_published_manifest',side_effect=manifest):
            self.assertFalse(releases.needs_build(plan))
        with patch.object(releases,'release_metadata',side_effect=metadata), patch.object(releases,'read_published_manifest',return_value={}):
            with self.assertRaisesRegex(ValueError,'increment revision'): releases.needs_build(plan)

    def test_moved_baseline_tag_is_rejected_even_if_a_newer_tag_exists(self):
        plan=libs.make_plan(ROOT); old=plan['sources']['zlib']
        response=f'{"0"*40}\trefs/tags/{old["tag"]}\n{"1"*40}\trefs/tags/v99.0.0'
        with patch.object(libs,'run',return_value=response):
            with self.assertRaisesRegex(ValueError,'moved'): libs.make_plan(ROOT,refresh=True)


class SDKContractTests(unittest.TestCase):
    def test_appcore_or_incomplete_sdk_is_not_accepted_as_shared_toolchain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); lock={'version':'fixture'}
            manifest={'format':1,'version':'fixture','target':libs.TARGET,'abi':libs.ABI,'features':[]}
            libs.write_json(root/'sdk.json',manifest)
            with self.assertRaises(libs.Blocked): libs.validate_sdk(root,lock)
            manifest['features']=['pic','shared','cxx23','threads','exceptions']
            manifest['cmake_toolchain']='missing.cmake'; manifest['sysroot']='sysroot'
            libs.write_json(root/'sdk.json',manifest)
            with self.assertRaises(libs.Blocked): libs.validate_sdk(root,lock)
            manifest['cmake_toolchain']='../outside.cmake'; libs.write_json(root/'sdk.json',manifest)
            with self.assertRaises(ValueError): libs.validate_sdk(root,lock)


class IsolationTests(unittest.TestCase):
    def test_paths_cannot_escape_through_parent_components_or_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'; root.mkdir()
            (root / 'outside').symlink_to(Path(tmp))
            for name in ['../escape', '/absolute', 'outside/escape', 'back\\slash']:
                with self.assertRaises(ValueError): libs.relative(root, name)

    def test_credential_urls_and_insecure_redirects_are_rejected(self):
        self.assertTrue(libs.https_url('https://example.invalid/sdk.tar.zst'))
        for url in ['http://example.invalid/sdk', 'https://user:secret@example.invalid/sdk', 'https://example.invalid/sdk?token=secret', 'https://example.invalid/sdk#floating']:
            self.assertFalse(libs.https_url(url))
        with self.assertRaisesRegex(ValueError,'Insecure'):
            libs.HTTPSRedirects().redirect_request(None,None,302,'',{},'http://example.invalid/sdk')

    def test_container_mounts_only_four_explicit_paths_and_has_no_network_or_socket(self):
        root = Path('/independent-libs/out')
        command = libs.docker_command('sha256:'+'a'*64, root/'sdk', root/'source', root/'deps', root/'work')
        self.assertIn('--network=none', command)
        self.assertIn('--read-only', command)
        self.assertIn('--cap-drop=ALL', command)
        mounts = [command[i+1] for i, item in enumerate(command) if item == '--mount']
        self.assertEqual(len(mounts), 4)
        self.assertEqual(sum(mount.endswith(',readonly') for mount in mounts), 3)
        self.assertNotIn('/var/run/docker.sock', ' '.join(command))
        self.assertNotIn('/Develop/mochiOS/', ' '.join(command))

    def test_build_environment_does_not_receive_host_credentials_or_search_paths(self):
        with patch.dict(os.environ, {'GH_TOKEN':'fixture', 'GITHUB_TOKEN':'fixture', 'SDK_READ_TOKEN':'fixture', 'SSH_AUTH_SOCK':'fixture', 'CPATH':'/host', 'CXXFLAGS':'-I/host'}):
            env = libs.clean_environment()
        for key in ['GH_TOKEN','GITHUB_TOKEN','SDK_READ_TOKEN','SSH_AUTH_SOCK','CPATH','CXXFLAGS']:
            self.assertNotIn(key, env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')

    def test_unsafe_archives_are_rejected_before_extraction(self):
        for name, kind, link in [('../escape','file',''), ('/absolute','file',''), ('root/link','link','../../escape'), ('root/device','device','')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp)/'test.tar'
                with tarfile.open(archive,'w') as tar:
                    member = tarfile.TarInfo(name)
                    if kind=='link': member.type=tarfile.SYMTYPE; member.linkname=link
                    if kind=='device': member.type=tarfile.CHRTYPE
                    tar.addfile(member)
                with tarfile.open(archive) as tar:
                    with self.assertRaises(ValueError): libs.safe_members(tar)

    def test_conflicting_dependencies_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); one=root/'one'; two=root/'two'; merged=root/'merged'
            for directory,text in [(one,'first'),(two,'different')]:
                (directory/'usr/include').mkdir(parents=True); (directory/'usr/include/shared.h').write_text(text)
            merged.mkdir(); libs.merge_dependency(one,merged)
            with self.assertRaisesRegex(ValueError,'Conflicting'): libs.merge_dependency(two,merged)
            self.assertEqual((merged/'usr/include/shared.h').read_text(),'first')

    def test_external_install_root_is_rejected_without_writing_to_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); outside=root/'outside'; outside.mkdir(); (root/'stage').symlink_to(outside)
            package=libs.make_plan(ROOT)['packages'][0]
            with self.assertRaisesRegex(ValueError,'regular directory'):
                libs.package_build(ROOT, package, root/'stage', {}, root/'releases')
            self.assertEqual(list(outside.iterdir()),[])


@unittest.skipUnless(shutil.which('cc') and shutil.which('readelf'), 'host fixture compiler/readelf required')
class ArtifactTests(unittest.TestCase):
    def fixture(self, root):
        stage=root/'stage'; (stage/'usr/lib').mkdir(parents=True)
        (stage/'usr/include').mkdir(); (stage/'usr/include/zlib.h').write_text('/* fixture only */\n')
        source=root/'fixture.c'; source.write_text('int fixture_only(void) { return 42; }\n')
        # Freestanding fixture ELF, not a mochiOS SDK or library compatibility claim.
        subprocess.run(['cc','-shared','-nostdlib','-fPIC','-Wl,--build-id=none',str(source),'-o',str(stage/'usr/lib/libz.so.1')],check=True)
        (stage/'usr/lib/libz.so').symlink_to('libz.so.1')
        plan=libs.make_plan(ROOT); package=plan['packages'][0]
        libs.package_build(ROOT,package,stage,dict(sdk=plan['sdk'],registry_commit='0'*40,builder={'fixture_only':True}),root/'releases')
        return plan,package,root/'releases'/package['release']

    def test_package_roundtrip_checks_files_links_and_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan,package,directory=self.fixture(Path(tmp))
            manifest=releases.verify_bundle(directory,package,plan)
            self.assertFalse(manifest['runtime_tested'])
            self.assertTrue(any('link' in item for item in manifest['files']))

    def test_tampered_archive_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan,package,directory=self.fixture(Path(tmp))
            archive=directory/releases.filenames(package)[0]
            archive.write_bytes(archive.read_bytes()+b'tampered')
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                releases.verify_bundle(directory,package,plan)

    def test_recomputed_outer_hash_cannot_hide_a_different_source_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan,package,directory=self.fixture(Path(tmp))
            archive,metadata,checksums=releases.filenames(package)
            manifest=json.loads((directory/metadata).read_text()); manifest['package']['source']['commit']='0'*40
            libs.write_json(directory/metadata,manifest)
            (directory/checksums).write_text(''.join(f'{libs.hash_file(directory/name)}  {name}\n' for name in [archive,metadata]))
            with self.assertRaisesRegex(ValueError,'frozen'):
                releases.verify_bundle(directory,package,plan)

    def test_host_glibc_dependent_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'host.c'; source.write_text('#include <stdio.h>\nvoid fixture(void) { puts("host"); }\n')
            binary=root/'host.so'; subprocess.run(['cc','-shared','-fPIC',str(source),'-o',str(binary)],check=True)
            with self.assertRaisesRegex(ValueError,'glibc'): container_build.validate_elf(binary,shared=True)

    def test_no_sdk_means_publish_does_not_call_github(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(releases,'release_metadata') as github:
            with self.assertRaises(libs.Blocked): releases.publish(ROOT,libs.make_plan(ROOT),Path(tmp))
            github.assert_not_called()


class ToolchainTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cmake') and shutil.which('cc') and shutil.which('c++'), 'CMake/host compiler required')
    def test_host_toolchain_cannot_satisfy_the_sdk_link_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); toolchain=root/'wrong.cmake'; toolchain.write_text('set(CMAKE_SYSTEM_NAME Linux)\n')
            result=subprocess.run(['cmake','-S',str(ROOT/'tooling/probe'),'-B',str(root/'build'),'-DCMAKE_TOOLCHAIN_FILE='+str(toolchain)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('real mochiOS cross toolchain',result.stderr)


if __name__ == '__main__': unittest.main()
