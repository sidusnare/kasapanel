# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""A small ring buffer of recent events.

Everything the daemon does on a device -- a schedule firing, a button
press in the dashboard, a failed connection -- lands here so the
activity tab can show it.

The buffer is the tab's backing store and stays in memory, but it is not
the only destination: every record is also handed to :mod:`logging` at
the matching level, so activity appears in the daemon log alongside
ordinary events and follows it to a file, to the terminal, or to the
journal.  Nothing is filtered on the way out; a record kept here is a
line written there.
"""

import collections
import datetime
import itertools
import logging
import threading
from typing import Any, Deque, Dict, List, Optional

_LOG = logging.getLogger(__name__)

DEFAULT_LIMIT = 500

LEVELS = ('info', 'warning', 'error')

# Activity levels are the vocabulary the dashboard speaks; these are
# their equivalents in the daemon log.
LOG_LEVELS = {
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
}


def _line(record: Dict[str, Any]) -> str:
    """Renders one record as a single log line.

    Args:
        record: The stored record.

    Returns:
        The message, prefixed with who caused it and which device it
        concerned, so a log reader sees what the activity tab shows.
    """
    where = record['device_name'] or record['device_id']
    source = record['source']
    parts = [f'[{source}]'] if source else []
    if where:
        parts.append(f'{where}:')
    parts.append(record['message'])
    return ' '.join(parts)


class ActivityLog:
    """A bounded, thread-safe list of recent events."""

    def __init__(self, limit: int = DEFAULT_LIMIT):
        """Initialises the log.

        Args:
            limit: How many records to keep before dropping the oldest.
        """
        self._lock = threading.Lock()
        self._records: Deque[Dict[str, Any]] = collections.deque(
            maxlen=max(int(limit), 10))
        self._counter = itertools.count(1)

    def add(self, message: str, level: str = 'info', source: str = 'daemon',
            device_id: str = '', device_name: str = '') -> Dict[str, Any]:
        """Appends one record.

        Args:
            message: What happened, in plain words.
            level: One of ``info``, ``warning`` or ``error``.
            source: Who caused it, such as ``schedule`` or a username.
            device_id: Device the record belongs to, when relevant.
            device_name: Device name shown in the activity tab.

        Returns:
            The stored record.
        """
        kind = level if level in LEVELS else 'info'
        record = {
            'id': next(self._counter),
            'at': datetime.datetime.now().isoformat(timespec='seconds'),
            'level': kind,
            'source': source,
            'device_id': device_id,
            'device_name': device_name,
            'message': message,
        }
        with self._lock:
            self._records.append(record)
        _LOG.log(LOG_LEVELS[kind], '%s', _line(record))
        return record

    def records(self, limit: int = 100,
                device_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns the most recent records, newest first.

        Args:
            limit: How many records to return.
            device_id: When given, only records for that device.

        Returns:
            A list of records.
        """
        with self._lock:
            found = list(self._records)
        if device_id:
            found = [item for item in found
                     if item['device_id'] == device_id]
        found.reverse()
        return found[:max(int(limit), 1)]

    def resize(self, limit: int) -> None:
        """Changes how many records are kept.

        Args:
            limit: The new limit.
        """
        with self._lock:
            self._records = collections.deque(
                self._records, maxlen=max(int(limit), 10))
