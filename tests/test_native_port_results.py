"""Only a unique, completed, zero-return native probe is runtime evidence."""
from pathlib import Path
import importlib.util
import unittest
import tempfile
import subprocess
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('port_runner',Path(__file__).parent/'mochios-ports/run.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
class Results(unittest.TestCase):
    def test_incomplete_failed_and_duplicate_probes_are_rejected(self):
        start='PORT-SMOKE START\n';ok='PORT-SMOKE COMPLETE result=0\n'
        self.assertTrue(runner.outcome(start+'XKBCOMMON PASS\n'+ok)['passed'])
        for text in ['',start,ok,start+'PORT-SMOKE COMPLETE result=1\n',start+ok+ok,
                     start+ok+'PORT-SMOKE COMPLETE result=1\n',start+start+ok,
                     'PORT-SMOKE FAIL launcher\n'+start+ok]:
            with self.subTest(text=text):self.assertFalse(runner.outcome(text)['passed'])
    def test_runner_reads_only_its_own_guest_log(self):
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command,0,'PORT-SMOKE START\nPORT-SMOKE COMPLETE result=0\n' if command[0]=='debugfs' else '','')
        with tempfile.TemporaryDirectory() as directory,patch.object(runner.subprocess,'run',run):
            text=runner.read_results(Path(directory),{'start':2048,'size':2048})
        self.assertEqual(calls[-1][2],'cat /tmp/port-smoke-results.log')
        self.assertTrue(runner.outcome(text)['passed'])
if __name__=='__main__':unittest.main()
