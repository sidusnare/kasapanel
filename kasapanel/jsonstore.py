# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Atomic, thread-safe JSON persistence helpers.

Every piece of persistent state in Kasa Panel lives in its own JSON
file.  Writes go to a temporary file in the same directory and are then
moved into place with :func:`os.replace`, so a crash mid-write can never
leave a truncated file behind.
"""

import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict, Optional

_LOG = logging.getLogger(__name__)

FILE_MODE = 0o600
DIR_MODE = 0o700


class StoreError(Exception):
    """Raised when a JSON document cannot be read or written."""


def ensure_directory(path: str) -> None:
    """Creates a directory (and parents) with restrictive permissions.

    Args:
        path: Directory to create.  Existing directories are left alone.

    Raises:
        StoreError: If the directory cannot be created.
    """
    try:
        os.makedirs(path, mode=DIR_MODE, exist_ok=True)
    except OSError as err:
        raise StoreError(f'cannot create directory {path}: {err}') from err


def read_json(path: str, default: Optional[Any] = None) -> Any:
    """Reads a JSON document from disk.

    Args:
        path: File to read.
        default: Value returned when the file does not exist.

    Returns:
        The decoded document, or ``default`` when the file is missing.

    Raises:
        StoreError: If the file exists but cannot be read or decoded.
    """
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as err:
        raise StoreError(f'cannot read {path}: {err}') from err


def write_json(path: str, document: Any) -> None:
    """Writes a JSON document atomically.

    Args:
        path: Destination file.
        document: Any JSON-serialisable object.

    Raises:
        StoreError: If the document cannot be serialised or stored.
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    ensure_directory(directory)
    handle = None
    tmp_path = None
    try:
        fileno, tmp_path = tempfile.mkstemp(
                prefix='.tmp-', dir=directory, text=True)
        handle = os.fdopen(fileno, 'w', encoding='utf-8')
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.chmod(tmp_path, FILE_MODE)
        os.replace(tmp_path, path)
        tmp_path = None
    except (OSError, TypeError, ValueError) as err:
        raise StoreError(f'cannot write {path}: {err}') from err
    finally:
        if handle is not None:
            handle.close()
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)


class JsonDocument:
    """A single JSON file guarded by a reentrant lock.

    The document is cached in memory; :meth:`load` refreshes the cache
    from disk and :meth:`save` flushes it back atomically.
    """

    def __init__(self, path: str, default: Optional[Dict[str, Any]] = None):
        """Initialises the document.

        Args:
            path: File backing this document.
            default: Document used when the file does not exist yet.
        """
        self._path = path
        self._default = default if default is not None else {}
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = dict(self._default)
        self._loaded = False

    @property
    def path(self) -> str:
        """Returns the path of the backing file."""
        return self._path

    @property
    def lock(self) -> threading.RLock:
        """Returns the lock guarding this document."""
        return self._lock

    def load(self) -> Dict[str, Any]:
        """Reads the document from disk into the cache.

        Returns:
            The cached document.
        """
        with self._lock:
            data = read_json(self._path, None)
            if not isinstance(data, dict):
                if data is not None:
                    _LOG.warning(
                        '%s is not a JSON object; using defaults', self._path)
                data = dict(self._default)
            self._data = data
            self._loaded = True
            return self._data

    def data(self) -> Dict[str, Any]:
        """Returns the cached document, loading it on first use."""
        with self._lock:
            if not self._loaded:
                self.load()
            return self._data

    def replace(self, document: Dict[str, Any]) -> None:
        """Replaces the cached document and writes it out.

        Args:
            document: New document contents.
        """
        with self._lock:
            self._data = document
            self._loaded = True
            self.save()

    def save(self) -> None:
        """Writes the cached document to disk."""
        with self._lock:
            write_json(self._path, self._data)
