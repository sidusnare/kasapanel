# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.metrics`."""

import datetime
import os
import tempfile
import unittest
from typing import Any, Dict, List

from kasapanel import app as app_lib
from kasapanel import jsonstore
from kasapanel import metrics


class RefusingBridge:
    """A bridge that fails the test if anything tries to use a device.

    The endpoint promises never to poll, so the strongest way to check
    it is to make polling impossible.
    """

    def __init__(self, test: unittest.TestCase):
        """Remembers the test so it can be failed from here.

        Args:
            test: The running test case.
        """
        self.test = test

    def _refuse(self, *args, **kwargs):
        """Fails the test.

        Args:
            *args: Ignored.
            **kwargs: Ignored.
        """
        del args, kwargs
        self.test.fail('rendering metrics reached out to a device')

    probe = _refuse
    snapshot = _refuse
    run_action = _refuse
    discover = _refuse

    def update_settings(self, settings: Any) -> None:
        """Accepts settings and ignores them.

        Args:
            settings: Unused.
        """
        del settings

    def forget(self, host: str) -> None:
        """Ignores a dropped connection.

        Args:
            host: Unused.
        """
        del host


def parse(text: str) -> Dict[str, List[Any]]:
    """Reads exposition text into something a test can assert on.

    Args:
        text: The rendered document.

    Returns:
        Metric names mapped to a list of (labels, value) pairs.
    """
    found: Dict[str, List[Any]] = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        head, _, value = line.rpartition(' ')
        name, _, labels = head.partition('{')
        found.setdefault(name, []).append((labels.rstrip('}'), value))
    return found


