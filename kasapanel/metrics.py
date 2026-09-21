# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The Prometheus exposition endpoint.

Two rules shape this module.

**Scraping never touches a device.**  Everything here comes from the
snapshot cache the poller and the scheduler fill in; a scrape reads what
the daemon already knows and nothing else.  A monitoring system that
polls every fifteen seconds must not turn into fifteen-second traffic to
every plug in the house, and a device that is slow or asleep must not be
able to hold a scrape open.  When a device has not been polled recently
the last known values are served as they are, with
``kasapanel_device_state_age_seconds`` alongside them so the scraper can
decide for itself how much to trust them.  A device that has never been
polled reports what is known about it from the inventory and no state.

**Nothing secret is published.**  The endpoint is unauthenticated, so it
carries no user names, no passwords, no key material and no session
tokens.  Counts of sessions, yes; who holds them, no.  Device names,
addresses and models are published, because they are what the numbers
would otherwise be meaningless without.
"""

import datetime
import logging
from typing import Any, Dict, List, Optional

from kasapanel import __version__

_LOG = logging.getLogger(__name__)

CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8'

PREFIX = 'kasapanel'


def _escape(value: str) -> str:
    """Escapes a label value for the exposition format.

    Args:
        value: The raw label value.

    Returns:
        The value with backslashes, quotes and newlines escaped.
    """
    return (str(value)
            .replace('\\', '\\\\')
            .replace('"', '\\"')
            .replace('\n', '\\n'))


def _labels(pairs: Dict[str, Any]) -> str:
    """Renders a label set.

    Args:
        pairs: Label names mapped to values; empty values are kept, so
            a device without a model still matches the same series.

    Returns:
        The label set in braces, or an empty string when there are none.
    """
    if not pairs:
        return ''
    inner = ','.join(f'{name}="{_escape(value)}"'
                     for name, value in sorted(pairs.items()))
    return '{' + inner + '}'


def _stamp(text: str) -> Optional[float]:
    """Turns one of the daemon's timestamps into a Unix time.

    The daemon writes local time without a zone, because that is the
    clock its schedules run on.

    Args:
        text: An ISO 8601 timestamp, or an empty string.

    Returns:
        Seconds since the epoch, or None when there is nothing to read.
    """
    if not text:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


class Writer:
    """Collects metric families in exposition order."""

    def __init__(self):
        """Starts an empty document."""
        self._lines: List[str] = []
        self._declared: set = set()

    def add(self, name: str, value: Any, kind: str = 'gauge',
            help_text: str = '', labels: Dict[str, Any] = None) -> None:
        """Appends one sample, declaring the family on first use.

        Args:
            name: Metric name without the prefix.
            value: The sample value; None is skipped entirely, which is
                how an unknown reading stays absent rather than
                pretending to be zero.
            kind: Prometheus metric type.
            help_text: One line describing the metric.
            labels: Label set for this sample.
        """
        if value is None:
            return
        full = f'{PREFIX}_{name}'
        if full not in self._declared:
            self._declared.add(full)
            if help_text:
                self._lines.append(f'# HELP {full} {help_text}')
            self._lines.append(f'# TYPE {full} {kind}')
        if isinstance(value, bool):
            value = 1 if value else 0
        self._lines.append(f'{full}{_labels(labels or {})} {value}')

    def render(self) -> str:
        """Returns the document.

        Returns:
            The exposition text, newline terminated.
        """
        return '\n'.join(self._lines) + '\n'


def _daemon_metrics(writer: Writer, app: Any, now: float) -> None:
    """Writes the metrics about the daemon itself.

    Args:
        writer: The document being built.
        app: The application.
        now: Current Unix time.
    """
    writer.add('build_info', 1, 'gauge',
               'Always 1, labelled with the running version.',
               {'version': __version__})
    started = app.started_at.timestamp()
    writer.add('start_time_seconds', f'{started:.0f}', 'gauge',
               'When the daemon started, in Unix time.')
    writer.add('uptime_seconds', f'{now - started:.0f}', 'gauge',
               'How long the daemon has been running.')

    scheduler = app.scheduler
    if scheduler is not None:
        state = scheduler.status()
        writer.add('scheduler_enabled', bool(state.get('enabled')), 'gauge',
                   'Whether schedules are allowed to run.')
        writer.add('scheduler_running', bool(state.get('running')), 'gauge',
                   'Whether the minute tick thread is alive.')
        writer.add('scheduler_rules_run_total',
                   int(state.get('rules_run', 0)), 'counter',
                   'Schedule rules dispatched since the daemon started.')
        writer.add('scheduler_last_tick_timestamp_seconds',
                   _fixed(_stamp(state.get('last_tick', ''))), 'gauge',
                   'When the scheduler last evaluated a minute.')

    writer.add('sessions_active', len(app.sessions.active()), 'gauge',
               'Signed-in sessions. No identifying detail is published.')

    counts: Dict[str, int] = {'info': 0, 'warning': 0, 'error': 0}
    for record in app.activity.records(limit=100000):
        counts[record['level']] = counts.get(record['level'], 0) + 1
    for level, total in sorted(counts.items()):
        writer.add('activity_records', total, 'gauge',
                   'Records held in the in-memory activity log.',
                   {'level': level})

    writer.add('pam_available', _pam_available(), 'gauge',
               'Whether the daemon can check passwords at all.')
    helper = getattr(app, 'helper', None)
    if helper is not None:
        writer.add('privileged_helper_alive', bool(helper.alive), 'gauge',
                   'Whether the privileged authentication helper is up.')
    account = getattr(app, 'daemon_user', {}) or {}
    if account:
        writer.add('daemon_user_last_resort',
                   bool(account.get('last_resort')), 'gauge',
                   'Whether the daemon fell back to the nobody account.')

    writer.add('poll_interval_seconds',
               int(getattr(app.settings, 'poll_seconds', 0)), 'gauge',
               'Configured interval between device polls.')

    watcher = getattr(app, 'certwatch', None)
    if watcher is not None:
        status = watcher.status()
        expiry = _stamp(status.get('expires', ''))
        writer.add('tls_certificate_expiry_timestamp_seconds',
                   _fixed(expiry), 'gauge',
                   'When the certificate being served stops being valid.')
        if expiry is not None:
            writer.add('tls_certificate_seconds_remaining',
                       f'{expiry - now:.0f}', 'gauge',
                       'Time left on the certificate being served.')
        writer.add('tls_certificate_reloads_total',
                   int(status.get('reloads', 0)), 'counter',
                   'Certificates loaded without restarting the daemon.')


def _pam_available() -> bool:
    """Reports whether PAM could be imported.

    Returns:
        True when password checking is possible.
    """
    from kasapanel import auth  # pylint: disable=import-outside-toplevel
    return bool(auth.PAM_AVAILABLE)


def _fixed(value: Optional[float]) -> Optional[str]:
    """Formats a Unix time without exponent notation.

    Args:
        value: Seconds since the epoch, or None.

    Returns:
        The formatted value, or None.
    """
    return None if value is None else f'{value:.0f}'


def _device_metrics(writer: Writer, app: Any, now: float) -> None:
    """Writes one set of metrics per configured device.

    Nothing here polls: every value is either from the inventory, from
    the device's own file, or from the cached snapshot.

    Args:
        writer: The document being built.
        app: The application.
        now: Current Unix time.
    """
    from kasapanel import schedule as schedule_lib  # pylint: disable=C0415

    records = app.devices.records()
    reachable = 0
    switched_on = 0
    enabled = 0

    for record in records:
        state = app.state_of(record.device_id)
        document = app.devices.device_document(record.device_id)
        labels = {
            'device': record.device_id,
            'host': record.host,
            'name': record.display_name,
        }
        info_labels = dict(labels)
        info_labels['model'] = record.model or ''
        info_labels['type'] = record.device_type or ''
        writer.add('device_info', 1, 'gauge',
                   'Always 1, labelled with what the device is.',
                   info_labels)
        writer.add('device_enabled', record.enabled, 'gauge',
                   'Whether the panel manages this device.', labels)
        if record.enabled:
            enabled += 1

        if state:
            is_up = bool(state.get('reachable'))
            writer.add('device_reachable', is_up, 'gauge',
                       'Whether the device answered when last polled.',
                       labels)
            if is_up:
                reachable += 1
            writer.add('device_on', bool(state.get('is_on')), 'gauge',
                       'Whether the outlet or lamp is energised.', labels)
            if state.get('is_on'):
                switched_on += 1
            writer.add('device_brightness_percent',
                       state.get('brightness'), 'gauge',
                       'Brightness of a dimmable device.', labels)
            writer.add('device_colour_temperature_kelvin',
                       state.get('colour_temperature'), 'gauge',
                       'Colour temperature of a tunable white device.',
                       labels)
            writer.add('device_power_watts', state.get('power_watts'),
                       'gauge', 'Instantaneous power draw.', labels)
            writer.add('device_energy_today_kwh',
                       state.get('energy_today_kwh'), 'counter',
                       'Energy used today, as the device reports it.',
                       labels)
            writer.add('device_energy_total_kwh',
                       state.get('energy_total_kwh'), 'counter',
                       'Lifetime energy, as the device reports it.', labels)
            writer.add('device_signal_dbm', state.get('rssi'), 'gauge',
                       'Wi-Fi signal strength.', labels)
            writer.add('device_on_since_timestamp_seconds',
                       _fixed(_stamp(state.get('on_since', ''))), 'gauge',
                       'When the device was last switched on.', labels)
            polled = _stamp(state.get('polled_at', ''))
            writer.add('device_last_poll_timestamp_seconds', _fixed(polled),
                       'gauge', 'When this snapshot was taken.', labels)
            if polled is not None:
                writer.add('device_state_age_seconds', f'{now - polled:.0f}',
                           'gauge',
                           'How old these readings are. Scraping does not '
                           'poll, so this grows between scheduled polls.',
                           labels)

        entries, problems = schedule_lib.parse_script(
            document.get('schedule', ''))
        writer.add('device_schedule_enabled',
                   bool(document.get('schedule_enabled', True)), 'gauge',
                   'Whether this device runs its schedule.', labels)
        writer.add('device_schedule_rules', len(entries), 'gauge',
                   'Rules in this device schedule.', labels)
        writer.add('device_schedule_problems', len(problems), 'gauge',
                   'Lines of the schedule that do not parse.', labels)
        upcoming = schedule_lib.next_runs(entries, count=1)
        if upcoming:
            writer.add('device_next_run_timestamp_seconds',
                       _fixed(_stamp(upcoming[0]['when'])), 'gauge',
                       'When this device next has a rule due.', labels)

    writer.add('devices_total', len(records), 'gauge',
               'Devices in the inventory.')
    writer.add('devices_enabled', enabled, 'gauge',
               'Devices the panel is managing.')
    writer.add('devices_reachable', reachable, 'gauge',
               'Devices that answered their last poll.')
    writer.add('devices_on', switched_on, 'gauge',
               'Devices energised at their last poll.')


def render(app: Any) -> str:
    """Builds the whole exposition document.

    Args:
        app: The application.

    Returns:
        Prometheus exposition text.
    """
    now = datetime.datetime.now().timestamp()
    writer = Writer()
    _daemon_metrics(writer, app, now)
    _device_metrics(writer, app, now)
    return writer.render()
