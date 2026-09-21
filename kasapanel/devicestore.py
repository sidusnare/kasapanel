# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Device inventory and per-device configuration.

Two kinds of file are managed here:

* ``devices.json`` -- the inventory, one entry per known device.
* ``devices/<device-id>.json`` -- one file per device holding its
  schedule script and its own options.

Splitting them keeps the inventory small enough to read at a glance and
lets a device be backed up, copied or edited on its own.
"""

import dataclasses
import datetime
import logging
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Optional

from kasapanel import jsonstore
from kasapanel import schedule as schedule_lib

_LOG = logging.getLogger(__name__)

INVENTORY_VERSION = 1
DEVICE_VERSION = 1



class DeviceError(Exception):
    """Raised for invalid device records or unknown device ids."""


def _now() -> str:
    """Returns the current local time as an ISO-8601 string."""
    return datetime.datetime.now().isoformat(timespec='seconds')


DEVICE_ID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def new_device_id() -> str:
    """Mints an identifier for a device.

    The identifier is random and carries nothing.  It used to be derived
    from the MAC address or a hash of the host name, which made a tidy
    stable key and also meant every file in the state directory was
    named after something the device told us.  A name on disk built from
    input is a name an attacker has a say in, and the way to be sure
    that cannot happen is not to do it: nothing a device or a person
    supplies reaches a path.

    Identity across a DHCP lease change is kept where it belongs, in the
    record: a device is matched by MAC address, then by host, and the
    identifier it was given the first time is kept.

    Returns:
        A random UUID as a string.
    """
    return str(uuid.uuid4())


def is_device_id(value: str) -> bool:
    """Says whether a string is one of our identifiers.

    Args:
        value: The string to check, from a request, a file, anywhere.

    Returns:
        True for a lower case UUID and nothing else.
    """
    return bool(DEVICE_ID_PATTERN.match(str(value)))


def _bare_mac(mac: str) -> str:
    """Reduces a MAC address to comparable form.

    Args:
        mac: MAC address, in any punctuation.

    Returns:
        Lower case hex digits, or an empty string.
    """
    return re.sub(r'[^0-9a-f]', '', str(mac).lower())


@dataclasses.dataclass
class DeviceRecord:
    """One entry of the device inventory.

    Attributes:
        device_id: Stable identifier, also the per-device file name.
        host: Host name or IP address used to reach the device.
        alias: Name shown in the dashboard.
        model: Hardware model reported by the device.
        device_type: Device kind reported by python-kasa.
        mac: MAC address, when known.
        added_at: When the device was added, ISO-8601 local time.
        source: ``scan`` or ``manual``, describing how it was added.
        enabled: False hides the device and stops its schedule.
        notes: Free text kept for the operator.
    """

    device_id: str
    host: str
    alias: str = ''
    model: str = ''
    device_type: str = ''
    mac: str = ''
    added_at: str = dataclasses.field(default_factory=_now)
    source: str = 'manual'
    enabled: bool = True
    notes: str = ''

    def to_dict(self) -> Dict[str, Any]:
        """Returns the record as a JSON friendly dictionary."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, document: Dict[str, Any]) -> 'DeviceRecord':
        """Builds a record from a decoded inventory entry.

        Args:
            document: One decoded entry of ``devices.json``.

        Returns:
            The record.

        Raises:
            DeviceError: If the entry has no usable host.
        """
        host = str(document.get('host', '')).strip()
        if not host:
            raise DeviceError('device entry has no host')
        device_id = str(document.get('device_id', '')).strip()
        if device_id and not is_device_id(device_id):
            raise DeviceError(
                f'device entry has an unusable identifier: {device_id!r}')
        mac = str(document.get('mac', '')).strip()
        return cls(
            device_id=device_id or new_device_id(),
            host=host,
            alias=str(document.get('alias', '')),
            model=str(document.get('model', '')),
            device_type=str(document.get('device_type', '')),
            mac=mac,
            added_at=str(document.get('added_at', '')) or _now(),
            source=str(document.get('source', 'manual')),
            enabled=bool(document.get('enabled', True)),
            notes=str(document.get('notes', '')))

    @property
    def display_name(self) -> str:
        """Returns the alias if set, otherwise the host."""
        return self.alias or self.host


def default_device_document(device_id: str) -> Dict[str, Any]:
    """Returns the contents of a fresh per-device file.

    Args:
        device_id: Identifier of the device.

    Returns:
        A JSON friendly document.
    """
    return {
        'version': DEVICE_VERSION,
        'device_id': device_id,
        'schedule': schedule_lib.EXAMPLE_SCRIPT,
        'schedule_enabled': True,
        'created_at': _now(),
        'updated_at': _now(),
        'last_seen': '',
        'last_state': {},
    }


