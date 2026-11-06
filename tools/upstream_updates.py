#!/usr/bin/env python3
"""Propose reviewed upstream source updates without merging or publishing them."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import libs

FILES = ('libraries.toml', 'sources.lock.json')
REPOSITORY = 'mochiOS/libs'
TEAM = '@mochiOS/devkit-maintainer'


def command(args, root):
    """Run repository tooling without shell expansion or embedding credentials."""
    return subprocess.check_output(list(map(str, args)), cwd=root, env=os.environ.copy(), text=True).strip()


def select_updates(before, after, catalog):
    """Reset new versions to r1 and bump unchanged dependents with changed inputs."""
    old = {package['name']: package for package in before['packages']}
    updates, revisions = [], {}
    for package in after['packages']:
        name = package['name']; previous = old[name]
        if package['source'] != previous['source']:
            updates.append(dict(name=name, old=previous['source']['version'],
                                new=package['source']['version'], tag=package['source']['tag'],
                                commit=package['source']['commit']))
            revisions[name] = 1
        elif package['input_id'] != previous['input_id']:
            revisions[name] = catalog['libraries'][name]['revision'] + 1
    return updates, revisions


def rewrite_revisions(text, revisions):
    """Change only revision assignments inside the selected library sections."""
    for name, revision in revisions.items():
        section = re.compile(r'(^\[libraries\.' + re.escape(name) + r'\]\n)(.*?)(?=^\[|\Z)', re.M | re.S)
        def replace(match):
            body, count = re.subn(r'^revision = [0-9]+$', f'revision = {revision}', match[2], flags=re.M)
            if count != 1: raise ValueError('Ambiguous library revision: ' + name)
            return match[1] + body
        text, count = section.subn(replace, text)
        if count != 1: raise ValueError('Missing library section: ' + name)
    return text


def scan(root, output):
    """Freeze newer enabled-library tags and archives in a proposal artifact only."""
    catalog = libs.read_catalog(root)
    before = libs.make_plan(root)
    after = libs.make_plan(root, refresh=True)
    updates, revisions = select_updates(before, after, catalog)
    if not updates:
        return {'format': 1, 'updates_available': False}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'libraries.toml').write_text(rewrite_revisions((root / 'libraries.toml').read_text(), revisions))
    lock = json.loads((root / 'sources.lock.json').read_text())
    for update in updates:
        lock['sources'][update['name']] = after['sources'][update['name']]
    (output / 'sources.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    hashes = {name: libs.hash_file(output / name) for name in FILES}
    proposal = dict(format=1, updates_available=True, base_commit=command(['git', 'rev-parse', 'HEAD'], root),
                    files=hashes, updates=updates, revisions=revisions,
                    branch='automation/library-updates-' + libs.identity(hashes)[:16])
    libs.write_json(output / 'proposal.json', proposal)
    return proposal


def verify_proposal(root, proposal_dir):
    """Bind candidate files to their hashes and the workflow's exact base checkout."""
    proposal = json.loads((proposal_dir / 'proposal.json').read_text())
    if proposal.get('format') != 1 or proposal.get('updates_available') is not True:
        raise ValueError('No upstream update proposal')
    if not libs.SHA.fullmatch(proposal.get('base_commit', '')) or proposal['base_commit'] != command(['git', 'rev-parse', 'HEAD'], root):
        raise ValueError('Proposal base commit differs from this workflow checkout')
    if set(proposal.get('files', {})) != set(FILES):
        raise ValueError('Unexpected proposal file set')
    for name in FILES:
        path = proposal_dir / name
        if path.is_symlink() or not path.is_file() or libs.hash_file(path) != proposal['files'][name]:
            raise ValueError('Proposal file hash mismatch: ' + name)
    expected = 'automation/library-updates-' + libs.identity(proposal['files'])[:16]
    if proposal.get('branch') != expected:
        raise ValueError('Unexpected proposal branch')
    return proposal


def apply(root, proposal_dir):
    """Apply only the two verified candidate registry files to a disposable checkout."""
    proposal = verify_proposal(root, proposal_dir)
    for name in FILES:
        target = root / name
        if target.is_symlink(): raise ValueError('Registry file must not be a symlink')
        shutil.copyfile(proposal_dir / name, target)
    libs.validate_plan(root, libs.make_plan(root))
    return proposal


def pr_body(proposal, result, run_url):
    """Describe updates and validation, mentioning the maintainer team once."""
    lines = [TEAM, '', 'Upstream library releases are available.', '',
             '| Library | Current | Proposed |', '| --- | --- | --- |']
    lines += [f"| {item['name']} | {item['old']} | {item['new']} |" for item in proposal['updates']]
    lines += ['', f'Initial validation: **{result}** — [workflow results]({run_url}). Latest results appear in the library-update-validation commit status.', '',
              'The candidate registry files were tested and cross-built against base commit ' + proposal['base_commit'] + '.',
              'Changed dependency inputs receive new build revisions; new upstream versions start at r1.',
              'Merging starts the normal build and devkit-maintainer publication approval flow.',
              'Target runtime testing has not been performed.']
    return '\n'.join(lines) + '\n'


