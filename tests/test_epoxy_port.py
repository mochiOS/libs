"""Check private header provenance and the public-SDK publication gate."""
from pathlib import Path
import hashlib
import json
import unittest
ROOT=Path(__file__).resolve().parents[1]

class EpoxyPort(unittest.TestCase):
    def test_vendored_khronos_headers_are_complete_and_match_recorded_bytes(self):
        recipe=json.loads((ROOT/'recipes/epoxy/recipe.json').read_text())
        patch=(ROOT/'recipes/epoxy/mochios.patch').read_text()
        for name,record in recipe['vendored_headers'].items():
            section=patch.split('+++ b/include/epoxy/khronos/'+name+'\n',1)[1].split('\n--- ',1)[0]
            installed='\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))+'\n'
            self.assertEqual(hashlib.sha256(installed.encode()).hexdigest(),record['installed_sha256'])
            self.assertIn('Copyright',installed)
            self.assertEqual(record['commit'],'8e56e86e65687697ef54114cdb1afabdfac756bf')
            upstream=installed.replace('#include <epoxy/khronos/khrplatform.h>','#include <KHR/khrplatform.h>').replace('#if defined(EGL_NO_PLATFORM_SPECIFIC_TYPES) || defined(__mochios__)','#if defined(EGL_NO_PLATFORM_SPECIFIC_TYPES)')
            self.assertEqual(hashlib.sha256(upstream.encode()).hexdigest(),record['upstream_sha256'])

    def test_recipe_does_not_claim_to_supply_egl_or_enable_an_unbuildable_release(self):
        import sys
        sys.path.insert(0,str(ROOT/'tools'))
        import libs
        catalog=libs.read_catalog(ROOT)
        self.assertFalse(catalog['libraries']['epoxy']['enabled'])
        self.assertIn('dlfcn.h',catalog['libraries']['epoxy']['reason'])
        recipe=json.loads((ROOT/'recipes/epoxy/recipe.json').read_text())
        self.assertEqual(recipe['pkg_config_modules'],['epoxy'])
        self.assertEqual(recipe['options'],dict(egl='yes',glx='no',x11=False,tests=False,docs=False))
        self.assertEqual(recipe['install_tags'],['runtime','devel'])

if __name__=='__main__':unittest.main()
