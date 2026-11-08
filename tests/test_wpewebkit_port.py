"""Resolve real WPE registry dependencies without enabling the unfinished C ABI adapter."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import libs
import sdk_candidate
class WPEPort(unittest.TestCase):
    def test_candidate_requires_complete_graph_and_keeps_product_adapter_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);shutil.copytree(ROOT/'recipes',root/'recipes');shutil.copy2(ROOT/'libraries.toml',root/'libraries.toml')
            original=(root/'libraries.toml').read_text()
            sdk_candidate.activate_ports(root,['wpewebkit'])
            with self.assertRaises(ValueError):libs.read_catalog(root)
            (root/'libraries.toml').write_text(original)
            policy=json.loads((ROOT/'tooling/sdk-candidate.json').read_text())
            sdk_candidate.activate_ports(root,policy['enable_libraries'])
            catalog=libs.read_catalog(root);order=libs.order(catalog);target=order.index('wpewebkit')
            for dependency in libs.transitive_dependencies(catalog,'wpewebkit'):
                self.assertLess(order.index(dependency),target)
            self.assertIn('mesa',libs.transitive_dependencies(catalog,'wpewebkit'))
            self.assertFalse(catalog['libraries']['webkit']['enabled'])
            recipe=libs.recipe(root,catalog['libraries']['wpewebkit'])
            self.assertEqual(len(recipe['executables']),2)
    def test_official_archive_matches_the_reviewed_upstream_pin(self):
        record=json.loads((ROOT/'sources.lock.json').read_text())['sources']['wpewebkit']
        upstream=json.loads((ROOT/'engines/webkit/webkit.lock.json').read_text())
        self.assertEqual(record['archive'],{'url':upstream['url'],'sha256':upstream['sha256']})
        self.assertEqual(record['version'],upstream['version'])
if __name__=='__main__':unittest.main()
