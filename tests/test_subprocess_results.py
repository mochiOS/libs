"""A boot marker or partial subprocess log must never become native runtime evidence."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('subprocess_runner', Path(__file__).parent / 'mochios-subprocess/run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
PASS = ''.join('GSUBPROCESS PASS mode=' + case + '\n' for case in runner.CASES)
COMPLETE = 'GSUBPROCESS COMPLETE failures=0\n'


class NativeSubprocessResults(unittest.TestCase):
    def test_all_cases_and_one_completion_are_required(self):
        self.assertTrue(runner.outcome(PASS + COMPLETE)['passed'])
        for text in ['', 'service-manager.service: Binder.app ready\n', PASS,
                     PASS + 'GSUBPROCESS COMPLETE failures=1\n', PASS + COMPLETE * 2,
                     PASS + 'GSUBPROCESS PASS mode=async-wait\n' + COMPLETE,
                     PASS + 'GSUBPROCESS PASS mode=unexpected\n' + COMPLETE]:
            with self.subTest(text=text):
                self.assertFalse(runner.outcome(text)['passed'])
        for case in runner.CASES:
            self.assertFalse(runner.outcome(PASS.replace('GSUBPROCESS PASS mode=' + case + '\n', '') + COMPLETE)['passed'])

    def test_failure_takes_precedence_over_complete_success_markers(self):
        self.assertFalse(runner.outcome('GSUBPROCESS FAIL launcher exec\n' + PASS + COMPLETE)['passed'])
        self.assertFalse(runner.outcome(PASS + 'GSUBPROCESS COMPLETE failures=2\n' + COMPLETE)['passed'])

    def test_reads_the_guest_subprocess_log_not_another_fixture(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, PASS + COMPLETE if command[0] == 'debugfs' else '', '')
        with tempfile.TemporaryDirectory() as directory, patch.object(runner.subprocess, 'run', run):
            text = runner.read_results(Path(directory), {'start': 2048, 'size': 2048})
        self.assertEqual(calls[-1][2], 'cat /tmp/gsubprocess-results.log')
        self.assertTrue(runner.outcome(text)['passed'])


if __name__ == '__main__':
    unittest.main()
