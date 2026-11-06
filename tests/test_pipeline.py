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
import build_backends


UNAVAILABLE_SDK = dict(format=1, available=False, target=libs.TARGET, abi=libs.ABI, reason='SDK unavailable test fixture')


class RegistryTests(unittest.TestCase):
    def test_real_catalog_and_offline_plan_follow_the_sdk_lock(self):
        catalog = libs.read_catalog()
        self.assertEqual(libs.order(catalog), ['zlib', 'libpng', 'pcre2', 'libffi', 'libiconv', 'gettext-runtime', 'glib', 'libjpeg-turbo', 'libwebp', 'brotli', 'libxml2', 'freetype', 'nghttp2', 'sqlite', 'libunistring', 'libidn2', 'libpsl', 'expat', 'fontconfig', 'icu', 'harfbuzz', 'libgpg-error', 'libgcrypt', 'libtasn1'])
        self.assertFalse(catalog['libraries']['webkit']['enabled'])
        self.assertTrue(catalog['libraries']['glib']['enabled'])
        plan = libs.make_plan(ROOT)
        libs.validate_plan(ROOT, plan)
        self.assertEqual(plan['ready'], libs.sdk_lock(ROOT)['available'])
        self.assertNotIn('glib', plan['blocked_ports'])
        if not plan['ready']: self.assertTrue(plan['blocked_reason'])

    def test_annotated_tags_are_peeled_and_versions_sort_numerically(self):
        text = '\n'.join([f'{"a"*40}\trefs/tags/v1.9.0', f'{"b"*40}\trefs/tags/v1.10.0', f'{"c"*40}\trefs/tags/v1.10.0^{{}}', f'{"d"*40}\trefs/tags/v2.0.0-rc1'])
        selected = libs.parse_tags(text, r'^v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$')
        self.assertEqual(selected, dict(tag='v1.10.0', version='1.10.0', commit='c'*40))

    def test_two_component_upstream_versions_are_compared_numerically(self):
        self.assertEqual(libs.version('10.49'), libs.version('10.49.0'))
        self.assertLess(libs.version('10.9'), libs.version('10.49'))
        selected = libs.parse_tags('a'*40 + '\trefs/tags/pcre2-10.49', r'^pcre2-(?P<version>[0-9]+\.[0-9]+)$')
        self.assertEqual(selected['version'], '10.49')

    def test_upstream_separator_versions_are_canonical_and_annotated_tags_are_peeled(self):
        text = 'a'*40+'\trefs/tags/VER-2-15-0\n'+'b'*40+'\trefs/tags/VER-2-15-0^{}'
        selected = libs.parse_tags(text, r'^VER-(?P<version>[0-9]+-[0-9]+-[0-9]+)$')
        self.assertEqual(selected, {'tag':'VER-2-15-0','version':'2.15.0','commit':'b'*40})
        self.assertEqual(libs.version('2_15_0'), libs.version('2.15.0'))
        with self.assertRaises(ValueError): libs.version('2.15.0-rc1')

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

    @patch.object(libs, 'sdk_lock', return_value=UNAVAILABLE_SDK)
    def test_unavailable_sdk_stops_before_sources_or_containers(self, _sdk):
        with tempfile.TemporaryDirectory() as tmp, patch.object(libs, 'run') as command:
            with self.assertRaises(libs.Blocked): libs.build(ROOT, libs.make_plan(ROOT), Path(tmp), None)
            command.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])


class ReleaseMetadataTests(unittest.TestCase):
    def test_draft_assets_remain_visible_before_tag_creation(self):
        value={'isDraft':True,'assets':[{'name':'fixture.tar.gz','digest':'sha256:'+'a'*64}]}
        result=subprocess.CompletedProcess([],0,json.dumps(value),'')
        with patch.object(releases.subprocess,'run',return_value=result):
            self.assertEqual(releases.release_metadata('fixture'),{'draft':True,'assets':value['assets']})

    def test_only_explicit_missing_release_is_absence(self):
        with patch.object(releases.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','release not found\n')):
            self.assertIsNone(releases.release_metadata('missing'))
        for error in ['HTTP 403: forbidden','timeout connecting to api.github.com']:
            with self.subTest(error=error), patch.object(releases.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'',error)):
                with self.assertRaisesRegex(ValueError,'Cannot read'):releases.release_metadata('fixture')


