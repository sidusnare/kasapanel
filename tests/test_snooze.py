# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.snooze` and the scheduler honouring it."""

import datetime
import os
import tempfile
import unittest
from typing import Any, List

from kasapanel import activity as activity_lib
from kasapanel import devicestore
from kasapanel import scheduler as scheduler_lib
from kasapanel import snooze

# A Monday evening, so the relative forms have something to work from.
NOW = datetime.datetime(2026, 9, 21, 22, 15, 10)


class SpanTest(unittest.TestCase):
    """Time spans, counted from now."""

    def test_the_menu_presets(self):
        """Every preset the card menu sends means what it says."""
        presets = {
            '5min': datetime.timedelta(minutes=5),
            '15min': datetime.timedelta(minutes=15),
            '30min': datetime.timedelta(minutes=30),
            '1h': datetime.timedelta(hours=1),
            '3h': datetime.timedelta(hours=3),
            '6h': datetime.timedelta(hours=6),
            '12h': datetime.timedelta(hours=12),
            '1d': datetime.timedelta(days=1),
            '1w': datetime.timedelta(weeks=1),
        }
        for text, span in presets.items():
            self.assertEqual(snooze.resolve(text, NOW), NOW + span, text)

    def test_components_add_up(self):
        """Several components are summed, spaced or not."""
        expected = datetime.timedelta(hours=2, minutes=30)
        for text in ('2h 30min', '2h30m', '2 hours 30 minutes', '+2h 30min',
                     '150min', '9000', '2.5h', '2h 30min left'):
            self.assertEqual(snooze.resolve(text, NOW), NOW + expected, text)

    def test_units_are_case_sensitive(self):
        """Lower case m is minutes and upper case M is months."""
        self.assertEqual(snooze.parse_span('1m'),
                         datetime.timedelta(minutes=1))
        self.assertGreater(snooze.parse_span('1M'),
                           datetime.timedelta(days=30))

    def test_unknown_units_are_named(self):
        """The unit that was not understood is quoted back."""
        with self.assertRaisesRegex(snooze.SnoozeError, '"fortnights"'):
            snooze.parse_span('2 fortnights')


class TimestampTest(unittest.TestCase):
    """Timestamps, the other half of systemd.time."""

    def test_forms_systemd_accepts(self):
        """Dates, times, the day words and epoch seconds."""
        cases = {
            'tomorrow 07:00': datetime.datetime(2026, 9, 22, 7, 0),
            'tomorrow': datetime.datetime(2026, 9, 22, 0, 0),
            '23:30': datetime.datetime(2026, 9, 21, 23, 30),
            '23:30:15': datetime.datetime(2026, 9, 21, 23, 30, 15),
            '2026-09-25 09:00': datetime.datetime(2026, 9, 25, 9, 0),
            '2026-09-25T09:00': datetime.datetime(2026, 9, 25, 9, 0),
            '2026-09-25': datetime.datetime(2026, 9, 25, 0, 0),
            'Fri 2026-09-25 09:00': datetime.datetime(2026, 9, 25, 9, 0),
            'friday 2026-09-25 09:00': datetime.datetime(2026, 9, 25, 9, 0),
            'Mon 23:00': datetime.datetime(2026, 9, 21, 23, 0),
        }
        for text, moment in cases.items():
            self.assertEqual(snooze.resolve(text, NOW), moment, text)
        epoch = datetime.datetime(2026, 9, 22, 12, 0)
        self.assertEqual(
            snooze.resolve(f'@{int(epoch.timestamp())}', NOW), epoch)

    def test_utc_is_converted_to_local_time(self):
        """A trailing UTC means UTC, returned as local time."""
        moment = snooze.resolve('2026-09-25 09:00 UTC', NOW)
        expected = (datetime.datetime(2026, 9, 25, 9, 0,
                                      tzinfo=datetime.timezone.utc)
                    .astimezone().replace(tzinfo=None))
        self.assertEqual(moment, expected)

    def test_a_weekday_must_match_the_date(self):
        """systemd refuses a weekday that contradicts the date."""
        with self.assertRaisesRegex(snooze.SnoozeError, 'Friday'):
            snooze.resolve('Thu 2026-09-25 09:00', NOW)

    def test_a_passed_time_today_is_not_tomorrow(self):
        """A bare time keeps systemd's meaning, with a hint."""
        with self.assertRaisesRegex(snooze.SnoozeError, 'tomorrow 07:00'):
            snooze.resolve('07:00', NOW)

    def test_the_past_is_refused(self):
        """Nothing can be snoozed until a moment already gone."""
        for text in ('now', 'yesterday', '-5min', '3h ago',
                     '2020-01-01 00:00', '@0'):
            with self.assertRaisesRegex(snooze.SnoozeError, 'future', msg=text):
                snooze.resolve(text, NOW)

    def test_nonsense_is_refused(self):
        """Every kind of mistake is a SnoozeError, never a crash."""
        for text in ('', '   ', 'soon', '25:00', '2026-02-30', '1x',
                     'tomorrow tomorrow 07:00', '@nonsense', '1e400h',
                     '9' * 400):
            with self.assertRaises(snooze.SnoozeError, msg=text):
                snooze.resolve(text, NOW)

    def test_there_is_a_ceiling(self):
        """A snooze longer than a year is almost certainly a typo."""
        with self.assertRaisesRegex(snooze.SnoozeError, 'disable'):
            snooze.resolve('2y', NOW)

    def test_the_examples_all_work(self):
        """Every example shown to the operator is accepted."""
        for example in snooze.reference(NOW)['examples']:
            snooze.resolve(example['text'], NOW)


