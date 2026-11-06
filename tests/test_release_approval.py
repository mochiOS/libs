"""Approval policy tests never change GitHub settings or publish releases."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import release_approval


def protected_environment(kind='Team'):
    """Describe a GitHub environment with an explicit required reviewer."""
    return {'name': 'library-release', 'protection_rules': [
        {'type': 'required_reviewers', 'reviewers': [
            {'type': kind, 'reviewer': {'id': 42, 'slug': 'devkit-maintainer',
                                       'html_url': 'https://github.com/orgs/mochiOS/teams/devkit-maintainer'}},
        ]},
    ]}


class ReleaseApprovalTests(unittest.TestCase):
    def test_only_the_chosen_org_team_satisfies_the_gate(self):
        release_approval.validate_environment(protected_environment())
        for kind, slug, url in [
            ('User', 'devkit-maintainer', release_approval.REVIEW_TEAM_URL),
            ('Team', 'another-team', release_approval.REVIEW_TEAM_URL),
            ('Team', 'devkit-maintainer', 'https://github.com/orgs/someone/teams/devkit-maintainer'),
        ]:
            environment = protected_environment(kind)
            reviewer = environment['protection_rules'][0]['reviewers'][0]['reviewer']
            reviewer.update(slug=slug, html_url=url)
            with self.subTest(kind=kind, slug=slug, url=url), self.assertRaises(ValueError):
                release_approval.validate_environment(environment)
        environment = protected_environment()
        environment['protection_rules'][0]['reviewers'].append({'type': 'User', 'reviewer': {'id': 1}})
        with self.assertRaises(ValueError):
            release_approval.validate_environment(environment)

    def test_missing_or_empty_review_protection_is_rejected(self):
        for environment in [None, {}, {'name': 'staging'},
                            {'name': 'library-release', 'protection_rules': []},
                            {'name': 'library-release', 'protection_rules': [
                                {'type': 'wait_timer', 'wait_timer': 10},
                            ]},
                            {'name': 'library-release', 'protection_rules': [
                                {'type': 'required_reviewers', 'reviewers': []},
                            ]}]:
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                release_approval.validate_environment(environment)

    def test_malformed_reviewers_cannot_satisfy_the_gate(self):
        for reviewer in [None, {}, {'type': 'User', 'reviewer': {}},
                         {'type': 'User', 'reviewer': {'id': 0}},
                         {'type': 'User', 'reviewer': {'id': True}},
                         {'type': 'User', 'reviewer': {'id': '42'}},
                         {'type': 'Bot', 'reviewer': {'id': 42}}]:
            environment = protected_environment()
            environment['protection_rules'][0]['reviewers'] = [reviewer]
            with self.subTest(reviewer=reviewer), self.assertRaises(ValueError):
                release_approval.validate_environment(environment)

    def test_failed_api_reads_fail_closed(self):
        for error in ['HTTP 404', 'HTTP 403', 'connection timed out']:
            result = subprocess.CompletedProcess([], 1, '', error)
            with self.subTest(error=error), patch.dict(os.environ, {'GITHUB_REPOSITORY': 'mochiOS/libs'}), \
                 patch.object(release_approval.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'publication is blocked'):
                    release_approval.check_environment()

    def test_check_only_reads_the_expected_environment(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(protected_environment()), '')
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'mochiOS/libs'}), \
             patch.object(release_approval.subprocess, 'run', return_value=result) as command:
            release_approval.check_environment()
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0], [
            'gh', 'api', 'repos/mochiOS/libs/environments/library-release',
        ])
        self.assertNotIn('shell', command.call_args.kwargs)

    def test_fork_repository_is_rejected_before_api_access(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'someone/libs'}), \
             patch.object(release_approval.subprocess, 'run') as command:
            with self.assertRaises(ValueError):
                release_approval.check_environment()
            command.assert_not_called()

    def test_removed_approval_is_rejected_on_a_second_check(self):
        removed = copy.deepcopy(protected_environment())
        removed['protection_rules'] = []
        responses = [subprocess.CompletedProcess([], 0, json.dumps(value), '')
                     for value in [protected_environment(), removed]]
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'mochiOS/libs'}), \
             patch.object(release_approval.subprocess, 'run', side_effect=responses):
            release_approval.check_environment()
            with self.assertRaises(ValueError):
                release_approval.check_environment()


if __name__ == '__main__':
    unittest.main()
