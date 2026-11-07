"""Check the compiler-selection shell code used by native crypto generators."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def selection_script(library, filename):
    """Read the patched selection hunk, retaining its context and added lines."""
    text = (ROOT/'recipes'/library/'mochios.patch').read_text()
    for section in text.split('--- a/'):
        if not section.startswith(filename+'\n') or '-    CC_FOR_BUILD=gcc\n' not in section:
            continue
        for hunk in section.split('@@ ')[1:]:
            if '-    CC_FOR_BUILD=gcc\n' not in hunk:
                continue
            # The diff context starts inside the outer "unset compiler" test.
            body = ''.join(line[1:]+'\n' for line in hunk.splitlines()
                           if line.startswith((' ', '+')) and not line.startswith('+++'))
            body = body.replace('AC_SUBST(CC_FOR_BUILD)\n', '')
            return 'if test -z "$CC_FOR_BUILD"; then\n'+body+'printf "%s" "$CC_FOR_BUILD"\n'
    raise AssertionError('Missing host compiler selection patch: '+library+'/'+filename)


class NativeCompilerTests(unittest.TestCase):
    def test_crypto_generators_use_clang_without_changing_target_or_explicit_compilers(self):
        for library in ['libgpg-error', 'libgcrypt']:
            for filename in ['configure', 'm4/ax_cc_for_build.m4']:
                script = selection_script(library, filename)
                for cross, host, explicit, expected in [
                    ('yes', 'mochios', '', 'clang'),
                    ('yes', 'linux-gnu', '', 'gcc'),
                    ('yes', 'mochios', 'custom-host-cc', 'custom-host-cc'),
                    ('no', 'mochios', '', '$(CC)'),
                ]:
                    with self.subTest(library=library, file=filename, cross=cross,
                                      host=host, explicit=explicit):
                        prefix = 'cross_compiling="$1"; host_os="$2"; CC_FOR_BUILD="$3"; CC=target-sdk-cc\n'
                        result = subprocess.run(['sh', '-c', prefix+script, 'selection',
                                                 cross, host, explicit],
                                                check=True, capture_output=True, text=True)
                        self.assertEqual(result.stdout, expected)