class StoredTest(unittest.TestCase):
    """Reading back what was stored."""

    def test_active_until(self):
        """Only a snooze still to come is in force."""
        later = (NOW + datetime.timedelta(hours=1)).isoformat()
        earlier = (NOW - datetime.timedelta(hours=1)).isoformat()
        self.assertEqual(snooze.active_until(later, NOW),
                         NOW + datetime.timedelta(hours=1))
        self.assertIsNone(snooze.active_until(earlier, NOW))
        self.assertIsNone(snooze.active_until('', NOW))
        # A hand edit that cannot be read must not stop a schedule.
        self.assertIsNone(snooze.active_until('next tuesday', NOW))

    def test_first_minute(self):
        """Rules come back at the first whole minute after the snooze."""
        self.assertEqual(snooze.first_minute(NOW),
                         datetime.datetime(2026, 9, 21, 22, 16))
        exact = datetime.datetime(2026, 9, 21, 22, 15)
        self.assertEqual(snooze.first_minute(exact), exact)


class Recorder:
    """A device bridge that only remembers what it was asked to do."""

    def __init__(self):
        """Starts with no commands."""
        self.commands: List[str] = []

    def run_action(self, record: Any, action: str,
                   arguments: List[str]) -> str:
        """Records one command.

        Args:
            record: The device record.
            action: The action name.
            arguments: The action arguments.

        Returns:
            A result message.
        """
        self.commands.append(' '.join([record.host, action] + arguments))
        return 'ok'


class SchedulerTest(unittest.TestCase):
    """The scheduler leaves a snoozed device alone."""

    def setUp(self):
        """Builds a store with one device that switches on every minute."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = devicestore.DeviceStore(
            os.path.join(self.tmp.name, 'devices.json'),
            os.path.join(self.tmp.name, 'devices'))
        self.record = self.store.add('10.0.0.5')
        self.store.set_schedule(self.record.device_id, '* * * * * on')
        self.bridge = Recorder()
        self.activity = activity_lib.ActivityLog()
        self.scheduler = scheduler_lib.Scheduler(
            self.store, self.bridge, self.activity, object())

    def test_snoozed_rules_do_not_run(self):
        """Rules are skipped until the snooze ends, then run again."""
        start = datetime.datetime(2026, 9, 21, 22, 15)
        self.store.snooze(self.record.device_id,
                          start + datetime.timedelta(minutes=2, seconds=30))
        ran = [self.scheduler.run_minute(start + datetime.timedelta(minutes=n))
               for n in range(5)]
        self.assertEqual(ran, [0, 0, 0, 1, 1])
        self.assertEqual(len(self.bridge.commands), 2)

    def test_an_ended_snooze_is_tidied_away_once(self):
        """The resumption is noted in the activity log a single time."""
        start = datetime.datetime(2026, 9, 21, 22, 15)
        self.store.snooze(self.record.device_id, start)
        self.scheduler.run_minute(start)
        self.scheduler.run_minute(start + datetime.timedelta(minutes=1))
        document = self.store.device_document(self.record.device_id)
        self.assertNotIn('snoozed_until', document)
        ended = [item for item in self.activity.records(limit=50)
                 if 'snooze ended' in item['message']]
        self.assertEqual(len(ended), 1)

    def test_tidying_does_not_clear_a_newer_snooze(self):
        """A snooze set since the scheduler read the file survives."""
        start = datetime.datetime(2026, 9, 21, 22, 15)
        self.store.snooze(self.record.device_id,
                          start + datetime.timedelta(hours=1))
        self.assertFalse(self.store.unsnooze(self.record.device_id,
                                             ended_by=start))
        self.assertTrue(self.store.unsnooze(self.record.device_id))
        self.assertFalse(self.store.unsnooze(self.record.device_id))


if __name__ == '__main__':
    unittest.main()
