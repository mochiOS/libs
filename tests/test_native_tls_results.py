"""Never promote a cross-build or a partial native log to a runtime pass."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('native_tls_runner', Path(__file__).parent / 'mochios-tls/run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
PASS = ''.join('NATIVE-TLS PASS mode=' + case + '\n' for case in runner.CASES)


class NativeTlsResults(unittest.TestCase):
    def test_requires_every_case_and_a_unique_successful_completion(self):
        self.assertTrue(runner.outcome(PASS + 'NATIVE-TLS SUITE failures=0\n')['passed'])
        for text in ['', 'service-manager.service: Binder.app ready\n', PASS,
                     PASS.replace('NATIVE-TLS PASS mode=cancel\n', '') + 'NATIVE-TLS SUITE failures=0\n',
                     PASS + 'NATIVE-TLS SUITE failures=1\n',
                     PASS + 'NATIVE-TLS SUITE failures=1\nNATIVE-TLS SUITE failures=0\n',
                     PASS + 'NATIVE-TLS PASS mode=https\nNATIVE-TLS SUITE failures=0\n']:
            with self.subTest(text=text):
                self.assertFalse(runner.outcome(text)['passed'])

    def test_failure_takes_precedence_over_success_markers(self):
        result = runner.outcome('NATIVE-TLS FAIL step=certificate\n' + PASS + 'NATIVE-TLS SUITE failures=0\n')
        self.assertFalse(result['passed'])
        self.assertTrue(result['failed'])


if __name__ == '__main__':
    unittest.main()
