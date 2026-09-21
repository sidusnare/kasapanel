# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The bridge between the threaded daemon and async python-kasa.

python-kasa is written for asyncio while the daemon is threaded, so one
dedicated event loop runs in its own thread and every other thread hands
coroutines to it through :meth:`AsyncWorker.run`.  Device objects are
created inside that loop and cached, which keeps sockets and protocol
state on the thread that owns them.

Nothing else in the daemon imports python-kasa, so the rest of the code
stays testable without any smart plugs on the network.
"""

import asyncio
import concurrent.futures
import datetime
import importlib.metadata
import logging
import threading
from typing import Any, Awaitable, Dict, List, Optional

from kasapanel import actions
from kasapanel import devicestore

_LOG = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the installed python-kasa version.
    from kasa import Credentials
    from kasa import Discover
    from kasa import KasaException
    KASA_AVAILABLE = True
except ImportError:  # pragma: no cover
    Credentials = None
    Discover = None

    class KasaException(Exception):
        """Stand-in used when python-kasa is not installed."""

    KASA_AVAILABLE = False


def _kasa_version() -> str:
    """Finds the installed python-kasa version.

    Returns:
        The version string, or an empty string when it is not installed.
    """
    if not KASA_AVAILABLE:
        return ''
    try:
        return importlib.metadata.version('python-kasa')
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        # A source checkout on the path has no distribution metadata.
        import kasa  # pylint: disable=import-outside-toplevel
        return str(getattr(kasa, '__version__', ''))


KASA_VERSION = _kasa_version()

# Errors a device call may raise that are not programming mistakes.
DEVICE_ERRORS = (KasaException, OSError, asyncio.TimeoutError,
                 concurrent.futures.TimeoutError, actions.ActionError)


class BridgeError(Exception):
    """Raised when a device cannot be reached or a command fails."""


class AsyncWorker:
    """An asyncio event loop running in its own thread."""

    def __init__(self, name: str = 'kasa-loop'):
        """Initialises the worker.

        Args:
            name: Thread name, which shows up in logs and in ps.
        """
        self._name = name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Returns the event loop, or None before it is started."""
        return self._loop

    def start(self) -> None:
        """Starts the event loop thread and waits until it is running."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._serve, name=self._name, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _serve(self) -> None:
        """Runs the event loop until it is stopped."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the event loop thread.

        Args:
            timeout: Seconds to wait for the thread to finish.
        """
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=timeout)
        self._loop = None
        self._thread = None
        self._ready.clear()

    def run(self, coroutine: Awaitable[Any], timeout: float = 20.0) -> Any:
        """Runs a coroutine on the loop and waits for its result.

        Args:
            coroutine: The coroutine to run.
            timeout: Seconds to wait before giving up.

        Returns:
            Whatever the coroutine returns.

        Raises:
            BridgeError: If the loop is not running or the deadline
                passes.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            raise BridgeError('the device event loop is not running')
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as err:
            future.cancel()
            raise BridgeError(
                f'the device did not answer within {timeout:g} seconds'
            ) from err


def _feature(device: Any, name: str) -> Any:
    """Reads an optional python-kasa module from a device.

    Args:
        device: A python-kasa device object.
        name: Attribute name on ``kasa.Module``.

    Returns:
        The module, or None when the device does not have it.
    """
    return actions.device_module(device, name)


def _light_state(device: Any) -> Dict[str, Any]:
    """Collects the light related part of a device snapshot.

    Args:
        device: A python-kasa device object.

    Returns:
        A dictionary of light values, empty for devices with no light.
    """
    light = _feature(device, 'Light')
    if light is None:
        return {}
    state: Dict[str, Any] = {}
    for source, key in (('brightness', 'brightness'),
                        ('color_temp', 'colour_temperature'),
                        ('hsv', 'hsv')):
        value = getattr(light, source, None)
        if value is not None:
            state[key] = list(value) if isinstance(value, tuple) else value
    state['is_dimmable'] = bool(getattr(light, 'is_dimmable', False))
    state['is_colour'] = bool(getattr(light, 'is_color', False))
    state['is_variable_white'] = bool(
        getattr(light, 'is_variable_color_temp', False))
    return state


