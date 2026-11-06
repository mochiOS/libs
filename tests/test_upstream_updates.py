"""Update proposals use isolated files and mocked GitHub calls only."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import libs
import upstream_updates as updates

BASE = 'a' * 40
HEAD = 'b' * 40
CI = {'GITHUB_REPOSITORY': 'mochiOS/libs', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '123'}
CATALOG = {'libraries': {'zlib': {'revision': 6}, 'libpng': {'revision': 6}, 'freetype': {'revision': 4}}}


def plans():
    """Describe one updated source and two affected dependents."""
    before = {'packages': [dict(name=name, source={'version': '1.0', 'tag': 'v1.0', 'commit': BASE},
                                input_id=str(index)*64) for index, name in enumerate(CATALOG['libraries'])]}
    after = copy.deepcopy(before)
    for index, package in enumerate(after['packages']): package['input_id'] = str(index+3)*64
    after['packages'][0]['source'] = {'version': '2.0', 'tag': 'v2.0', 'commit': HEAD}
    after['sources'] = {item['name']: item['source'] for item in after['packages']}
    return before, after


def proposal_fixture(root):
    """Create verified candidate bytes without modifying a real repository."""
    proposal_dir = root/'proposal'; proposal_dir.mkdir()
    for name in updates.FILES: (proposal_dir/name).write_text('candidate '+name)
    hashes = {name: libs.hash_file(proposal_dir/name) for name in updates.FILES}
    proposal = dict(format=1, updates_available=True, base_commit=BASE, files=hashes,
                    branch='automation/library-updates-'+libs.identity(hashes)[:16],
                    updates=[dict(name='zlib', old='1.0', new='2.0', tag='v2.0', commit=HEAD)], revisions={'zlib':1})
    libs.write_json(proposal_dir/'proposal.json', proposal)
    return proposal_dir, proposal


class ProposalTests(unittest.TestCase):
    def test_new_source_resets_revision_and_transitive_dependents_increment(self):
        before, after = plans()
        changed, revisions = updates.select_updates(before, after, CATALOG)
        self.assertEqual(changed, [dict(name='zlib', old='1.0', new='2.0', tag='v2.0', commit=HEAD)])
        self.assertEqual(revisions, {'zlib':1, 'libpng':7, 'freetype':5})
        self.assertEqual(CATALOG['libraries']['libpng']['revision'], 6)

    def test_unchanged_sources_produce_no_update(self):
        before, _ = plans()
        self.assertEqual(updates.select_updates(before, before, CATALOG), ([], {}))

    def test_revision_rewrite_preserves_unrelated_sections_and_rejects_ambiguity(self):
        text='[libraries.zlib]\nrevision = 6\n\n[libraries.libpng]\nrevision = 6\n'
        result=updates.rewrite_revisions(text, {'zlib':1})
        self.assertEqual(result,text.replace('revision = 6','revision = 1',1))
        for value in ['[libraries.zlib]\n', '[libraries.zlib]\nrevision = 6\nrevision = 7\n']:
            with self.assertRaises(ValueError): updates.rewrite_revisions(value, {'zlib':1})

    def test_scan_only_writes_proposal_and_preserves_disabled_source_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); before, after=plans()
            catalog=''.join(f'[libraries.{name}]\nrevision = {spec["revision"]}\n' for name,spec in CATALOG['libraries'].items())
            (root/'libraries.toml').write_text(catalog)
            lock={'format':1,'sources':{'disabled':{'version':'0.1'}}}
            (root/'sources.lock.json').write_text(json.dumps(lock))
            with patch.object(libs,'read_catalog',return_value=CATALOG), patch.object(libs,'make_plan',side_effect=[before,after]), patch.object(updates,'command',return_value=BASE):
                proposal=updates.scan(root,root/'out')
            self.assertEqual((root/'libraries.toml').read_text(),catalog)
            self.assertEqual(json.loads((root/'sources.lock.json').read_text()),lock)
            candidate=json.loads((root/'out/sources.lock.json').read_text())
            self.assertEqual(candidate['sources']['disabled'],lock['sources']['disabled'])
            self.assertEqual(candidate['sources']['zlib']['version'],'2.0')
            self.assertTrue(proposal['updates_available'])

    def test_tampered_candidate_or_wrong_base_is_rejected_before_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory,proposal=proposal_fixture(root)
            for name in updates.FILES:(root/name).write_text('original')
            with patch.object(updates,'command',return_value=HEAD), self.assertRaisesRegex(ValueError,'base commit'):
                updates.apply(root,directory)
            (directory/'libraries.toml').write_text('tampered')
            with patch.object(updates,'command',return_value=BASE), self.assertRaisesRegex(ValueError,'hash mismatch'):
                updates.apply(root,directory)
            self.assertTrue(all((root/name).read_text()=='original' for name in updates.FILES))

    def test_apply_rejects_extra_files_and_unexpected_branches(self):
        for field in ['files','branch']:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); directory,proposal=proposal_fixture(root)
                if field=='files': proposal['files']['some-script.py']='0'*64
                else:proposal['branch']='main'
                libs.write_json(directory/'proposal.json',proposal)
                with patch.object(updates,'command',return_value=BASE), self.assertRaises(ValueError):updates.apply(root,directory)


class PullRequestTests(unittest.TestCase):
    def test_validation_failure_creates_draft_and_success_creates_ready_pr(self):
        for result in ['success','failure']:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); directory,proposal=proposal_fixture(root); calls=[]
                def command(args, cwd):
                    args=list(map(str,args));calls.append(args)
                    if args[:3]==['gh','pr','list']:return '[]'
                    if args[:4]==['git','remote','get-url','origin']:return 'https://github.com/mochiOS/libs.git'
                    if args[:3]==['git','rev-parse','HEAD']:return HEAD
                    if args[:3]==['gh','pr','create']:return 'https://github.com/mochiOS/libs/pull/1'
                    return ''
                with patch.dict(os.environ,CI),patch.object(updates,'apply',return_value=proposal),patch.object(updates,'command',side_effect=command):
                    updates.open_pull_request(root,directory,result)
                create=next(call for call in calls if call[:3]==['gh','pr','create'])
                self.assertEqual('--draft' in create,result!='success')
                status=next(call for call in calls if call[:4]==['gh','api','--method','POST'])
                self.assertIn('state='+result,status)
                self.assertIn('context=library-update-validation',status)
                body=(directory/'pull-request-body.txt').read_text()
                self.assertEqual(body.count('@mochiOS/devkit-maintainer'),1)
                self.assertFalse(any('merge' in call or 'publish' in call for call in calls))
                push=next(call for call in calls if 'push' in call)
                self.assertIn('HEAD:refs/heads/'+proposal['branch'],push)
                self.assertNotIn('--force',push)

    def test_closed_proposal_does_not_reopen_or_repeat_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,proposal=proposal_fixture(root)
            existing=[{'state':'CLOSED','url':'https://github.com/mochiOS/libs/pull/1'}]
            with patch.dict(os.environ,CI),patch.object(updates,'apply',return_value=proposal),patch.object(updates,'command',return_value=json.dumps(existing)) as command:
                updates.open_pull_request(root,directory,'success')
            self.assertEqual(command.call_count,1)
            self.assertFalse((directory/'pull-request-body.txt').exists())

    def test_existing_pr_with_unrelated_files_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,proposal=proposal_fixture(root);calls=[]
            def command(args,cwd):
                calls.append(args)
                if args[:3]==['gh','pr','list']:return json.dumps([{'state':'OPEN','number':1}])
                return 'libraries.toml\ntools/evil.py'
            with patch.dict(os.environ,CI),patch.object(updates,'apply',return_value=proposal),patch.object(updates,'command',side_effect=command),self.assertRaisesRegex(ValueError,'unrelated'):
                updates.open_pull_request(root,directory,'success')
            self.assertEqual(len(calls),2)

    def test_existing_matching_pr_only_updates_check_without_another_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,proposal=proposal_fixture(root);calls=[]
            def command(args,cwd):
                args=list(map(str,args));calls.append(args)
                if args[:3]==['gh','pr','list']:return json.dumps([dict(state='OPEN',number=1,headRefOid=HEAD,isDraft=False,url='https://github.com/mochiOS/libs/pull/1')])
                if args[:3]==['gh','pr','diff']:return '\n'.join(updates.FILES)
                if args[:2]==['gh','api'] and '/contents/' in args[2]:
                    name=args[2].split('/contents/')[1].split('?')[0]
                    return json.dumps({'content':base64.b64encode((directory/name).read_bytes()).decode()})
                return ''
            with patch.dict(os.environ,CI),patch.object(updates,'apply',return_value=proposal),patch.object(updates,'command',side_effect=command):updates.open_pull_request(root,directory,'success')
            self.assertFalse(any(call[:3]==['gh','pr','create'] or 'push' in call for call in calls))
            self.assertFalse((directory/'pull-request-body.txt').exists())
            self.assertTrue(any('state=success' in call for call in calls))

    def test_non_main_or_fork_context_stops_before_any_git_or_github_write(self):
        for environment in [dict(CI,GITHUB_REPOSITORY='someone/libs'),dict(CI,GITHUB_REF='refs/heads/feature')]:
            with self.subTest(environment=environment),patch.dict(os.environ,environment),patch.object(updates,'command') as command,self.assertRaises(ValueError):
                updates.open_pull_request(Path('/tmp'),Path('/tmp'),'success')
            command.assert_not_called()


if __name__ == '__main__':unittest.main()
