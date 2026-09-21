# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Application wiring and lifecycle.

:class:`Application` owns every long lived object and the threads that
drive them:

* the asyncio worker thread that talks to devices;
* the polling thread that keeps the dashboard fresh;
* the scheduling thread that runs the rules;
* the threaded HTTPS server, one thread per connection.

The JSON API layer only ever talks to this object, which keeps the HTTP
code free of device and storage details.
"""

import concurrent.futures
import datetime
import logging
import sys
import threading
from typing import Any, Dict, List, Optional

from kasapanel import actions
from kasapanel import activity as activity_lib
from kasapanel import auth
from kasapanel import config as config_lib
from kasapanel import devicestore
from kasapanel import filecheck
from kasapanel import kasabridge
from kasapanel import logsink
from kasapanel import schedule as schedule_lib

_LOG = logging.getLogger(__name__)

LOG_FORMAT = '%(asctime)s %(levelname)-7s %(name)s: %(message)s'

MAX_PARALLEL_POLLS = 8


def configure_logging(settings: config_lib.PanelConfig) -> None:
    """Points the root logger at the configured destination.

    Args:
        settings: The configuration in effect.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler: logging.Handler
    if settings.log_file:
        try:
            handler = logsink.ReopeningFileHandler(settings.log_file)
        except OSError as error:
            # A log file that cannot be opened is a reason to complain,
            # not a reason to refuse to start.  The file check reports
            # it properly a moment later.
            handler = logging.StreamHandler()
            print(f'kasapanel: cannot open the log file '
                  f'{settings.log_file}: {error}; logging to stderr',
                  file=sys.stderr)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))


