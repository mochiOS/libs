"""Measured code closure, helper export and independent package contracts."""
from pathlib import Path
import importlib.util
import struct
import tempfile
import tomllib
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_webkit', ROOT/'tools/package_webkit.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
class EnginePackage(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)/'prefix'
        data = bytearray(64); data[:6] = b'\x7fELF\x02\x01'; struct.pack_into('<HH',data,16,3,62)
        for name in ['usr/lib/libwebkit.so.1', 'usr/lib/libengine.so.2', 'usr/libexec/WPEWebProcess', 'usr/libexec/WPENetworkProcess']:
            p=root/name;p.parent.mkdir(parents=True, exist_ok=True);p.write_bytes(data)
        p=root/'usr/share/licenses/webkit/LICENSE';p.parent.mkdir(parents=True);p.write_text('upstream fixture notice')
        return root
    def metadata(self, path):
        if path.name.startswith('libwebkit'):return 3,path.name,['libengine.so.2']
        if path.name.startswith('libengine'):return 3,path.name,[]
        return 2,None,['libengine.so.2']
    def test_measured_separate_package_and_minimal_exported_children(self):
        with tempfile.TemporaryDirectory() as t:
            prefix=self.fixture(t); output=Path(t)/'product'
            with patch.object(module,'elf',side_effect=self.metadata), patch.object(module,'validate_process_hook'):
                module.stage(prefix,output,'2.54.2')
            m=tomllib.loads((output/'manifest.toml').read_text())
            self.assertEqual(m['package']['kind'],'binary')
            paths={f['path'] for f in m['file']}
            self.assertIn('/system/libraries/libwebkit.so.1',paths)
            self.assertIn('/system/libraries/webkit/fonts.conf',paths)
            self.assertIn('/system/libraries/webkit/libengine.so.2',paths)
            self.assertIn('/system/libraries/webkit/helpers/WPEWebProcess',paths)
            self.assertTrue(all(p.startswith(('/system/libraries/','/system/share/licenses/')) for p in paths))
            self.assertEqual(m['package']['provenance'],'built-in')
            self.assertEqual({e['name'] for e in m['executable_export']},{'web-process','network-process'})
            for binary in m['binary']:
                self.assertNotIn('fs.read.all',binary['requires'])
                if binary['path'].endswith('WPEWebProcess'):self.assertNotIn('net.connect',binary['requires'])
                if binary['path'].endswith('WPENetworkProcess'):self.assertIn('net.connect',binary['requires'])
            for f in m['file']:
                path=output/'payload/root'/f['path'].lstrip('/')
                self.assertEqual(f['size'],path.stat().st_size)
                self.assertEqual(f['digest'],'sha256:'+module.hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertFalse(any(p.is_symlink() for p in output.rglob('*')))
    def test_missing_helper_and_dso_fail_before_product_creation(self):
        with tempfile.TemporaryDirectory() as t:
            prefix=self.fixture(t); output=Path(t)/'product'
            (prefix/'usr/lib/libengine.so.2').unlink()
            with patch.object(module,'elf',side_effect=self.metadata),self.assertRaisesRegex(ValueError,'Unresolved'):module.stage(prefix,output,'1')
            self.assertFalse(output.exists())
            (prefix/'usr/libexec/WPEWebProcess').unlink()
            with patch.object(module,'elf',side_effect=self.metadata),self.assertRaisesRegex(ValueError,'Missing'):module.stage(prefix,output,'1')
    def test_wrong_architecture_and_external_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            prefix=self.fixture(t); output=Path(t)/'product';p=prefix/'usr/lib/libwebkit.so.1'
            data=bytearray(p.read_bytes());struct.pack_into('<H',data,18,183);p.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'x86-64'):module.stage(prefix,output,'1')
            (prefix/'outside').symlink_to('/etc/passwd')
            with patch.object(module,'elf',side_effect=self.metadata),self.assertRaisesRegex(ValueError,'symlink'):module.stage(prefix,output,'1')

    def test_pre_hook_artifacts_fail_before_product_creation(self):
        with tempfile.TemporaryDirectory() as t:
            prefix=self.fixture(t); output=Path(t)/'product'
            with patch.object(module,'elf',side_effect=self.metadata), patch.object(module.subprocess,'run',return_value=type('Result',(),{'stdout':''})()):
                with self.assertRaisesRegex(ValueError,'predates'):module.stage(prefix,output,'1')
            self.assertFalse(output.exists())
