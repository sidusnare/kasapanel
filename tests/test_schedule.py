# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.schedule` and :mod:`kasapanel.actions`."""

import datetime
import unittest

from kasapanel import actions
from kasapanel import schedule


SCRIPT = '\n'.join((
    '# kitchen lamp',
    '0 6 * * mon-fri   on          # weekday mornings',
    '30 22 * * *       off',
    '*/15 9-17 * * *   brightness 40',
    '@daily            refresh',
))


class ParseTest(unittest.TestCase):
    """Script parsing."""

    def test_valid_script(self):
        """Comments are ignored and every rule is parsed."""
        entries, problems = schedule.parse_script(SCRIPT)
        self.assertEqual(problems, [])
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].action, 'on')
        self.assertEqual(entries[0].line_number, 2)
        self.assertEqual(entries[2].command, 'brightness 40')

    def test_problems_are_collected(self):
        """Every bad line is reported, with its line number."""
        entries, problems = schedule.parse_script('\n'.join((
            '0 6 * * * on',
            '99 6 * * * off',
            '0 6 * * * explode',
            '0 6 * * *',
            '@daily brightness 900',
            '@daily brightness',
        )))
        self.assertEqual(len(entries), 1)
        self.assertEqual([problem.line_number for problem in problems],
                         [2, 3, 4, 5, 6])
        self.assertIn('outside', problems[0].message)
        self.assertIn('unknown action', problems[1].message)

    def test_strict_parse_raises(self):
        """Strict parsing rejects a script with any bad line."""
        with self.assertRaises(schedule.ScheduleError):
            schedule.parse_strict('0 6 * * * nope')
        self.assertEqual(len(schedule.parse_strict(SCRIPT)), 4)

    def test_aliases_resolve(self):
        """Action aliases are stored under the canonical name."""
        entries, problems = schedule.parse_script('@daily dim 30')
        self.assertEqual(problems, [])
        self.assertEqual(entries[0].action, 'brightness')

    def test_summary_and_preview(self):
        """The summary reports the rules and the next few firings."""
        now = datetime.datetime(2026, 7, 28, 5, 0)
        summary = schedule.summarise(SCRIPT, now=now)
        self.assertTrue(summary['valid'])
        self.assertEqual(summary['rule_count'], 4)
        self.assertEqual(len(summary['next_runs']), 5)
        self.assertEqual(summary['next_runs'][0]['when'], '2026-07-28T06:00')

    def test_argument_checking(self):
        """Arguments are range checked before anything is stored."""
        spec, values = actions.parse('brightness', ['40'])
        self.assertEqual(spec.name, 'brightness')
        self.assertEqual(values, (40,))
        for name, arguments in (('brightness', ['0']),
                                ('brightness', ['x']),
                                ('brightness', []),
                                ('on', ['now']),
                                ('colour', ['10', '20']),
                                ('led', ['maybe']),
                                ('temperature', ['200'])):
            with self.assertRaises(actions.ActionError):
                actions.parse(name, arguments)

    def test_catalogue_is_complete(self):
        """Every action carries a usage line and a summary."""
        entries = actions.catalogue()
        self.assertEqual(len(entries), len(actions.ACTIONS))
        for entry in entries:
            self.assertTrue(entry['usage'])
            self.assertTrue(entry['summary'].endswith('.'))


if __name__ == '__main__':
    unittest.main()