class DeviceStore:
    """The device inventory and the per-device files that go with it."""

    def __init__(self, inventory_path: str, device_dir: str):
        """Initialises the store.

        Args:
            inventory_path: Path of ``devices.json``.
            device_dir: Directory holding the per-device files.
        """
        self._lock = threading.RLock()
        self._device_dir = device_dir
        self._inventory = jsonstore.JsonDocument(
            inventory_path, {'version': INVENTORY_VERSION, 'devices': []})
        self._records: Dict[str, DeviceRecord] = {}

    @property
    def device_dir(self) -> str:
        """Returns the directory holding per-device files."""
        return self._device_dir

    def load(self) -> List[DeviceRecord]:
        """Reads the inventory from disk.

        Returns:
            The device records, in file order.
        """
        with self._lock:
            document = self._inventory.load()
            records: Dict[str, DeviceRecord] = {}
            for entry in document.get('devices', []):
                try:
                    record = DeviceRecord.from_dict(entry)
                except (DeviceError, AttributeError, TypeError) as err:
                    _LOG.warning('skipping bad device entry: %s', err)
                    continue
                records[record.device_id] = record
            self._records = records
            return list(self._records.values())

    def _flush(self) -> None:
        """Writes the inventory back to disk.  Callers hold the lock."""
        self._inventory.replace({
            'version': INVENTORY_VERSION,
            'updated_at': _now(),
            'devices': [record.to_dict()
                        for record in self._records.values()],
        })

    def records(self) -> List[DeviceRecord]:
        """Returns every known device record."""
        with self._lock:
            return list(self._records.values())

    def enabled_records(self) -> List[DeviceRecord]:
        """Returns the records of devices that are not disabled."""
        return [record for record in self.records() if record.enabled]

    def get(self, device_id: str) -> DeviceRecord:
        """Looks a device up by identifier.

        Args:
            device_id: Identifier of the device.

        Returns:
            The record.

        Raises:
            DeviceError: If no such device is known.
        """
        with self._lock:
            record = self._records.get(device_id)
        if record is None:
            raise DeviceError(f'unknown device "{device_id}"')
        return record

    def find_by_host(self, host: str) -> Optional[DeviceRecord]:
        """Looks a device up by host name or address.

        Args:
            host: Host name or IP address.

        Returns:
            The record, or None when the host is not in the inventory.
        """
        wanted = host.strip().lower()
        for record in self.records():
            if record.host.strip().lower() == wanted:
                return record
        return None

    def find_by_mac(self, mac: str) -> Optional[DeviceRecord]:
        """Looks a device up by MAC address.

        This is what keeps a device's identity across a change of
        address, now that the identifier itself says nothing.

        Args:
            mac: MAC address, in any punctuation.

        Returns:
            The record, or None when it is not in the inventory.
        """
        wanted = _bare_mac(mac)
        if not wanted:
            return None
        for record in self.records():
            if _bare_mac(record.mac) == wanted:
                return record
        return None

    def add(self, host: str, alias: str = '', source: str = 'manual',
            info: Optional[Dict[str, Any]] = None) -> DeviceRecord:
        """Adds a device, or updates the matching one.

        Args:
            host: Host name or IP address of the device.
            alias: Name for the dashboard; defaults to the device alias
                reported by the hardware, then to the host.
            source: ``scan`` or ``manual``.
            info: Optional details reported by the device, using the
                keys alias, model, device_type and mac.

        Returns:
            The stored record.

        Raises:
            DeviceError: If the host is empty.
        """
        host = host.strip()
        if not host:
            raise DeviceError('a host name or IP address is required')
        info = info or {}
        mac = str(info.get('mac', ''))
        with self._lock:
            # Match on what the device is, not on what it is called: the
            # MAC first, because it survives a DHCP lease change, then
            # the host.  A match keeps the identifier it already has.
            existing = self.find_by_mac(mac) or self.find_by_host(host)
            if existing is not None:
                existing.host = host
                existing.alias = alias or existing.alias or str(
                    info.get('alias', ''))
                existing.model = str(info.get('model', '')) or existing.model
                existing.device_type = (str(info.get('device_type', ''))
                                        or existing.device_type)
                existing.mac = mac or existing.mac
                self._flush()
                self.device_document(existing.device_id)
                return existing
            record = DeviceRecord(
                device_id=new_device_id(),
                host=host,
                alias=alias or str(info.get('alias', '')),
                model=str(info.get('model', '')),
                device_type=str(info.get('device_type', '')),
                mac=mac,
                source=source)
            self._records[record.device_id] = record
            self._flush()
            self.device_document(record.device_id)
            _LOG.info('added device %s (%s)', record.display_name, record.host)
            return record

    def update(self, device_id: str,
               changes: Dict[str, Any]) -> DeviceRecord:
        """Updates the editable fields of a record.

        Args:
            device_id: Identifier of the device.
            changes: New values for alias, host, enabled or notes.

        Returns:
            The updated record.

        Raises:
            DeviceError: If the device is unknown or the host is empty.
        """
        record = self.get(device_id)
        with self._lock:
            if 'alias' in changes:
                record.alias = str(changes['alias']).strip()
            if 'notes' in changes:
                record.notes = str(changes['notes'])
            if 'enabled' in changes:
                record.enabled = bool(changes['enabled'])
            if 'host' in changes:
                host = str(changes['host']).strip()
                if not host:
                    raise DeviceError('the host cannot be empty')
                record.host = host
            self._flush()
        return record

    def annotate(self, device_id: str, info: Dict[str, Any]) -> None:
        """Fills in details learned from a live device.

        Args:
            device_id: Identifier of the device.
            info: Details using the keys alias, model, device_type, mac.
        """
        try:
            record = self.get(device_id)
        except DeviceError:
            return
        with self._lock:
            changed = False
            for field in ('model', 'device_type', 'mac'):
                value = str(info.get(field, ''))
                if value and getattr(record, field) != value:
                    setattr(record, field, value)
                    changed = True
            if not record.alias and info.get('alias'):
                record.alias = str(info['alias'])
                changed = True
            if changed:
                self._flush()

    def remove(self, device_id: str) -> DeviceRecord:
        """Removes a device and deletes its per-device file.

        Args:
            device_id: Identifier of the device.

        Returns:
            The record that was removed.

        Raises:
            DeviceError: If the device is unknown.
        """
        record = self.get(device_id)
        with self._lock:
            self._records.pop(device_id, None)
            self._flush()
        path = self.device_path(device_id)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as err:  # pragma: no cover - defensive.
            _LOG.warning('cannot delete %s: %s', path, err)
        _LOG.info('removed device %s', record.display_name)
        return record

    def device_path(self, device_id: str) -> str:
        """Returns the path of a per-device file.

        The identifier is checked rather than scrubbed.  Scrubbing
        turns bad input into some other path; refusing it means no
        string that is not one of our own identifiers can name a file
        at all.

        Args:
            device_id: Identifier of the device.

        Returns:
            An absolute path.

        Raises:
            DeviceError: If the identifier is not one of ours.
        """
        if not is_device_id(device_id):
            raise DeviceError(f'{device_id!r} is not a device identifier')
        return os.path.join(self._device_dir, f'{device_id}.json')

    def device_document(self, device_id: str) -> Dict[str, Any]:
        """Reads a per-device file, creating it when missing.

        Args:
            device_id: Identifier of the device.

        Returns:
            The decoded per-device document.
        """
        path = self.device_path(device_id)
        with self._lock:
            document = jsonstore.read_json(path, None)
            if not isinstance(document, dict):
                document = default_device_document(device_id)
                jsonstore.write_json(path, document)
            document.setdefault('device_id', device_id)
            document.setdefault('schedule', '')
            document.setdefault('schedule_enabled', True)
            return document

    def save_device_document(self, device_id: str,
                             document: Dict[str, Any]) -> Dict[str, Any]:
        """Writes a per-device file.

        Args:
            device_id: Identifier of the device.
            document: The document to store.

        Returns:
            The stored document.
        """
        document['device_id'] = device_id
        document['version'] = DEVICE_VERSION
        document['updated_at'] = _now()
        with self._lock:
            jsonstore.write_json(self.device_path(device_id), document)
        return document

    def get_schedule(self, device_id: str) -> str:
        """Returns the schedule script of a device.

        Args:
            device_id: Identifier of the device.

        Returns:
            The script text, which may be empty.
        """
        return str(self.device_document(device_id).get('schedule', ''))

    def set_schedule(self, device_id: str, script: str) -> Dict[str, Any]:
        """Validates and stores the schedule script of a device.

        Args:
            device_id: Identifier of the device.
            script: The new script text.

        Returns:
            The stored per-device document.

        Raises:
            schedule_lib.ScheduleError: If any rule is invalid.
        """
        schedule_lib.parse_strict(script)
        document = self.device_document(device_id)
        document['schedule'] = script
        return self.save_device_document(device_id, document)

    def set_schedule_enabled(self, device_id: str,
                             enabled: bool) -> Dict[str, Any]:
        """Turns the schedule of one device on or off.

        Args:
            device_id: Identifier of the device.
            enabled: Whether the schedule should run.

        Returns:
            The stored per-device document.
        """
        document = self.device_document(device_id)
        document['schedule_enabled'] = bool(enabled)
        return self.save_device_document(device_id, document)

    def record_state(self, device_id: str, state: Dict[str, Any]) -> None:
        """Stores the last known state of a device.

        Args:
            device_id: Identifier of the device.
            state: The state snapshot to remember.
        """
        document = self.device_document(device_id)
        document['last_state'] = state
        document['last_seen'] = _now()
        self.save_device_document(device_id, document)