class SchedulingTests(unittest.TestCase):
    @patch.object(libs, 'sdk_lock', return_value=UNAVAILABLE_SDK)
    def test_unavailable_sdk_does_not_query_or_publish_releases(self, _sdk):
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
    def setUp(self):
        # These fixtures contain bytes, not an SDK/source distribution. Their
        # security assertions must not depend on the developer's free space.
        budget = patch.object(shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(16*1024**3, 0, 16*1024**3))
        budget.start()
        self.addCleanup(budget.stop)

    def test_sdk_archives_support_root_and_one_enclosing_directory(self):
        for prefix in ['.', 'sdk']:
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); source=root/'source'; source.mkdir(); (source/'sysroot').mkdir()
                (source/'toolchain.cmake').write_text('# fixture only\n')
                libs.write_json(source/'sdk.json',dict(format=1, version='fixture', target=libs.TARGET, abi=libs.ABI,
                    features=['pic','shared','cxx23','threads','exceptions'],cmake_toolchain='toolchain.cmake',sysroot='sysroot'))
                archive=root/'input.tar.gz'
                with tarfile.open(archive,'w:gz') as tar: tar.add(source,arcname=prefix)
                output=root/'out'; (output/'downloads').mkdir(parents=True)
                digest=libs.hash_file(archive); shutil.copy2(archive,output/'downloads'/digest)
                lock=dict(available=True,version='fixture',sha256=digest,url='https://example.invalid/sdk.tar.gz')
                installed=libs.install_sdk(ROOT,output,lock)
                self.assertTrue((installed/'sdk.json').is_file())
                self.assertTrue((installed/'sysroot').is_dir())
                self.assertEqual(libs.install_sdk(ROOT,output,lock),installed)

    def test_enclosing_sdk_link_cannot_escape_after_flattening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'sdk'; source.mkdir(); (source/'escape').symlink_to('..')
            (source/'sdk.json').write_text('{}')
            archive=root/'input.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:tar.add(source,arcname='sdk')
            output=root/'out'; (output/'downloads').mkdir(parents=True)
            digest=libs.hash_file(archive);shutil.copy2(archive,output/'downloads'/digest)
            lock=dict(available=True,version='fixture',sha256=digest,url='https://example.invalid/sdk.tar.gz')
            with self.assertRaisesRegex(ValueError,'symlink escapes'):libs.install_sdk(ROOT,output,lock)


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

    def test_dependency_merge_ignores_installed_libtool_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source'; merged=root/'merged'
            (source/'usr/lib/pkgconfig').mkdir(parents=True); merged.mkdir()
            (source/'usr/lib/libexample.la').write_text("dependency_libs='/usr/lib/libother.la'\n")
            (source/'usr/lib/pkgconfig/example.pc').write_text('prefix=/usr\n')
            libs.merge_dependency(source,merged)
            self.assertFalse((merged/'usr/lib/libexample.la').exists())
            self.assertTrue((source/'usr/lib/libexample.la').exists())
            self.assertEqual((merged/'usr/lib/pkgconfig/example.pc').read_text(),'prefix=/usr\n')

    def test_external_install_root_is_rejected_without_writing_to_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); outside=root/'outside'; outside.mkdir(); (root/'stage').symlink_to(outside)
            package=libs.make_plan(ROOT)['packages'][0]
            with self.assertRaisesRegex(ValueError,'regular directory'):
                libs.package_build(ROOT, package, root/'stage', {}, root/'releases')
            self.assertEqual(list(outside.iterdir()),[])


