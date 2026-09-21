# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The scheduling thread.

The scheduler wakes a moment after every minute boundary, reads each
device's schedule script and runs the rules that match the new minute.
Device calls happen in a small thread pool so one slow or unplugged
device cannot hold up the rest.

Times are the local time of the daemon host, exactly like cron, so a
daylight saving change behaves the way an operator expects: rules in a
skipped hour do not run, rules in a repeated hour run twice.
"""

import concurrent.futures
import datetime
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from kasapanel import devicestore
from kasapanel import kasabridge
from kasapanel import schedule as schedule_lib

_LOG = logging.getLogger(__name__)

# Wait this long past the minute boundary before evaluating rules, so a
# clock that is a few milliseconds fast cannot skip a minute.
TICK_OFFSET_SECONDS = 0.5

MAX_PARALLEL_DEVICES = 8


class Scheduler:
    """Runs per-device schedule scripts once a minute."""

    def __init__(self, store: devicestore.DeviceStore,
                 bridge: kasabridge.DeviceBridge, activity: Any,
                 settings: Any):
        """Initialises the scheduler.

        Args:
            store: The device inventory.
            bridge: The bridge used to talk to devices.
            activity: The :class:`kasapanel.activity.ActivityLog`.
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        self._store = store
        self._bridge = bridge
        self._activity = activity
        self._settings = settings
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._fired: Dict[Tuple[str, int], str] = {}
        self._last_tick: Optional[datetime.datetime] = None
        self._runs = 0

    def update_settings(self, settings: Any) -> None:
        """Replaces the settings used on the next tick.

        Args:
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        self._settings = settings

    @property
    def enabled(self) -> bool:
        """Returns whether schedules are allowed to run at all."""
        return bool(getattr(self._settings, 'scheduler_enabled', True))

    def status(self) -> Dict[str, Any]:
        """Returns a JSON friendly view of the scheduler state."""
        with self._lock:
            last = self._last_tick
            runs = self._runs
        return {
            'enabled': self.enabled,
            'running': self._thread is not None and self._thread.is_alive(),
            'last_tick': last.isoformat(timespec='seconds') if last else '',
            'rules_run': runs,
        }

    def start(self) -> None:
        """Starts the scheduling thread."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, name='kasa-scheduler', daemon=True)
        self._thread.start()
        _LOG.info('scheduler started')

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the scheduling thread.

        Args:
            timeout: Seconds to wait for the thread to finish.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def _serve(self) -> None:
        """Sleeps to each minute boundary and evaluates the rules."""
        while not self._stop.is_set():
            delay = self._seconds_to_next_minute()
            if self._stop.wait(timeout=delay):
                break
            moment = datetime.datetime.now().replace(second=0, microsecond=0)
            try:
                self.run_minute(moment)
            except Exception as err:  # pylint: disable=broad-except
                _LOG.exception('scheduler tick failed: %s', err)
                self._activity.add(
                    f'Schedule check failed: {err}', level='error',
                    source='schedule')

    @staticmethod
    def _seconds_to_next_minute() -> float:
        """Returns the seconds to wait until just past the next minute."""
        now = datetime.datetime.now()
        seconds = 60 - now.second - now.microsecond / 1_000_000
        return max(seconds + TICK_OFFSET_SECONDS, 0.5)

    def _device_entries(
        self, record: devicestore.DeviceRecord
    ) -> List[schedule_lib.ScheduleEntry]:
        """Reads and parses the schedule script of one device.

        Args:
            record: The inventory record of the device.

        Returns:
            The parsed rules; problems are logged and skipped.
        """
        document = self._store.device_document(record.device_id)
        if not document.get('schedule_enabled', True):
            return []
        entries, problems = schedule_lib.parse_script(
            document.get('schedule', ''))
        for problem in problems:
            _LOG.warning('%s: schedule line %d is invalid: %s',
                         record.display_name, problem.line_number,
                         problem.message)
        return entries

    def run_minute(self, moment: datetime.datetime) -> int:
        """Runs every rule that matches one minute.

        Args:
            moment: The minute to evaluate, seconds already zeroed.

        Returns:
            How many rules were run.
        """
        with self._lock:
            previous = self._last_tick
            self._last_tick = moment
        if previous is not None:
            gap = (moment - previous).total_seconds()
            if gap > 90:
                _LOG.warning(
                    'the scheduler missed %d minute(s); rules due in that '
                    'window did not run', int(gap // 60) - 1)
        if not self.enabled:
            return 0
        stamp = moment.isoformat(timespec='minutes')
        work = []
        for record in self._store.enabled_records():
            for entry in self._device_entries(record):
                if not entry.expression.matches(moment):
                    continue
                key = (record.device_id, entry.line_number)
                with self._lock:
                    if self._fired.get(key) == stamp:
                        continue
                    self._fired[key] = stamp
                work.append((record, entry))
        self._dispatch(work)
        return len(work)

    def _dispatch(
        self,
        work: List[Tuple[devicestore.DeviceRecord,
                         schedule_lib.ScheduleEntry]],
    ) -> None:
        """Runs a batch of due rules in a small thread pool.

        Args:
            work: Pairs of device record and rule to run.
        """
        if not work:
            return
        workers = min(MAX_PARALLEL_DEVICES, len(work))
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix='kasa-rule') as pool:
            for record, entry in work:
                pool.submit(self._run_entry, record, entry)

    def _run_entry(self, record: devicestore.DeviceRecord,
                   entry: schedule_lib.ScheduleEntry) -> None:
        """Runs one rule and records the outcome.

        Args:
            record: The inventory record of the device.
            entry: The rule to run.
        """
        label = f'line {entry.line_number}: {entry.command}'
        try:
            result = self._bridge.run_action(
                record, entry.action, list(entry.arguments))
        except kasabridge.BridgeError as err:
            _LOG.warning('%s: %s failed: %s',
                         record.display_name, label, err)
            self._activity.add(
                f'{label} failed: {err}', level='error', source='schedule',
                device_id=record.device_id,
                device_name=record.display_name)
            return
        with self._lock:
            self._runs += 1
        _LOG.info('%s: %s -> %s', record.display_name, label, result)
        self._activity.add(
            f'{label} -> {result}', source='schedule',
            device_id=record.device_id, device_name=record.display_name)
