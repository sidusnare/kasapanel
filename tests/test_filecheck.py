# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.filecheck`."""

import os
import shutil
import stat
import tempfile
import unittest

from kasapanel import app as app_lib
from kasapanel import filecheck
from kasapanel import jsonstore


def find(reports, purpose):
    """Pulls one report out of a list.

    Args:
        reports: Reports from :func:`filecheck.inspect`.
        purpose: The purpose to look for.

    Returns:
        The matching report, or None.
    """
    for report in reports:
        if report['purpose'] == purpose:
            return report
    return None


class FileCheckTest(unittest.TestCase):
    """What the daemon can and cannot write."""

    def setUp(self):
        """Builds an application in a scratch directory."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.state = os.path.join(self.tmp, 'state')
        jsonstore.write_json(self.config_path, {
            'state_dir': self.state,
            'log_level': 'CRITICAL',
        })
        self.app = app_lib.Application(self.config_path)
        self.app.load()

    def test_a_healthy_installation_reports_no_problems(self):
        """Everything writable means nothing to say."""
        reports = filecheck.inspect(self.app)
        writable = [r for r in reports if r['access'] == 'write']
        self.assertTrue(writable)
        self.assertEqual(filecheck.problems(writable), [])

    def test_the_paths_it_checks(self):
        """Every file the daemon writes is covered."""
        purposes = {report['purpose']
                    for report in filecheck.inspect(self.app)}
        self.assertEqual(
            purposes,
            {'configuration', 'inventory', 'schedules', 'TLS certificate',
             'TLS key'})

    def test_the_log_file_is_checked_when_configured(self):
        """A log file only appears in the report when there is one."""
        self.app.apply_settings(
            {'log_file': os.path.join(self.tmp, 'logs', 'panel.log')})
        report = find(filecheck.inspect(self.app), 'log file')
        self.assertIsNotNone(report)
        self.assertEqual(report['source'], 'configured')

    def test_the_pid_file_is_checked_when_there_is_one(self):
        """The pid file is checked because the daemon removes it."""
        self.app.pidfile = os.path.join(self.tmp, 'kasapanel.pid')
        report = find(filecheck.inspect(self.app), 'pid file')
        self.assertIsNotNone(report)
        self.assertTrue(report['ok'])

    def test_configured_and_default_are_told_apart(self):
        """Where a value came from is part of the report."""
        jsonstore.write_json(self.config_path, {
            'state_dir': self.state,
            'log_level': 'CRITICAL',
            'tls_certificate': os.path.join(self.tmp, 'mine.crt'),
        })
        app = app_lib.Application(self.config_path)
        app.load()
        reports = filecheck.inspect(app)
        self.assertEqual(find(reports, 'TLS certificate')['source'],
                         'configured')
        self.assertEqual(find(reports, 'TLS key')['source'], 'default')

    @unittest.skipIf(os.getuid() == 0, 'root can write anything')
    def test_an_unwritable_directory_is_caught(self):
        """The directory matters, not only the file."""
        os.chmod(self.state, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.state, 0o700)
        report = find(filecheck.inspect(self.app), 'inventory')
        self.assertFalse(report['ok'])
        self.assertIn('directory', report['problem'])

    @unittest.skipIf(os.getuid() == 0, 'root can write anything')
    def test_a_writable_file_in_a_read_only_directory_still_fails(self):
        """An atomic write needs the directory, and this is the trap."""
        path = os.path.join(self.tmp, 'locked', 'config.json')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{}')
        os.chmod(path, 0o666)
        os.chmod(os.path.dirname(path), stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, os.path.dirname(path), 0o700)
        # pylint: disable=protected-access
        report = filecheck._examine(path, 'configuration', 'configured')
        self.assertFalse(report['ok'])
        self.assertIn('atomic write', report['problem'])

    def test_a_missing_directory_is_reported(self):
        """A log file nobody can create is caught before it is needed."""
        # pylint: disable=protected-access
        report = filecheck._examine('/proc/nowhere/panel.log', 'log file',
                                    'configured')
        self.assertFalse(report['ok'])
        self.assertIn('does not exist', report['problem'])
        self.assertIn('rotation', report['consequence'])

    def test_missing_tls_material_is_a_read_problem(self):
        """The TLS pair is read, not written, so it reads differently."""
        report = find(filecheck.inspect(self.app), 'TLS key')
        self.assertEqual(report['access'], 'read')
        self.assertFalse(report['ok'])
        self.assertIn('not there', report['problem'])

    def test_findings_are_kept_on_the_application(self):
        """The API serves what the check found."""
        self.app.check_files()
        self.assertTrue(self.app.file_checks)
        self.assertEqual(self.app.file_checks,
                         filecheck.inspect(self.app))

    def test_problems_reach_the_activity_log(self):
        """An operator sees them in the panel as well as the log."""
        self.app.check_files()
        messages = [record['message'] for record in self.app.activity.records()]
        self.assertTrue(any('not usable' in message
                            for message in messages))

    def test_saving_settings_rechecks(self):
        """Moving the log file moves what has to be writable."""
        self.app.check_files()
        self.app.apply_settings({'log_file': '/proc/nowhere/panel.log'})
        report = find(self.app.file_checks, 'log file')
        self.assertIsNotNone(report)
        self.assertFalse(report['ok'])


if __name__ == '__main__':
    unittest.main()