@unittest.skipUnless(shutil.which('cc') and shutil.which('readelf'), 'host fixture compiler/readelf required')
class ArtifactTests(unittest.TestCase):
    def fixture(self, root, name='zlib', helpers=None, libtool=False):
        stage=root/'stage'; (stage/'usr/lib').mkdir(parents=True)
        (stage/'usr/include').mkdir(); (stage/'usr/include/zlib.h').write_text('/* fixture only */\n')
        source=root/'fixture.c'; source.write_text('int fixture_only(void) { return 42; }\n')
        # Freestanding fixture ELF, not a mochiOS SDK or library compatibility claim.
        subprocess.run(['cc','-shared','-nostdlib','-fPIC','-Wl,--build-id=none',str(source),'-o',str(stage/'usr/lib/libz.so.1')],check=True)
        (stage/'usr/lib/libz.so').symlink_to('libz.so.1')
        if helpers:
            (stage/'usr/bin').mkdir()
            for helper,content in helpers.items():(stage/'usr/bin'/helper).write_bytes(content)
        if libtool:
            (stage/'usr/lib/libz.la').write_text("dependency_libs='/private/build/libother.la'\n")
        plan=libs.make_plan(ROOT); package=next(item for item in plan['packages'] if item['name']==name)
        libs.package_build(ROOT,package,stage,dict(sdk=plan['sdk'],registry_commit='0'*40,builder={'fixture_only':True}),root/'releases')
        return plan,package,root/'releases'/package['release']

    def test_package_excludes_libtool_build_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); plan,package,directory=self.fixture(root,libtool=True)
            manifest=releases.verify_bundle(directory,package,plan)
            paths={entry['path'] for entry in manifest['files']}
            self.assertIn('usr/lib/libz.so.1',paths)
            self.assertNotIn('usr/lib/libz.la',paths)
            self.assertFalse((root/'stage/usr/lib/libz.la').exists())

    def test_only_declared_posix_config_helpers_can_be_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan,package,directory=self.fixture(Path(tmp),name='libpng',helpers={'libpng16-config':b'#! /bin/sh\nprintf fixture\n'})
            releases.verify_bundle(directory,package,plan)
        for helper,content in [('unexpected-config',b'#!/bin/sh\n'),('libpng16-config',b'\x7fELFnot-a-script')]:
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):self.fixture(Path(tmp),name='libpng',helpers={helper:content})

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

    @patch.object(libs, 'sdk_lock', return_value=UNAVAILABLE_SDK)
    def test_no_sdk_means_publish_does_not_call_github(self, _sdk):
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


class SubmoduleTests(unittest.TestCase):
    def test_gitlinks_and_relative_upstream_urls_must_match_reviewed_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.gitmodules').write_text('[submodule "subprojects/gvdb"]\npath = subprojects/gvdb\nurl = ../../GNOME/gvdb.git\n')
            library = {'git': 'https://gitlab.gnome.org/GNOME/glib.git', 'submodules': {
                'subprojects/gvdb': {'git': 'https://gitlab.gnome.org/GNOME/gvdb.git', 'commit': 'a'*40}}}
            output = '160000 commit ' + 'a'*40 + '\tsubprojects/gvdb'
            with patch.object(libs, 'run', return_value=output):
                self.assertEqual(libs.validate_submodules(root, library), library['submodules'])
                with self.assertRaisesRegex(ValueError, 'explicit submodule'):
                    libs.validate_submodules(root, {'git': library['git']})
                altered = copy.deepcopy(library)
                altered['submodules']['subprojects/gvdb']['commit'] = 'b'*40
                with self.assertRaisesRegex(ValueError, 'revision or URL changed'):
                    libs.validate_submodules(root, altered)
                altered = copy.deepcopy(library)
                altered['submodules']['subprojects/gvdb']['git'] = 'https://example.invalid/gvdb.git'
                with self.assertRaisesRegex(ValueError, 'revision or URL changed'):
                    libs.validate_submodules(root, altered)

    def test_unexpected_gitmodules_and_nested_dependencies_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.gitmodules').write_text('[submodule "extra"]\npath = extra\nurl = https://example.invalid/extra.git\n')
            with patch.object(libs, 'run', return_value=''):
                with self.assertRaisesRegex(ValueError, 'definitions differ'):
                    libs.validate_submodules(root, {'git':'https://example.invalid/base.git'})


