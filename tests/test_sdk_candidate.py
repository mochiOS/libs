"""Candidate SDKs use mocked Actions receipts and disposable archives, never a host toolchain."""
import copy
import io
import json
import shutil
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import libs
import sdk_candidate
import releases


class CandidateSDKTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / 'tooling/sdk-candidate.json').read_text())
        self.run = dict(id=self.policy['run_id'], head_sha=self.policy['head_sha'],
                        event='workflow_dispatch', status='completed', conclusion='success',
                        path='.github/workflows/sdk.yml',
                        repository={'full_name': 'mochiOS/devkit'}, head_repository={'full_name': 'mochiOS/devkit'})

    def test_candidate_enables_only_declared_ports_without_changing_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / 'recipes', root / 'recipes')
            shutil.copytree(ROOT / 'engines', root / 'engines')
            shutil.copy2(ROOT / 'libraries.toml', root / 'libraries.toml')
            before = (root / 'libraries.toml').read_text()
            self.assertFalse(libs.read_catalog(root)['libraries']['mesa']['enabled'])
            sdk_candidate.activate_ports(root, ['mesa'])
            catalog = libs.read_catalog(root)
            self.assertTrue(catalog['libraries']['mesa']['enabled'])
            self.assertFalse(catalog['libraries']['epoxy']['enabled'])
            sdk_candidate.activate_ports(root, ['mesa'])
            for names in [['unknown'], ['mesa', 'mesa'], ['../mesa']]:
                with self.assertRaises(ValueError): sdk_candidate.activate_ports(root, names)
            self.assertNotEqual(before, (root / 'libraries.toml').read_text())

    def test_only_reviewed_successful_non_fork_sdk_run_is_accepted(self):
        sdk_candidate.validate_run(self.run, self.policy)
        for key, value in [('id', 1), ('head_sha', 'a'*40), ('event', 'pull_request'),
                           ('status', 'in_progress'), ('conclusion', 'failure'), ('path', 'other.yml'),
                           ('head_repository', {'full_name': 'attacker/devkit'}),
                           ('repository', {'full_name': 'attacker/devkit'})]:
            run = dict(self.run, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                sdk_candidate.validate_run(run, self.policy)

    def fixture(self, directory):
        name = 'x86_64-mochios-sdk-' + self.policy['version'] + '.tar.gz'
        sdk = dict(format=1, version=self.policy['version'], target=libs.TARGET, abi=libs.ABI,
                   features=self.policy['features'])
        with tarfile.open(directory / name, 'w:gz') as tar:
            for path, content in [('sdk.json', sdk), ('share/runtime-symbols.json', self.policy['exports'])]:
                data = json.dumps(content).encode()
                member = tarfile.TarInfo('sdk/' + path); member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        manifest = dict(format=1, archive=name, sha256=libs.hash_file(directory / name), sdk=sdk,
                        path_audit={'passed': True}, sources=dict(version=self.policy['version'],
                        sources={name: {'commit': commit} for name, commit in self.policy['sources'].items()}))
        path = directory / (name + '.json')
        path.write_text(json.dumps(manifest))
        return path, manifest

    def test_digest_audit_sources_and_real_exports_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, manifest = self.fixture(directory)
            sdk_candidate.validate_artifact(directory, self.policy)
            for key, value in [('sha256', '0'*64), ('path_audit', {'passed': False}),
                               ('sources', {'version': self.policy['version'], 'sources': {}}), ('sdk', {})]:
                path.write_text(json.dumps(dict(manifest, **{key: value})))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    sdk_candidate.validate_artifact(directory, self.policy)
            path.write_text(json.dumps(manifest))
            for key in ['features', 'exports']:
                policy = copy.deepcopy(self.policy); policy[key].append('absent')
                with self.subTest(key=key), self.assertRaises(ValueError):
                    sdk_candidate.validate_artifact(directory, policy)

    def test_development_sdk_publication_is_blocked_before_network_or_plan_validation(self):
        with patch.object(libs, 'validate_plan', side_effect=AssertionError('must reject first')):
            with self.assertRaisesRegex(libs.Blocked, 'cannot be published'):
                releases.publish(ROOT, {'sdk': {'development_only': True}}, Path('/missing'))

    def test_candidate_does_not_reuse_or_compare_published_release_assets(self):
        plan = {'sdk': {'development_only': True, 'available': True}}
        with patch.object(releases, 'release_metadata', side_effect=AssertionError('no published cache')):
            self.assertTrue(releases.needs_build(plan))
            self.assertIsNone(releases.restore_existing(ROOT, {}, plan, Path('/missing')))

    def test_release_workflow_never_approves_or_publishes_candidate_builds(self):
        workflow = (ROOT / '.github/workflows/libraries.yml').read_text()
        self.assertIn("group: library-${{ inputs.sdk_candidate == true && 'verification' || 'releases' }}", workflow)
        self.assertIn('python3 tools/sdk_candidate.py fetch', workflow)
        self.assertIn('python3 tools/sdk_candidate.py restore', workflow)
        for job in ['release-policy', 'release']:
            body = workflow.split('\n  ' + job + ':', 1)[1].split('    steps:', 1)[0]
            self.assertIn('inputs.sdk_candidate != true', body)