class MetricsTest(unittest.TestCase):
    """What the endpoint publishes."""

    def setUp(self):
        """Builds an application with one device and cached state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_path = os.path.join(self.tmp.name, 'config.json')
        jsonstore.write_json(config_path, {
            'state_dir': os.path.join(self.tmp.name, 'state'),
            'log_level': 'CRITICAL',
            'poll_seconds': 30,
        })
        self.app = app_lib.Application(config_path)
        self.app.load()
        self.app.bridge = RefusingBridge(self)
        self.record = self.app.devices.add('10.0.0.5', alias='Desk lamp')
        self.app.devices.set_schedule(
            self.record.device_id, '0 6 * * * on\n@daily off\n')

    def cache(self, **state) -> None:
        """Puts a snapshot in the cache, as a poll would.

        Args:
            **state: Snapshot fields.
        """
        snapshot = {
            'reachable': True,
            'is_on': True,
            'polled_at': datetime.datetime.now().isoformat(
                timespec='seconds'),
        }
        snapshot.update(state)
        self.app.store_state(self.record, snapshot)

    def render(self) -> Dict[str, List[Any]]:
        """Renders and parses the document.

        Returns:
            The parsed metrics.
        """
        return parse(metrics.render(self.app))

    def test_daemon_metrics_are_present(self):
        """The basics about the daemon itself."""
        found = self.render()
        self.assertIn('kasapanel_build_info', found)
        self.assertIn('kasapanel_uptime_seconds', found)
        self.assertIn('kasapanel_sessions_active', found)
        self.assertIn('kasapanel_devices_total', found)
        self.assertEqual(found['kasapanel_devices_total'][0][1], '1')

    def test_every_family_is_typed(self):
        """Prometheus wants a TYPE line before each family."""
        text = metrics.render(self.app)
        declared = {line.split()[2] for line in text.splitlines()
                    if line.startswith('# TYPE ')}
        for line in text.splitlines():
            if line.startswith('#') or not line:
                continue
            name = line.partition('{')[0].partition(' ')[0]
            self.assertIn(name, declared, f'{name} has no TYPE line')

    def test_device_state_is_published(self):
        """A cached snapshot turns into readings."""
        self.cache(brightness=40, power_watts=12.5, rssi=-53,
                   colour_temperature=2700, energy_today_kwh=0.4)
        found = self.render()
        self.assertEqual(found['kasapanel_device_on'][0][1], '1')
        self.assertEqual(found['kasapanel_device_reachable'][0][1], '1')
        self.assertEqual(found['kasapanel_device_brightness_percent'][0][1],
                         '40')
        self.assertEqual(found['kasapanel_device_power_watts'][0][1], '12.5')
        self.assertEqual(found['kasapanel_device_signal_dbm'][0][1], '-53')
        self.assertEqual(
            found['kasapanel_device_colour_temperature_kelvin'][0][1], '2700')

    def test_readings_a_device_does_not_have_are_absent(self):
        """A plug with no dimmer publishes no brightness at all."""
        self.cache()
        found = self.render()
        self.assertNotIn('kasapanel_device_brightness_percent', found)
        self.assertNotIn('kasapanel_device_power_watts', found)

    def test_schedule_facts_need_no_poll(self):
        """Schedule metrics come from the device file."""
        found = self.render()
        self.assertEqual(found['kasapanel_device_schedule_rules'][0][1], '2')
        self.assertEqual(found['kasapanel_device_schedule_problems'][0][1],
                         '0')
        self.assertIn('kasapanel_device_next_run_timestamp_seconds', found)

    def test_a_device_never_polled_still_appears(self):
        """It is listed, with no state rather than invented state."""
        found = self.render()
        self.assertIn('kasapanel_device_info', found)
        self.assertNotIn('kasapanel_device_reachable', found)
        self.assertNotIn('kasapanel_device_state_age_seconds', found)

    def test_stale_readings_are_served_with_their_age(self):
        """Old values are published, and dated, rather than withheld."""
        old = (datetime.datetime.now()
               - datetime.timedelta(minutes=42)).isoformat(
                   timespec='seconds')
        self.cache(polled_at=old, is_on=True)
        found = self.render()
        self.assertEqual(found['kasapanel_device_on'][0][1], '1')
        age = int(found['kasapanel_device_state_age_seconds'][0][1])
        self.assertGreaterEqual(age, 42 * 60)
        self.assertLess(age, 42 * 60 + 120)

    def test_rendering_never_touches_a_device(self):
        """The bridge fails the test if it is called at all."""
        self.cache()
        for _ in range(5):
            metrics.render(self.app)

    def test_labels_are_escaped(self):
        """A hostile device name cannot break the format."""
        self.app.devices.update(self.record.device_id,
                                {'alias': 'Bad "name"\\ here'})
        text = metrics.render(self.app)
        self.assertIn(r'name="Bad \"name\"\\ here"', text)

    def test_nothing_secret_is_published(self):
        """No credentials, tokens or key material."""
        self.app.apply_settings({'device_username': 'ada',
                                 'device_password': 'hunter2'})
        session = self.app.sessions.create('ada', remote='10.0.0.9')
        self.cache()
        text = metrics.render(self.app).lower()
        self.assertNotIn('hunter2', text)
        self.assertNotIn(session.token.lower(), text)
        self.assertNotIn(session.csrf_token.lower(), text)
        self.assertNotIn('ada', text)
        self.assertIn('kasapanel_sessions_active 1', metrics.render(self.app))

    def test_the_document_ends_with_a_newline(self):
        """Prometheus wants a trailing newline."""
        self.assertTrue(metrics.render(self.app).endswith('\n'))


class TimestampTest(unittest.TestCase):
    """Turning the daemon's local timestamps into Unix time."""

    def test_a_naive_timestamp_is_read_as_local(self):
        """The daemon writes local time, because schedules run on it."""
        # pylint: disable=protected-access
        moment = datetime.datetime(2026, 7, 30, 12, 0)
        self.assertEqual(metrics._stamp(moment.isoformat()),
                         moment.astimezone().timestamp())

    def test_an_empty_or_broken_timestamp_is_none(self):
        """Nothing to read means no sample rather than a wrong one."""
        # pylint: disable=protected-access
        self.assertIsNone(metrics._stamp(''))
        self.assertIsNone(metrics._stamp('not a time'))


if __name__ == '__main__':
    unittest.main()