class CrossBackendTests(unittest.TestCase):
    def test_meson_machine_uses_mochios_and_cannot_execute_target_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'cross.ini'
            build_backends.cross_file(Path('/sdk'), Path('/sdk/sysroot'), Path('/deps'), output)
            text = output.read_text()
            self.assertIn("system = 'mochios'", text)
            self.assertIn('needs_exe_wrapper = true', text)
            self.assertNotIn('exe_wrapper = [', text)
            self.assertIn("'/sdk/bin/mochios-cc'", text)
            self.assertIn("'/deps/usr/lib/pkgconfig'", text)
            self.assertNotIn("'/usr/lib/pkgconfig'", text)

    def test_nested_dependencies_are_included_in_consumer_prefixes(self):
        catalog = {'libraries': {
            'a': {'enabled': True, 'dependencies': []},
            'b': {'enabled': True, 'dependencies': ['a']},
            'c': {'enabled': True, 'dependencies': ['b']}}}
        self.assertEqual(libs.transitive_dependencies(catalog, 'c'), ['a', 'b'])

    def test_autotools_commands_always_use_the_target_driver_and_explicit_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            spec = {'backend': 'autotools', 'configure':['--disable-docs']}
            with patch.object(build_backends, 'invoke') as invoke:
                build_backends.build(spec, Path('/source'), work, Path('/sdk'), Path('/sdk/sysroot'), Path('/deps'), [], {})
            command, env = invoke.call_args_list[0].args
            self.assertIn('--host=x86_64-unknown-mochios', command)
            self.assertEqual(env['CC'], '/sdk/bin/mochios-cc')
            self.assertEqual(env['LD'], '/sdk/host/bin/ld.lld')
            self.assertNotIn('ac_cv_func_malloc_0_nonnull', env)


class SourceDistributionTests(unittest.TestCase):
    def setUp(self):
        # These fixtures contain bytes, not an SDK/source distribution. Their
        # security assertions must not depend on the developer's free space.
        budget = patch.object(shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(16*1024**3, 0, 16*1024**3))
        budget.start()
        self.addCleanup(budget.stop)

    def test_reviewed_distribution_hash_is_preserved_and_cannot_be_changed(self):
        import source_archives
        library = {'source_archive': 'https://example.invalid/library-{version}.tar.gz'}
        baseline = {'version':'1.2', 'commit':'a'*40, 'archive':{
            'url':'https://example.invalid/library-1.2.tar.gz', 'sha256':'b'*64}}
        selected = source_archives.resolve(library, baseline, baseline, libs)
        source_archives.validate(library, selected, baseline, libs)
        selected['archive'] = dict(selected['archive'], sha256='c'*64)
        with self.assertRaisesRegex(ValueError, 'Pinned source distribution changed'):
            source_archives.validate(library, selected, baseline, libs)
        for template in ['http://example.invalid/{version}.tar.gz', 'https://example.invalid/{token}/{version}.tar.gz']:
            with self.assertRaises(ValueError): source_archives.release_url({'source_archive':template}, '1.2', libs)

    def test_official_archive_can_use_dash_and_underscore_versions(self):
        import source_archives
        library = {'source_archive': 'https://github.com/unicode-org/icu/releases/download/release-{version_dash}/icu4c-{version_underscore}-src.tgz'}
        self.assertEqual(source_archives.release_url(library, '77.1', libs),
                         'https://github.com/unicode-org/icu/releases/download/release-77-1/icu4c-77_1-src.tgz')
        for token in ['{version.__class__}', '{version[0]}', '{version!r}', '{version:10}', '{token}']:
            with self.subTest(token=token), self.assertRaises(ValueError):
                source_archives.release_url({'source_archive': 'https://example.invalid/'+token}, '77.1', libs)
        for version in ['../77.1', '77.1?secret=1', '77_1', 'v77.1']:
            with self.subTest(version=version), self.assertRaises(ValueError):
                source_archives.release_url(library, version, libs)

    def test_official_archive_can_repeat_a_numeric_version(self):
        import source_archives
        library = {'source_archive': 'https://github.com/harfbuzz/harfbuzz/releases/download/{version}/harfbuzz-{version}.tar.xz'}
        self.assertEqual(source_archives.release_url(library, '15.1.0', libs),
                         'https://github.com/harfbuzz/harfbuzz/releases/download/15.1.0/harfbuzz-15.1.0.tar.xz')

    def test_distribution_cache_is_checked_against_its_complete_inventory(self):
        import source_archives
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'library-1.2'; source.mkdir()
            (source/'configure').write_text('# source fixture; never executed\n')
            archive = root/'source.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar: tar.add(source, arcname=source.name)
            digest = libs.hash_file(archive)
            downloads = root/'out/downloads'; downloads.mkdir(parents=True)
            shutil.copy2(archive, downloads/digest)
            library = {'source_archive':'https://example.invalid/library-{version}.tar.gz'}
            selected = {'version':'1.2', 'archive':{'url':'https://example.invalid/library-1.2.tar.gz','sha256':digest}}
            installed = source_archives.checkout(root/'out', library, selected, libs)
            self.assertEqual(source_archives.checkout(root/'out', library, selected, libs), installed)
            (installed/'configure').write_text('modified source')
            with self.assertRaisesRegex(ValueError, 'cache was modified'):
                source_archives.checkout(root/'out', library, selected, libs)