def _energy_state(device: Any) -> Dict[str, Any]:
    """Collects the energy monitoring part of a device snapshot.

    Args:
        device: A python-kasa device object.

    Returns:
        A dictionary of energy values, empty when unsupported.
    """
    energy = _feature(device, 'Energy')
    if energy is None:
        return {}
    state = {}
    watts = getattr(energy, 'current_consumption', None)
    if watts is not None:
        state['power_watts'] = round(float(watts), 2)
    today = getattr(energy, 'consumption_today', None)
    if today is not None:
        state['energy_today_kwh'] = round(float(today), 3)
    total = getattr(energy, 'consumption_total', None)
    if total is not None:
        state['energy_total_kwh'] = round(float(total), 3)
    return state


# python-kasa raises one exception type for "I do not know how to talk
# to this" and another for "you did not say who you are", and the raw
# text of either tells an operator nothing about what to do next.
UNSUPPORTED_HINT = (
    'This device speaks a protocol the installed python-kasa does not '
    'implement. It is not a fault in the panel and no setting here will '
    'reach it. Newer TP-Link firmware has begun using an encryption '
    'scheme called TPAP, support for which is still an unmerged pull '
    'request upstream (python-kasa/python-kasa#1592); until it is '
    'released the device cannot be controlled by python-kasa at all. '
    'Check for a newer python-kasa first, since this note may have '
    'aged.')

CREDENTIALS_HINT = (
    'This device wants TP-Link account credentials. Put the e-mail '
    'address and password of the account the device is bound to into '
    'device_username and device_password on the settings page.')


def explain(where: str, err: Exception) -> str:
    """Turns a python-kasa exception into something worth reading.

    Args:
        where: Host or display name to name in the message.
        err: The exception raised.

    Returns:
        A message that says what happened and what to do about it.
    """
    text = str(err).strip() or err.__class__.__name__
    name = err.__class__.__name__
    if name == 'UnsupportedDeviceError' or 'Unsupported device' in text:
        return f'{where}: {text}. {UNSUPPORTED_HINT}'
    if name == 'AuthenticationError' or 'authentication' in text.lower():
        return f'{where}: {text}. {CREDENTIALS_HINT}'
    return f'{where}: {text}'


