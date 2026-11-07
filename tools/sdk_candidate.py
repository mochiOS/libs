#!/usr/bin/env python3
"""Use one reviewed SDK Actions artifact in the ordinary libs pipeline, without publication."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

import libs


def validate_run(run, policy):
    """Reject incomplete, fork, PR, rerouted and unreviewed SDK builds before downloading code."""
    expected = dict(id=policy['run_id'], head_sha=policy['head_sha'], event='workflow_dispatch',
                    status='completed', conclusion='success', path='.github/workflows/sdk.yml')
    if any(run.get(k) != v for k, v in expected.items()):
        raise ValueError('SDK candidate must be the reviewed successful SDK workflow run')
    for key in ['repository', 'head_repository']:
        if run.get(key, {}).get('full_name') != policy['repository']:
            raise ValueError('SDK candidate cannot come from a fork')


def validate_artifact(directory, policy):
    """Check archive identity, real SDK features/exports and the packaging path-audit receipt."""
    name = 'x86_64-mochios-sdk-' + policy['version'] + '.tar.gz'
    archive = directory / name
    manifest_path = directory / (name + '.json')
    if archive.is_symlink() or manifest_path.is_symlink():
        raise ValueError('SDK candidate files must be regular files')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('format') != 1 or manifest.get('archive') != name
            or manifest.get('sha256') != libs.hash_file(archive)
            or manifest.get('path_audit', {}).get('passed') is not True):
        raise ValueError('SDK candidate digest or path audit mismatch')
    sources = manifest.get('sources', {})
    if sources.get('version') != policy['version']:
        raise ValueError('SDK candidate source version mismatch')
    for component, commit in policy['sources'].items():
        if sources.get('sources', {}).get(component, {}).get('commit') != commit:
            raise ValueError('SDK candidate source commit mismatch: ' + component)
    with tarfile.open(archive) as tar:
        libs.safe_members(tar)
        def read(name):
            member = tar.getmember('sdk/' + name)
            if not member.isfile(): raise ValueError('SDK metadata must be regular files')
            if member.size > 8 * 1024**2: raise ValueError('SDK metadata exceeds size limit')
            return json.load(tar.extractfile(member))
        metadata = read('sdk.json')
        symbols = read('share/runtime-symbols.json')
    if (metadata != manifest.get('sdk') or metadata.get('version') != policy['version']
            or metadata.get('target') != libs.TARGET or metadata.get('abi') != libs.ABI
            or not set(policy['features']).issubset(metadata.get('features', []))
            or not set(policy['exports']).issubset(symbols)):
        raise ValueError('SDK candidate feature/export contract mismatch')
    return archive, manifest


def prepare(root, directory, fetch=False):
    """Preseed the ordinary SDK hash cache and freeze a non-publishable temporary SDK lock."""
    policy = json.loads((root / 'tooling/sdk-candidate.json').read_text())
    directory.mkdir(parents=True, exist_ok=True)
    if fetch:
        run = json.loads(libs.run(['gh', 'api', f"repos/{policy['repository']}/actions/runs/{policy['run_id']}"], env=os.environ.copy()))
        validate_run(run, policy)
        libs.run(['gh', 'run', 'download', str(policy['run_id']), '--repo', policy['repository'],
                  '--name', 'shared-sdk', '--dir', str(directory)], env=os.environ.copy())
        libs.write_json(directory / 'run.json', run)
    validate_run(json.loads((directory / 'run.json').read_text()), policy)
    archive, receipt = validate_artifact(directory, policy)
    lock = libs.sdk_lock(root)
    lock.update(version=policy['version'], sha256=receipt['sha256'], development_only=True,
                candidate_run=policy['run_id'], candidate_head=policy['head_sha'],
                url=f"https://github.com/{policy['repository']}/actions/runs/{policy['run_id']}")
    cache = root / 'out/downloads' / lock['sha256']
    cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, cache)
    # Run-local only: no tracked production SDK pin is changed by this command in git.
    text = ''.join(k + ' = ' + ('true' if v is True else 'false' if v is False else json.dumps(v)) + '\n'
                   for k, v in lock.items())
    (root / 'sdk.lock.toml').write_text(text)
    return lock


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['fetch', 'restore'])
    args = parser.parse_args()
    try:
        prepare(libs.ROOT, libs.ROOT / 'out/sdk-candidate', fetch=args.command == 'fetch')
    except (ValueError, OSError, KeyError, tarfile.TarError) as error:
        print('SDK candidate rejected: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