def open_pull_request(root, proposal_dir, result):
    """Create an idempotent bot PR and record validation; never merge or publish."""
    if os.environ.get('GITHUB_REPOSITORY', '').lower() != REPOSITORY.lower():
        raise ValueError('PR automation is restricted to mochiOS/libs')
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Update proposals must originate from main')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if not run_id.isdigit(): raise ValueError('Missing workflow run identity')
    proposal = apply(root, proposal_dir)
    branch = proposal['branch']
    run_url = f'https://github.com/{REPOSITORY}/actions/runs/{run_id}'
    existing = json.loads(command(['gh', 'pr', 'list', '--repo', REPOSITORY, '--state', 'all',
                                  '--head', branch, '--json', 'number,url,state,headRefOid,isDraft'], root))
    if existing:
        pr = existing[0]
        if pr['state'] != 'OPEN':
            print('Proposal was already merged or closed: ' + pr['url'])
            return
        # A matching branch name alone is insufficient: reject modified bot PRs.
        paths = set(command(['gh', 'pr', 'diff', str(pr['number']), '--repo', REPOSITORY, '--name-only'], root).splitlines())
        if not paths or not paths.issubset(FILES): raise ValueError('Existing bot PR has unrelated changes')
        sha = pr['headRefOid']
        if not libs.SHA.fullmatch(sha): raise ValueError('Invalid PR commit identity')
        for name in FILES:
            blob = json.loads(command(['gh', 'api', f'repos/{REPOSITORY}/contents/{name}?ref={sha}'], root))
            content = base64.b64decode(blob['content'].replace('\n', ''), validate=True)
            if hashlib.sha256(content).hexdigest() != proposal['files'][name]:
                raise ValueError('Existing bot PR registry differs from the validated proposal')
        if (result == 'success') == pr['isDraft']:
            args = ['gh', 'pr', 'ready', str(pr['number']), '--repo', REPOSITORY]
            if result != 'success': args.append('--undo')
            command(args, root)
        url = pr['url']
    else:
        origin = command(['git', 'remote', 'get-url', 'origin'], root).removesuffix('.git')
        if origin.lower() != f'https://github.com/{REPOSITORY}'.lower():
            raise ValueError('Unexpected update repository remote')
        remote = command(['git', 'ls-remote', '--heads', 'origin', 'refs/heads/' + branch], root)
        if remote:
            command(['git', 'fetch', 'origin', branch], root)
            sha = command(['git', 'rev-parse', 'FETCH_HEAD'], root)
            for name in FILES:
                content = subprocess.check_output(['git', 'show', f'{sha}:{name}'], cwd=root)
                if hashlib.sha256(content).hexdigest() != proposal['files'][name]:
                    raise ValueError('Existing proposal branch has different registry files')
            paths = set(command(['git', 'diff', '--name-only', proposal['base_commit'] + '...' + sha], root).splitlines())
            if not paths or not paths.issubset(FILES): raise ValueError('Existing proposal branch has unrelated changes')
        else:
            command(['git', 'switch', '-c', branch], root)
            command(['git', 'config', '--local', 'user.name', 'github-actions[bot]'], root)
            command(['git', 'config', '--local', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'], root)
            command(['git', 'add', '--', *FILES], root)
            command(['git', 'commit', '-m', 'Update upstream library sources'], root)
            sha = command(['git', 'rev-parse', 'HEAD'], root)
            command(['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                     'push', 'origin', 'HEAD:refs/heads/' + branch], root)
        body_path = proposal_dir / 'pull-request-body.txt'
        body_path.write_text(pr_body(proposal, result, run_url))
        args = ['gh', 'pr', 'create', '--repo', REPOSITORY, '--base', 'main', '--head', branch,
                '--title', 'Update upstream library releases', '--body-file', body_path]
        if result != 'success': args.append('--draft')
        url = command(args, root)
    if not libs.SHA.fullmatch(sha): raise ValueError('Invalid PR commit identity')
    command(['gh', 'api', '--method', 'POST', f'repos/{REPOSITORY}/statuses/{sha}',
             '-f', 'context=library-update-validation', '-f', 'state=' + ('success' if result == 'success' else 'failure'),
             '-f', 'target_url=' + run_url, '-f', 'description=Candidate registry tests and cross-build: ' + result], root)
    print(url)


def main():
    """Scan without writes to GitHub; apply or open a PR only in their explicit stages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['scan', 'apply', 'pull-request'])
    parser.add_argument('--proposal', type=Path, default=Path('out/upstream-proposal'))
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--result', choices=['success', 'failure', 'cancelled', 'skipped'], default='failure')
    args = parser.parse_args()
    if args.command == 'scan':
        proposal = scan(libs.ROOT, args.proposal)
        if args.github_output:
            with args.github_output.open('a') as output:
                output.write('updates_available=' + str(proposal['updates_available']).lower() + '\n')
        print(json.dumps(proposal, indent=2))
    elif args.command == 'apply': apply(libs.ROOT, args.proposal)
    else: open_pull_request(libs.ROOT, args.proposal, args.result)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Upstream update proposal failed: {error}', file=sys.stderr)
        raise SystemExit(1)
