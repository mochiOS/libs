"""Exercise real child processes without building SDKs or contacting GitHub."""
import io
import json
from contextlib import ExitStack
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import libs
import releases


class BuildLoggingTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'})
        environment.start()
        self.addCleanup(environment.stop)

    def test_partial_output_reaches_console_and_log_before_child_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); log = root/'container.log'; release = root/'continue'
            visible = threading.Event(); errors = []
            class Console(io.StringIO):
                def write(self, text):
                    result = super().write(text)
                    if 'starting' in self.getvalue(): visible.set()
                    return result
            console = Console()
            script = '''import pathlib,sys,time
sys.stdout.write('starting');sys.stdout.flush()
release=pathlib.Path(sys.argv[1]); deadline=time.monotonic()+10
while not release.exists():
    if time.monotonic()>deadline:raise SystemExit(9)
    time.sleep(0.01)
sys.stderr.write(' done\\n');sys.stderr.flush()
'''
            def child():
                try: libs.run([sys.executable, '-c', script, release], log=log)
                except BaseException as error: errors.append(error)
            with patch.object(sys, 'stdout', console):
                thread = threading.Thread(target=child); thread.start()
                try:
                    self.assertTrue(visible.wait(5), 'Output must be visible while the child is still waiting')
                    self.assertTrue(thread.is_alive())
                    self.assertEqual(log.read_bytes(), b'starting')
                finally:
                    release.touch(); thread.join(10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(console.getvalue(), 'starting done\n')
            self.assertEqual(log.read_bytes(), b'starting done\n')

    def test_stderr_and_nonzero_status_are_preserved_and_existing_log_is_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'container.log'; log.write_bytes(b'previous\n'); console=io.StringIO()
            script="import sys; sys.stdout.write('out\\n'); sys.stdout.flush(); sys.stderr.write('failure\\n'); sys.exit(7)"
            with patch.object(sys,'stdout',console), self.assertRaises(subprocess.CalledProcessError) as failed:
                libs.run([sys.executable,'-c',script],log=log)
            self.assertEqual(failed.exception.returncode,7)
            self.assertEqual(console.getvalue(),'out\nfailure\n')
            self.assertEqual(log.read_bytes(),b'previous\nout\nfailure\n')

    def test_split_utf8_and_invalid_bytes_do_not_corrupt_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'container.log';console=io.StringIO()
            script="import os,time; os.write(1,b'\\xe3'); time.sleep(0.05); os.write(1,b'\\x81\\x82\\xff')"
            with patch.object(sys,'stdout',console):libs.run([sys.executable,'-c',script],log=log)
            self.assertEqual(console.getvalue(),'あ\ufffd')
            self.assertEqual(log.read_bytes(),b'\xe3\x81\x82\xff')

    def test_github_command_interpretation_is_disabled_only_during_child_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'container.log';console=io.StringIO()
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),patch.object(sys,'stdout',console):
                libs.run([sys.executable,'-c',"print('::error::untrusted source output')"],log=log)
            text=console.getvalue();match=re.match(r'::stop-commands::([0-9a-f]{32})\n',text)
            self.assertIsNotNone(match)
            self.assertTrue(text.endswith('\n::'+match[1]+'::\n'))
            self.assertEqual(log.read_bytes(),b'::error::untrusted source output\n')

    def test_failed_spawn_still_restores_github_command_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            console=io.StringIO()
            with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),patch.object(sys,'stdout',console),self.assertRaises(FileNotFoundError):
                libs.run([Path(tmp)/'missing-command'],log=Path(tmp)/'container.log')
            match=re.match(r'::stop-commands::([0-9a-f]{32})\n',console.getvalue())
            self.assertIsNotNone(match)
            self.assertTrue(console.getvalue().endswith('\n::'+match[1]+'::\n'))

    def test_build_progress_and_container_output_share_the_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'out'; source=root/'source'; source.mkdir()
            (source/'COPYING').write_text('fixture license')
            recipe_dir=root/'recipes/fixture'; recipe_dir.mkdir(parents=True)
            (recipe_dir/'probe.c').write_text('/* no compiler is run in this fixture */')
            image='sha256:'+'1'*64; builder='sha256:'+'2'*64
            package={'name':'fixture','release':'fixture-1.0','input_id':'3'*64,'source':{}}
            plan={'sdk':{'available':True,'builder_image':image,'version':'fixture-sdk'},'packages':[package]}
            catalog={'libraries':{'fixture':{'enabled':True,'dependencies':[], 'recipe':'recipes/fixture/recipe.json'}}}
            specification={'probe':'probe.c','licenses':['COPYING']}
            original_run=libs.run; console=io.StringIO()
            script = """import json,pathlib,sys
work=pathlib.Path(sys.argv[1]); (work/'stage/usr/lib').mkdir(parents=True)
(work/'stage/usr/lib/libfixture.so').write_bytes(b'fixture data, not a target binary')
(work/'verified.json').write_text(json.dumps({'format':1,'target':'x86_64-unknown-mochios','abi':'mochios-1','tools':{}}))
print('fixture build output',flush=True)
"""
            def execute(args, **kwargs):
                if args[0]=='docker':
                    if args[-1]=='{{json .Config.Labels}}':return json.dumps({'org.mochios.libs.runner-sha256':'4'*64})
                    if args[-1]=='{{.Id}}':return builder
                    return ''
                if args[0]=='git':return '0'*40
                return original_run(args,**kwargs)
            def container(builder_id,sdk,source_dir,dependencies,work):
                return [sys.executable,'-c',script,work]
            with ExitStack() as stack:
                stack.enter_context(patch.object(sys,'stdout',console))
                for name,value in [('validate_plan',catalog),('install_sdk',root/'sdk'),('validate_sdk',{}),
                                   ('runner_identity','4'*64),('checkout_source',source),('recipe',specification)]:
                    stack.enter_context(patch.object(libs,name,return_value=value))
                stack.enter_context(patch.object(libs.shutil,'which',return_value='/fixture/docker'))
                stack.enter_context(patch.object(releases,'restore_existing',return_value=None))
                stack.enter_context(patch.object(libs,'package_build'))
                stack.enter_context(patch.object(libs,'docker_command',side_effect=container))
                stack.enter_context(patch.object(libs,'run',side_effect=execute))
                libs.build(root,plan,output,None)
            text=console.getvalue()
            self.assertIn('[1/1] fixture-1.0:',text)
            self.assertIn('fetching and verifying source',text)
            self.assertIn('fixture build output',text)
            self.assertIn('verifying and packaging artifacts',text)
            self.assertIn('fixture-1.0: complete',text)
            work=output/'builds'/package['input_id']
            self.assertEqual((work/'container.log').read_text(),'fixture build output\n')
            self.assertTrue((work/'complete.json').is_file())

    def test_unlogged_commands_keep_the_existing_capture_contract(self):
        self.assertEqual(libs.run([sys.executable,'-c',"print('  captured  ')"]), 'captured')


if __name__ == '__main__':unittest.main()
