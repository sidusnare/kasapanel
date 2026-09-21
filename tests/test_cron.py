# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.cron`."""

import datetime
import unittest

from kasapanel import cron


def moment(text: str) -> datetime.datetime:
    """Parses a short local time for use in assertions.

    Args:
        text: A time such as ``2026-07-28 09:30``.

    Returns:
        The parsed time.
    """
    return datetime.datetime.strptime(text, '%Y-%m-%d %H:%M')


class ParseTest(unittest.TestCase):
    """Field parsing and validation."""

    def test_every_minute(self):
        """A bare star in every field matches any minute."""
        expression = cron.CronExpression.parse('* * * * *')
        self.assertTrue(expression.matches(moment('2026-07-28 09:30')))
        self.assertEqual(len(expression.minutes), 60)

    def test_lists_ranges_and_steps(self):
        """Lists, ranges and steps expand to the right values."""
        expression = cron.CronExpression.parse('0,30 9-11 * * *')
        self.assertEqual(sorted(expression.minutes), [0, 30])
        self.assertEqual(sorted(expression.hours), [9, 10, 11])
        stepped = cron.CronExpression.parse('*/15 * * * *')
        self.assertEqual(sorted(stepped.minutes), [0, 15, 30, 45])
        ranged = cron.CronExpression.parse('0 8-18/6 * * *')
        self.assertEqual(sorted(ranged.hours), [8, 14])

    def test_names_are_accepted(self):
        """Month and weekday names work, and Sunday folds to zero."""
        expression = cron.CronExpression.parse('0 6 * Jan-Mar mon-fri')
        self.assertEqual(sorted(expression.months), [1, 2, 3])
        self.assertEqual(sorted(expression.weekdays), [1, 2, 3, 4, 5])
        sunday = cron.CronExpression.parse('0 0 * * 7')
        self.assertEqual(sorted(sunday.weekdays), [0])

    def test_keywords(self):
        """Keywords expand to their five field form."""
        daily = cron.CronExpression.parse('@daily')
        self.assertTrue(daily.matches(moment('2026-07-28 00:00')))
        self.assertFalse(daily.matches(moment('2026-07-28 00:01')))

    def test_bad_expressions(self):
        """Malformed expressions are rejected with a message."""
        for text in ('', '* * * *', '61 * * * *', '* * * * 9',
                     'every minute', '@yearlyish', '5-1 * * * *',
                     '*/0 * * * *', '0 0 * * mon-'):
            with self.assertRaises(cron.CronError):
                cron.CronExpression.parse(text)


class MatchTest(unittest.TestCase):
    """Matching and next run search."""

    def test_day_or_weekday_rule(self):
        """With both day fields restricted, either one may match."""
        expression = cron.CronExpression.parse('0 0 1 * mon')
        self.assertTrue(expression.matches(moment('2026-07-01 00:00')))
        self.assertTrue(expression.matches(moment('2026-07-06 00:00')))
        self.assertFalse(expression.matches(moment('2026-07-07 00:00')))

    def test_weekday_only(self):
        """With only the weekday restricted, the day must match it."""
        expression = cron.CronExpression.parse('30 22 * * sun')
        self.assertTrue(expression.matches(moment('2026-08-02 22:30')))
        self.assertFalse(expression.matches(moment('2026-08-03 22:30')))

    def test_next_run(self):
        """The next run is the first later matching minute."""
        expression = cron.CronExpression.parse('0 6 * * *')
        self.assertEqual(expression.next_run(moment('2026-07-28 09:30')),
                         moment('2026-07-29 06:00'))
        quarter = cron.CronExpression.parse('*/15 * * * *')
        self.assertEqual(quarter.next_run(moment('2026-07-28 09:30')),
                         moment('2026-07-28 09:45'))

    def test_next_run_is_strictly_later(self):
        """A rule due right now returns the following firing."""
        expression = cron.CronExpression.parse('30 9 * * *')
        self.assertEqual(expression.next_run(moment('2026-07-28 09:30')),
                         moment('2026-07-29 09:30'))

    def test_impossible_dates(self):
        """A date that never happens has no next run."""
        never = cron.CronExpression.parse('0 0 30 2 *')
        self.assertIsNone(never.next_run(moment('2026-07-28 09:30')))

    def test_reboot_is_not_a_keyword(self):
        """@reboot was removed; it must not parse as anything."""
        with self.assertRaises(cron.CronError) as caught:
            cron.CronExpression.parse('@reboot')
        self.assertIn('unknown keyword', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
