"""Apply the complete port to full files from the exact locked release archive."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/wpe-2.54.2'


class FullReleasePatch(unittest.TestCase):
    @unittest.skipUnless(shutil.which('patch'), 'GNU patch required')
    def test_all_hunks_apply_without_fuzz_to_complete_release_files(self):
        source = json.loads((FIXTURE / 'source.json').read_text())
        locked = json.loads((ROOT / 'sources.lock.json').read_text())['sources']['wpewebkit']
        self.assertEqual(source['version'], locked['version'])
        self.assertEqual(source['archive_sha256'], locked['archive']['sha256'])
        for name, digest in source['files'].items():
            self.assertEqual(hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest(), digest, name)
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / 'source'
            shutil.copytree(FIXTURE, work)
            recipe = json.loads((ROOT / 'recipes/wpewebkit/recipe.json').read_text())
            for patch in recipe['patches']:
                result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(work),
                                         '-i', str(ROOT / 'recipes/wpewebkit' / patch)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(list(work.rglob('*.rej')))
