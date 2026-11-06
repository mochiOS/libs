#!/usr/bin/env python3
"""Require a configured GitHub Environment approval gate before publication."""
import json
import os
import subprocess
import sys

REPOSITORY = 'mochiOS/libs'
ENVIRONMENT = 'library-release'
REVIEW_TEAM = 'devkit-maintainer'
REVIEW_TEAM_URL = f'https://github.com/orgs/mochiOS/teams/{REVIEW_TEAM}'


def validate_environment(environment):
    """Reject missing, mismatched or unprotected release environments."""
    if not isinstance(environment, dict) or environment.get('name') != ENVIRONMENT:
        raise ValueError('The library-release environment is missing or mismatched')
    rules = environment.get('protection_rules', [])
    if not isinstance(rules, list):
        raise ValueError('Invalid release environment protection rules')
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('type') != 'required_reviewers':
            continue
        reviewers = rule.get('reviewers', [])
        if not isinstance(reviewers, list) or len(reviewers) != 1:
            continue
        item = reviewers[0]
        if not isinstance(item, dict) or item.get('type') != 'Team':
            continue
        team = item.get('reviewer')
        if (isinstance(team, dict) and type(team.get('id')) is int and team['id'] > 0
                and team.get('slug') == REVIEW_TEAM and team.get('html_url') == REVIEW_TEAM_URL):
            return
    raise ValueError('Require mochiOS/devkit-maintainer as the only release approval team')


def check_environment():
    """Read the approval policy without creating environments or changing protection."""
    repository = os.environ.get('GITHUB_REPOSITORY', REPOSITORY)
    if repository.lower() != REPOSITORY.lower():
        raise ValueError('Release approval checks are restricted to mochiOS/libs')
    result = subprocess.run(
        ['gh', 'api', f'repos/{REPOSITORY}/environments/{ENVIRONMENT}'],
        env=os.environ.copy(), capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise ValueError('Cannot verify the library-release approval policy; publication is blocked')
    validate_environment(json.loads(result.stdout))


def main():
    """Fail closed when the release approval policy cannot be verified."""
    check_environment()
    print('library-release requires approval from mochiOS/devkit-maintainer')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(f'Release approval policy failed: {error}', file=sys.stderr)
        raise SystemExit(1)
