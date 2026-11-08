"""Official source tar duplicates never relax SDK or executable-package validation."""
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import libs
import source_archives
class SourceDuplicates(unittest.TestCase):
    def archive(self, entries):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w') as tar:
            for name,body,mode in entries:
                m=tarfile.TarInfo(name);m.size=len(body);m.mode=mode
                tar.addfile(m,io.BytesIO(body))
        data.seek(0);return tarfile.open(fileobj=data)
    def test_identical_regular_source_duplicate_is_deduplicated_only_for_sources(self):
        with self.archive([('source/theme.css',b'body{}',0o644)]*2) as tar:
            self.assertEqual(len(source_archives.source_members(tar,libs)),1)
            with self.assertRaises(ValueError):libs.safe_members(tar)
    def test_conflicts_aliases_unsafe_paths_and_budget_are_rejected(self):
        for entries in [
            [('source/a',b'one',0o644),('source/a',b'two',0o644)],
            [('source/a',b'one',0o644),('source/a',b'one',0o755)],
            [('source/a',b'one',0o644),('source/./a',b'one',0o644)],
            [('../escape',b'x',0o644)], [('/absolute',b'x',0o644)]]:
            with self.subTest(entries=entries),self.archive(entries) as tar,self.assertRaises(ValueError):source_archives.source_members(tar,libs)
        with self.archive([('source/a',b'bytes',0o644)]) as tar:
            from unittest.mock import patch
            with patch.object(source_archives,'MAX_EXPANDED',1),self.assertRaises(ValueError):source_archives.source_members(tar,libs)
if __name__=='__main__':unittest.main()
