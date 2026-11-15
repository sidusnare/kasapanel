# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
"""Tests for :mod:`kasapanel.schedule` and :mod:`kasapanel.actions`."""

import asyncio
import datetime
import unittest
from collections.abc import Callable
from typing import Any

from kasapanel import actions, schedule


class FakeDevice:
    """A python-kasa device stub that records the order of calls.

    Only the handful of members the actions touch are provided.  The
    order matters as much as the result: a toggle that reads before it
    polls is wrong even when the cached answer happens to be right, so
    the tests assert on :attr:`calls` rather than only on the report.
    """

    def __init__(
        self,
        is_on: bool = False,
        on_update: Callable[['FakeDevice'], None] | None = None,
    ):
        """Initialises the stub.

        Args:
            is_on: The state the device starts out believing.
            on_update: Called by :meth:`update` to stand in for the
                world having moved on since the last poll.
        """
        self.is_on = is_on
        self.calls: list[str] = []
        self._on_update = on_update

    async def update(self) -> None:
        """Polls the device, applying whatever changed underneath."""
        self.calls.append('update')
        if self._on_update is not None:
            self._on_update(self)

    async def turn_on(self) -> None:
        """Switches the device on."""
        self.calls.append('turn_on')
        self.is_on = True

    async def turn_off(self) -> None:
        """Switches the device off."""
        self.calls.append('turn_off')
        self.is_on = False

    def modules(self) -> dict[str, Any]:
        """Returns no optional modules.

        Returns:
            An empty mapping; this stub is a plain switch.
        """
        return {}


SCRIPT = '\n'.join(
    (
        '# kitchen lamp',
        '0 6 * * mon-fri   on          # weekday mornings',
        '30 22 * * *       off',
        '*/15 9-17 * * *   brightness 40',
        '@daily            refresh',
    )
)


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
        entries, problems = schedule.parse_script(
            '\n'.join(
                (
                    '0 6 * * * on',
                    '99 6 * * * off',
                    '0 6 * * * explode',
                    '0 6 * * *',
                    '@daily brightness 900',
                    '@daily brightness',
                )
            )
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            [problem.line_number for problem in problems], [2, 3, 4, 5, 6]
        )
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
        for name, arguments in (
            ('brightness', ['0']),
            ('brightness', ['x']),
            ('brightness', []),
            ('on', ['now']),
            ('colour', ['10', '20']),
            ('led', ['maybe']),
            ('temperature', ['200']),
        ):
            with self.assertRaises(actions.ActionError):
                actions.parse(name, arguments)

    def test_catalogue_is_complete(self):
        """Every action carries a usage line and a summary."""
        entries = actions.catalogue()
        self.assertEqual(len(entries), len(actions.ACTIONS))
        for entry in entries:
            self.assertTrue(entry['usage'])
            self.assertTrue(entry['summary'].endswith('.'))


class RunActionTest(unittest.TestCase):
    """Running an action against a device."""

    def test_toggle_polls_before_it_decides(self):
        """A stale cache would invert the wrong state.

        The device is the authority on whether it is on.  If the plug
        was switched by hand since the last poll, toggling on the
        cached answer turns it the way it already went and the
        activity log records the opposite of what happened.
        """
        device = FakeDevice(
            is_on=False, on_update=lambda d: setattr(d, 'is_on', True)
        )
        report = asyncio.run(actions.execute(device, 'toggle', []))
        self.assertEqual(report, 'toggled off')
        self.assertEqual(device.calls[:2], ['update', 'turn_off'])

    def test_toggle_reads_the_device_not_the_cache(self):
        """Both directions, with the poll in front of the decision."""
        for cached, fresh, report, command in (
            (True, False, 'toggled on', 'turn_on'),
            (False, True, 'toggled off', 'turn_off'),
            (False, False, 'toggled on', 'turn_on'),
            (True, True, 'toggled off', 'turn_off'),
        ):
            with self.subTest(cached=cached, fresh=fresh):
                device = FakeDevice(
                    is_on=cached,
                    on_update=lambda d, fresh=fresh: setattr(d, 'is_on', fresh),
                )
                self.assertEqual(
                    asyncio.run(actions.execute(device, 'toggle', [])), report
                )
                self.assertEqual(device.calls[0], 'update')
                self.assertIn(command, device.calls)

    def test_the_other_switches_do_not_poll_first(self):
        """on and off need no reading, so they cost no extra round trip."""
        for action, command in (('on', 'turn_on'), ('off', 'turn_off')):
            with self.subTest(action=action):
                device = FakeDevice(is_on=False)
                asyncio.run(actions.execute(device, action, []))
                self.assertEqual(device.calls[0], command)

    def test_every_action_leaves_the_state_fresh(self):
        """execute polls afterwards so the log records what is true."""
        device = FakeDevice(is_on=False)
        asyncio.run(actions.execute(device, 'on', []))
        self.assertEqual(device.calls[-1], 'update')


if __name__ == '__main__':
    unittest.main()
