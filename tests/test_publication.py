"""Publication uses mocked GitHub only; these tests never publish a release."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import libs
import releases


class ImmutablePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = dict(name='fixture', release='fixture-1.0-r2', stem='fixture-1.0-r2-target',
                            source=dict(tag='v1.0', commit='a'*40))
        self.plan = dict(packages=[self.package], sdk=dict(available=True, version='fixture-sdk'))
        self.artifacts = self.root/'artifacts'
        directory = self.artifacts/self.package['release']
        directory.mkdir(parents=True)
        self.assets = []
        for name in releases.filenames(self.package):
            path = directory/name
            path.write_bytes(b'fixture only: '+name.encode())
            self.assets.append(dict(name=name, digest='sha256:'+libs.hash_file(path)))
        self.calls = []
        def command(args, **kwargs):
            if args[0] == 'git': return 'b'*40
            self.calls.append(list(map(str, args)))
            return ''
        for target, attribute, options in [
            (libs, 'validate_plan', dict(return_value={'distribution':{'prerelease':True}})),
            (releases, 'verify_bundle', dict(return_value={})),
            (libs, 'run', dict(side_effect=command)),
        ]:
            manager = patch.object(target, attribute, **options)
            manager.start()
            self.addCleanup(manager.stop)

    def publish(self, snapshots):
        with patch.object(releases, 'release_metadata', side_effect=snapshots):
            releases.publish(self.root, self.plan, self.artifacts)

    def test_new_revision_creates_draft_uploads_then_publishes_after_digest_check(self):
        self.publish([None, dict(draft=True, assets=self.assets)])
        self.assertIn('--draft', self.calls[0])
        self.assertEqual(self.calls[0][3], 'fixture-1.0-r2')
        uploads = [call for call in self.calls if call[2] == 'upload']
        self.assertEqual([Path(call[4]).name for call in uploads], releases.filenames(self.package))
        self.assertIn('--draft=false', self.calls[-1])
        self.assertIn('--prerelease', self.calls[-1])
        self.assertFalse(any('--clobber' in call for call in self.calls))

    def test_complete_public_revision_is_reused_without_mutation(self):
        self.publish([dict(draft=False, assets=self.assets)])
        self.assertEqual(self.calls, [])

    def test_incomplete_public_revision_never_receives_uploads(self):
        with self.assertRaisesRegex(ValueError, 'never append'):
            self.publish([dict(draft=False, assets=self.assets[:1])])
        self.assertEqual(self.calls, [])

    def test_partial_draft_can_resume_without_overwriting_existing_assets(self):
        self.publish([dict(draft=True, assets=self.assets[:1]), dict(draft=True, assets=self.assets)])
        uploads = [call for call in self.calls if call[2] == 'upload']
        self.assertEqual([Path(call[4]).name for call in uploads], releases.filenames(self.package)[1:])
        self.assertIn('--draft=false', self.calls[-1])

    def test_existing_asset_digest_conflict_prevents_all_writes(self):
        wrong = [dict(self.assets[0], digest='sha256:'+'0'*64)]
        with self.assertRaisesRegex(ValueError, 'never clobber'):
            self.publish([dict(draft=True, assets=wrong)])
        self.assertEqual(self.calls, [])

    def test_failed_readback_digest_never_publishes_draft(self):
        wrong = [dict(asset, digest='sha256:'+'0'*64) for asset in self.assets]
        with self.assertRaisesRegex(ValueError, 'draft remains unpublished'):
            self.publish([None, dict(draft=True, assets=wrong)])
        self.assertFalse(any(call[2] == 'edit' for call in self.calls))