class ConsumerPrefixTests(unittest.TestCase):
    def test_current_library_and_indirect_dependencies_share_one_pkg_config_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); dep = root/'dep'; stage = root/'stage'
            for source in [dep, stage]: (source/'usr/lib').mkdir(parents=True)
            (dep/'usr/lib/libdependency.so').write_bytes(b'dependency fixture')
            (stage/'usr/lib/libcurrent.so').write_bytes(b'current fixture')
            prefix = container_build.merge_prefixes([dep, stage], root/'consumer')
            self.assertTrue((prefix/'usr/lib/libdependency.so').is_file())
            self.assertTrue((prefix/'usr/lib/libcurrent.so').is_file())
            (stage/'usr/lib/libdependency.so').write_bytes(b'different dependency')
            with self.assertRaisesRegex(ValueError, 'Conflicting consumer file'):
                container_build.merge_prefixes([dep, stage], root/'consumer')

    def test_consumer_prefix_rejects_external_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; source.mkdir()
            (source/'escape').symlink_to('..')
            with self.assertRaisesRegex(ValueError, 'External dependency link'):
                container_build.merge_prefixes([source], root/'consumer')


class MesonSyntaxTests(unittest.TestCase):
    def test_generated_cross_file_parses_with_meson_itself(self):
        try:
            from mesonbuild.machinefile import MachineFileParser
        except ImportError:
            self.skipTest('Meson is required for the machine-file grammar integration test')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cross.ini'
            sdk = Path("/sdk's build")
            build_backends.cross_file(sdk, sdk/'sysroot', Path('/deps'), path)
            parsed = MachineFileParser([str(path)], str(Path(tmp))).sections
            self.assertEqual(parsed['binaries']['c'], str(sdk/'bin/mochios-cc'))
            self.assertTrue(parsed['properties']['needs_exe_wrapper'])
            self.assertEqual(parsed['properties']['sys_root'], '/deps')
            self.assertEqual(parsed['properties']['pkg_config_libdir'], ['/deps/usr/lib/pkgconfig', '/deps/usr/share/pkgconfig'])
            self.assertEqual(parsed['built-in options']['c_link_args'], ['-L/deps/usr/lib'])


class MesonTargetPolicyTests(unittest.TestCase):
    def test_mochios_does_not_embed_any_build_or_install_rpath(self):
        try:
            from mesonbuild.linkers.linkers import GnuLikeDynamicLinkerMixin
            from mesonbuild.mesonlib import MachineChoice
        except ImportError:
            self.skipTest('Pinned patched Meson is required for linker policy integration')
        from types import SimpleNamespace
        machine = MachineChoice.HOST
        env = SimpleNamespace(machines={machine:SimpleNamespace(system='mochios')})
        linker = SimpleNamespace(environment=env,for_machine=machine)
        flags, removed = GnuLikeDynamicLinkerMixin.build_rpath_args(linker, '/work/build', '.', object(), ['/deps/usr/lib'])
        self.assertEqual(flags, [])
        self.assertEqual(removed, set())