class Application:
    """Everything the daemon owns, and the threads that drive it."""

    def __init__(self, config_path: str = ''):
        """Initialises the application without touching the network.

        Args:
            config_path: Configuration file; empty selects the default.
        """
        self.config_path = config_path
        self.config_store = config_lib.ConfigStore(config_path)
        self.settings = self.config_store.config
        self.activity = activity_lib.ActivityLog()
        self.devices = devicestore.DeviceStore(
            self.settings.devices_file, self.settings.device_dir)
        self.worker = kasabridge.AsyncWorker()
        self.bridge = kasabridge.DeviceBridge(self.worker, self.settings)
        self.authenticator = auth.Authenticator(self.settings)
        self.sessions = auth.SessionManager(self.settings)
        self.scheduler: Optional[Any] = None
        self.server: Optional[Any] = None
        self.certwatch: Optional[Any] = None
        self.helper: Optional[Any] = None
        self.daemon_user: Dict[str, Any] = {}
        self.pidfile: str = ''
        self.file_checks: List[Dict[str, Any]] = []
        self.started_at = datetime.datetime.now()
        self._states: Dict[str, Dict[str, Any]] = {}
        self._states_lock = threading.Lock()
        self._stop = threading.Event()
        self._poller: Optional[threading.Thread] = None

    def load(self) -> config_lib.PanelConfig:
        """Reads the configuration and the device inventory.

        Returns:
            The configuration now in effect.
        """
        # Imported here so that config, storage and scheduling can be
        # unit tested without pulling in the HTTP layer.
        from kasapanel import scheduler as scheduler_lib  # pylint: disable=import-outside-toplevel

        self.settings = self.config_store.load()
        configure_logging(self.settings)
        self.devices = devicestore.DeviceStore(
            self.settings.devices_file, self.settings.device_dir)
        self.devices.load()
        self.activity.resize(self.settings.history_limit)
        self.bridge.update_settings(self.settings)
        self.authenticator.update_settings(self.settings)
        self.sessions.update_settings(self.settings)
        self.scheduler = scheduler_lib.Scheduler(
            self.devices, self.bridge, self.activity, self.settings)
        _LOG.info('configuration loaded from %s', self.config_store.path)
        _LOG.info('state directory is %s', self.settings.state_dir)
        if not auth.PAM_AVAILABLE:
            report = auth.pam_diagnosis()
            _LOG.warning('nobody can sign in: %s', report['reason'])
            _LOG.warning('interpreter: %s', report['interpreter'])
            _LOG.warning('to fix it, run: %s', report['advice'])
        return self.settings

    def start(self) -> None:
        """Starts the worker, polling and scheduling threads."""
        self._stop.clear()
        self.worker.start()
        self._poller = threading.Thread(
            target=self._poll_forever, name='kasa-poller', daemon=True)
        self._poller.start()
        if self.scheduler is not None:
            self.scheduler.start()
        self.activity.add('Kasa Panel started', source='daemon')

    def stop(self) -> None:
        """Stops every background thread."""
        self._stop.set()
        if self.scheduler is not None:
            self.scheduler.stop()
        if self._poller is not None:
            self._poller.join(timeout=5)
            self._poller = None
        self.worker.stop()
        self.activity.add('Kasa Panel stopped', source='daemon')
        _LOG.info('stopped')

    def apply_settings(self, changes: Dict[str, Any]) -> config_lib.PanelConfig:
        """Saves changed settings and pushes them to every component.

        Changes to the listening address and port only take effect when
        the daemon is restarted.  A changed certificate path does not:
        the certificate watcher is told, and picks the new file up.

        Args:
            changes: Field names mapped to new values.

        Returns:
            The configuration now in effect.
        """
        self.settings = self.config_store.update(changes)
        self.activity.resize(self.settings.history_limit)
        self.bridge.update_settings(self.settings)
        self.authenticator.update_settings(self.settings)
        self.sessions.update_settings(self.settings)
        if self.scheduler is not None:
            self.scheduler.update_settings(self.settings)
        if self.certwatch is not None:
            self.certwatch.update_settings(self.settings)
        configure_logging(self.settings)
        # A changed log file or state directory can move a path the
        # daemon has to write, so look again rather than showing the
        # answer from start up.
        self.check_files()
        _LOG.info('settings updated')
        return self.settings

    def _poll_forever(self) -> None:
        """Polls every enabled device on the configured interval."""
        while not self._stop.is_set():
            try:
                self.poll_all()
            except Exception as err:  # pylint: disable=broad-except
                _LOG.exception('device poll failed: %s', err)
            interval = max(int(self.settings.poll_seconds), 5)
            if self._stop.wait(timeout=interval):
                break

    def poll_all(self) -> Dict[str, Dict[str, Any]]:
        """Polls every enabled device once, in parallel.

        Returns:
            Device identifiers mapped to their fresh snapshots.
        """
        records = self.devices.enabled_records()
        if not records:
            return {}
        workers = min(MAX_PARALLEL_POLLS, len(records))
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix='kasa-poll') as pool:
            snapshots = list(pool.map(self._poll_one, records))
        return {record.device_id: snapshot
                for record, snapshot in zip(records, snapshots)}

    def _poll_one(self, record: devicestore.DeviceRecord) -> Dict[str, Any]:
        """Polls one device and remembers the result.

        Args:
            record: The inventory record of the device.

        Returns:
            The snapshot.
        """
        snapshot = self.bridge.snapshot(record)
        self.store_state(record, snapshot)
        return snapshot

    def store_state(self, record: devicestore.DeviceRecord,
                    snapshot: Dict[str, Any]) -> None:
        """Caches a snapshot and persists interesting changes.

        The per-device file is only rewritten when the device changes
        state or reachability, so a fast poll interval does not turn
        into constant disk writes.

        Args:
            record: The inventory record of the device.
            snapshot: The snapshot to store.
        """
        with self._states_lock:
            previous = self._states.get(record.device_id, {})
            self._states[record.device_id] = snapshot
        if snapshot.get('reachable'):
            self.devices.annotate(record.device_id, snapshot)
        changed = (previous.get('is_on') != snapshot.get('is_on')
                   or previous.get('reachable') != snapshot.get('reachable'))
        if changed:
            self.devices.record_state(record.device_id, snapshot)
            if previous and not snapshot.get('reachable'):
                self.activity.add(
                    f'{record.display_name} stopped answering',
                    level='warning', source='poller',
                    device_id=record.device_id,
                    device_name=record.display_name)

    def check_files(self, account: Any = None) -> List[Dict[str, Any]]:
        """Tests every file the daemon writes and keeps the findings.

        Run this after privileges have been dropped: the question is
        whether the account the daemon has become can write these
        files, and root could write them either way.

        Args:
            account: Ask on behalf of this account rather than this
                process, for callers that have not dropped.

        Returns:
            One report per path.
        """
        self.file_checks = filecheck.inspect(self, account)
        filecheck.log_reports(self.file_checks)
        for report in filecheck.problems(self.file_checks):
            purpose = report['purpose']
            path = report['path']
            problem = report['problem']
            self.activity.add(
                f'The {purpose} at {path} is not usable: {problem}',
                level='warning', source='daemon')
        return self.file_checks

    def state_of(self, device_id: str) -> Dict[str, Any]:
        """Returns the cached snapshot of one device.

        Args:
            device_id: Identifier of the device.

        Returns:
            The snapshot, or an empty dictionary when never polled.
        """
        with self._states_lock:
            return dict(self._states.get(device_id, {}))

    def refresh(self, device_id: str) -> Dict[str, Any]:
        """Polls one device immediately.

        Args:
            device_id: Identifier of the device.

        Returns:
            The fresh snapshot.

        Raises:
            devicestore.DeviceError: If the device is unknown.
        """
        record = self.devices.get(device_id)
        return self._poll_one(record)

    def run_action(self, device_id: str, action: str,
                   arguments: List[str], actor: str = 'dashboard') -> str:
        """Runs one action against a device and logs it.

        Args:
            device_id: Identifier of the device.
            action: Action keyword.
            arguments: Raw string arguments.
            actor: Who asked for it, used in the activity log.

        Returns:
            A short report of what happened.

        Raises:
            actions.ActionError: If the action or its arguments are
                invalid.
            devicestore.DeviceError: If the device is unknown.
            kasabridge.BridgeError: If the command fails.
        """
        record = self.devices.get(device_id)
        actions.parse(action, arguments)
        try:
            result = self.bridge.run_action(record, action, arguments)
        except kasabridge.BridgeError as err:
            self.activity.add(
                f'{action} failed: {err}', level='error', source=actor,
                device_id=device_id, device_name=record.display_name)
            raise
        self.activity.add(
            f'{action} -> {result}', source=actor, device_id=device_id,
            device_name=record.display_name)
        self._poll_one(record)
        return result

    def device_view(self, record: devicestore.DeviceRecord) -> Dict[str, Any]:
        """Builds the dashboard view of one device.

        Args:
            record: The inventory record of the device.

        Returns:
            A JSON friendly dictionary holding the record, its cached
            state and a summary of its schedule.
        """
        document = self.devices.device_document(record.device_id)
        entries, problems = schedule_lib.parse_script(
            document.get('schedule', ''))
        view = record.to_dict()
        view['display_name'] = record.display_name
        view['state'] = self.state_of(record.device_id)
        view['schedule_enabled'] = bool(document.get('schedule_enabled', True))
        view['rule_count'] = len(entries)
        view['schedule_problems'] = len(problems)
        view['next_runs'] = (schedule_lib.next_runs(entries, count=2)
                             if document.get('schedule_enabled', True)
                             else [])
        view['last_seen'] = document.get('last_seen', '')
        return view

    def dashboard(self) -> Dict[str, Any]:
        """Builds the whole dashboard payload.

        Returns:
            A JSON friendly dictionary with devices, scheduler state and
            a short summary.
        """
        views = [self.device_view(record)
                 for record in self.devices.records()]
        online = sum(1 for view in views
                     if view['state'].get('reachable'))
        switched_on = sum(1 for view in views
                          if view['state'].get('is_on'))
        power = 0.0
        for view in views:
            power += float(view['state'].get('power_watts') or 0.0)
        scheduler_state = (self.scheduler.status() if self.scheduler
                           else {'enabled': False, 'running': False})
        return {
            'devices': views,
            'summary': {
                'total': len(views),
                'online': online,
                'on': switched_on,
                'power_watts': round(power, 2),
                'started_at': self.started_at.isoformat(timespec='seconds'),
                'server_time': datetime.datetime.now().isoformat(
                    timespec='seconds'),
            },
            'scheduler': scheduler_state,
            'kasa_available': kasabridge.KASA_AVAILABLE,
            'pam_available': auth.PAM_AVAILABLE,
            'pam_error': auth.pam_reason(),
            'daemon_user': dict(self.daemon_user),
        }