def describe_device(device: Any) -> Dict[str, Any]:
    """Builds a JSON friendly snapshot of a connected device.

    Args:
        device: A python-kasa device object that has been updated.

    Returns:
        A dictionary describing the device and its current state.
    """
    device_type = getattr(device, 'device_type', '')
    on_since = getattr(device, 'on_since', None)
    snapshot: Dict[str, Any] = {
        'reachable': True,
        'alias': getattr(device, 'alias', '') or '',
        'model': getattr(device, 'model', '') or '',
        'device_type': getattr(device_type, 'value', str(device_type)),
        'mac': (getattr(device, 'mac', '') or '').lower(),
        'host': getattr(device, 'host', ''),
        'is_on': bool(getattr(device, 'is_on', False)),
        'rssi': getattr(device, 'rssi', None),
        'on_since': on_since.isoformat(timespec='seconds')
                    if isinstance(on_since, datetime.datetime) else '',
        'has_led': _feature(device, 'Led') is not None,
        'polled_at': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    snapshot.update(_light_state(device))
    snapshot.update(_energy_state(device))
    return snapshot


class DeviceBridge:
    """Connects to Kasa devices and runs commands against them."""

    def __init__(self, worker: AsyncWorker, settings: Any):
        """Initialises the bridge.

        Args:
            worker: The event loop thread used for every device call.
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        self._worker = worker
        self._settings = settings
        self._lock = threading.RLock()
        self._devices: Dict[str, Any] = {}

    def update_settings(self, settings: Any) -> None:
        """Replaces the settings used for new connections.

        Args:
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        with self._lock:
            self._settings = settings
            self._devices.clear()

    @property
    def timeout(self) -> float:
        """Returns the per-command deadline in seconds."""
        return float(getattr(self._settings, 'command_timeout_seconds', 20))

    def _credentials(self) -> Any:
        """Returns cloud credentials, or None when none are configured."""
        username = getattr(self._settings, 'device_username', '')
        password = getattr(self._settings, 'device_password', '')
        if username and password and Credentials is not None:
            return Credentials(username, password)
        return None

    async def _connect(self, host: str) -> Any:
        """Connects to a device, reusing a cached connection.

        Args:
            host: Host name or IP address of the device.

        Returns:
            A connected python-kasa device object.

        Raises:
            BridgeError: If python-kasa is missing or the device does
                not answer.
        """
        if not KASA_AVAILABLE:
            raise BridgeError(
                'python-kasa is not installed; run "pip install python-kasa"')
        device = self._devices.get(host)
        if device is None:
            device = await Discover.discover_single(
                host,
                credentials=self._credentials(),
                timeout=int(self.timeout))
            self._devices[host] = device
        await device.update()
        return device

    async def _disconnect(self, host: str) -> None:
        """Closes and forgets a cached connection.

        Args:
            host: Host name or IP address of the device.
        """
        device = self._devices.pop(host, None)
        closer = getattr(device, 'disconnect', None)
        if closer is not None:
            try:
                await closer()
            except DEVICE_ERRORS as err:  # pragma: no cover - defensive.
                _LOG.debug('closing %s failed: %s', host, err)

    def forget(self, host: str) -> None:
        """Drops any cached connection to a host.

        Args:
            host: Host name or IP address of the device.
        """
        with self._lock:
            if host in self._devices:
                try:
                    self._worker.run(self._disconnect(host), timeout=5)
                except BridgeError:  # pragma: no cover - defensive.
                    self._devices.pop(host, None)

    def probe(self, host: str) -> Dict[str, Any]:
        """Contacts a device once and describes it.

        Args:
            host: Host name or IP address of the device.

        Returns:
            A snapshot dictionary.

        Raises:
            BridgeError: If the device cannot be reached.
        """
        async def _probe() -> Dict[str, Any]:
            device = await self._connect(host)
            return describe_device(device)

        try:
            return self._worker.run(_probe(), timeout=self.timeout + 5)
        except DEVICE_ERRORS as err:
            raise BridgeError(explain(host, err)) from err

    def snapshot(self, record: devicestore.DeviceRecord) -> Dict[str, Any]:
        """Polls one device and returns its state.

        Unreachable devices produce a snapshot with ``reachable`` set to
        False and an ``error`` message rather than an exception, because
        the dashboard shows every device whether it answers or not.

        Args:
            record: The inventory record of the device.

        Returns:
            A snapshot dictionary.
        """
        try:
            state = self.probe(record.host)
        except BridgeError as err:
            return {
                'reachable': False,
                'error': explain(record.host, err),
                'is_on': False,
                'host': record.host,
                'polled_at': datetime.datetime.now().isoformat(
                    timespec='seconds'),
            }
        return state

    def run_action(self, record: devicestore.DeviceRecord, action: str,
                   arguments: List[str]) -> str:
        """Runs one action against a device.

        Args:
            record: The inventory record of the device.
            action: Action keyword, for example ``brightness``.
            arguments: Raw string arguments for the action.

        Returns:
            A short report of what happened.

        Raises:
            BridgeError: If the device cannot be reached or refuses the
                command.
        """
        async def _run() -> str:
            device = await self._connect(record.host)
            return await actions.execute(device, action, arguments)

        try:
            return self._worker.run(_run(), timeout=self.timeout + 5)
        except DEVICE_ERRORS as err:
            self.forget(record.host)
            raise BridgeError(explain(record.display_name, err)) from err

    def discover(self, seconds: Optional[int] = None) -> List[Dict[str, Any]]:
        """Scans the local network for Kasa devices.

        Args:
            seconds: How long to listen for replies; defaults to the
                configured discovery time.

        Returns:
            One snapshot dictionary per device found.

        Raises:
            BridgeError: If python-kasa is missing or the scan fails.
        """
        if not KASA_AVAILABLE:
            raise BridgeError(
                'python-kasa is not installed; run "pip install python-kasa"')
        listen = int(seconds or getattr(
            self._settings, 'discovery_seconds', 8))
        target = getattr(self._settings, 'discovery_target',
                         '255.255.255.255')

        async def _scan() -> List[Dict[str, Any]]:
            found = await Discover.discover(
                target=target,
                credentials=self._credentials(),
                discovery_timeout=listen,
                timeout=listen)
            results = []
            for host, device in found.items():
                try:
                    await device.update()
                    snapshot = describe_device(device)
                except DEVICE_ERRORS as err:
                    _LOG.info('discovered %s but could not read it: %s',
                              host, err)
                    snapshot = {'reachable': False,
                                'error': explain(host, err)}
                snapshot['host'] = host
                results.append(snapshot)
            return results

        try:
            return self._worker.run(_scan(), timeout=listen + 15)
        except DEVICE_ERRORS as err:
            raise BridgeError(f'network scan failed: {err}') from err
