# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Per-user preferences.

Everything here belongs to one operator rather than to the panel: the
order they like their cards in, and whether they want the dark theme.
Two people signed in at once want different answers, so these are kept
per user in ``users/<username>.json`` under the state directory rather
than in ``config.json``.

The file name is the account name, which is the one place in the project
where a name from outside reaches a path.  It is allowed because the
name is not arbitrary: it arrived with a session, and a session only
exists because PAM accepted that account.  It is still checked rather
than scrubbed, the same way a device identifier is -- scrubbing turns a
bad name into some other path, while refusing it means no string that is
not an account name can name a file at all.

Preferences are a convenience, not a record.  A file that cannot be read
is replaced by the defaults and the operator loses their card order; a
field that makes no sense is dropped and the rest of the file is kept.
Nothing here is allowed to stop somebody using the panel.
"""

import datetime
import logging
import os
import re
import threading
from typing import Any, Dict, List

from kasapanel import jsonstore

_LOG = logging.getLogger(__name__)

PREFERENCES_VERSION = 1

THEMES = ('light', 'dark')
DEFAULT_THEME = 'light'

# A POSIX account name, and nothing that could be read as a path: no
# separator, no leading dot, nothing long enough to be a surprise.
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$')

# Card order holds device identifiers, which are UUIDs.  Checked here as
# well so a preferences file cannot smuggle anything else into a payload
# the panel reads back.
DEVICE_ID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

MAX_CARD_ORDER = 512


class UserError(Exception):
    """Raised for a name that cannot be used, or a value that cannot."""


def _now() -> str:
    """Returns the current local time as an ISO-8601 string."""
    return datetime.datetime.now().isoformat(timespec='seconds')


def is_username(value: str) -> bool:
    """Says whether a string is usable as an account name.

    Args:
        value: The name to check, from a session, a file, anywhere.

    Returns:
        True for a plain account name and nothing else.
    """
    return bool(USERNAME_PATTERN.match(str(value)))


def clean_theme(value: Any, current: str = DEFAULT_THEME) -> str:
    """Reduces an incoming theme name to one the stylesheet knows.

    Args:
        value: The incoming value, typically decoded from JSON.
        current: The theme in force, returned when the value is not one
            of ours.

    Returns:
        A member of :data:`THEMES`.
    """
    name = str(value or '').strip().lower()
    if name in THEMES:
        return name
    return current if current in THEMES else DEFAULT_THEME


def clean_card_order(value: Any) -> List[str]:
    """Reduces an incoming card order to a list of device identifiers.

    Identifiers that are not UUIDs are dropped, and so are repeats: a
    device named twice has no meaningful position.  Identifiers of
    devices that are no longer in the inventory are kept, because the
    panel places what it recognises and ignores the rest, and a device
    removed by mistake should come back where it was.

    Args:
        value: The incoming value, typically decoded from JSON.

    Returns:
        The identifiers, in the order given.
    """
    if not isinstance(value, (list, tuple)):
        return []
    order: List[str] = []
    for item in value[:MAX_CARD_ORDER]:
        device_id = str(item)
        if DEVICE_ID_PATTERN.match(device_id) and device_id not in order:
            order.append(device_id)
    return order


def default_preferences(username: str) -> Dict[str, Any]:
    """Returns the contents of a fresh per-user file.

    Args:
        username: The account the file belongs to.

    Returns:
        A JSON friendly document.
    """
    return {
        'version': PREFERENCES_VERSION,
        'username': username,
        'theme': DEFAULT_THEME,
        'device_card_order': [],
        'created_at': _now(),
        'updated_at': _now(),
    }


class UserStore:
    """The per-user preference files under ``users/``."""

    def __init__(self, user_dir: str):
        """Initialises the store.

        Args:
            user_dir: Directory holding the per-user files.
        """
        self._lock = threading.RLock()
        self._user_dir = user_dir

    @property
    def user_dir(self) -> str:
        """Returns the directory holding per-user files."""
        return self._user_dir

    def path_for(self, username: str) -> str:
        """Returns the path of one operator's preference file.

        Args:
            username: The account name.

        Returns:
            An absolute path; the file itself need not exist yet.

        Raises:
            UserError: If the name is not a plain account name.
        """
        if not is_username(username):
            raise UserError(f'{username!r} is not an account name')
        return os.path.join(self._user_dir, f'{username}.json')

    def preferences(self, username: str) -> Dict[str, Any]:
        """Reads one operator's preferences, filling in the defaults.

        The file is not created here.  Reading preferences happens on
        every sign in, and a file that only ever holds the defaults is
        a file written for nothing; it appears the first time the
        operator chooses something.

        Args:
            username: The account name.

        Returns:
            The decoded document, with every field present.

        Raises:
            UserError: If the name is not a plain account name.
        """
        path = self.path_for(username)
        with self._lock:
            try:
                document = jsonstore.read_json(path, None)
            except jsonstore.StoreError as err:
                # A preference file is not worth an error page.  Say so
                # in the log and carry on with the defaults.
                _LOG.warning('ignoring %s: %s', path, err)
                document = None
            if not isinstance(document, dict):
                document = default_preferences(username)
            document['version'] = PREFERENCES_VERSION
            document['username'] = username
            document['theme'] = clean_theme(document.get('theme'))
            document['device_card_order'] = clean_card_order(
                document.get('device_card_order'))
            return document

    def save(self, username: str,
             document: Dict[str, Any]) -> Dict[str, Any]:
        """Writes one operator's preference file.

        Args:
            username: The account name.
            document: The document to store.

        Returns:
            The stored document.

        Raises:
            UserError: If the name is not a plain account name.
            jsonstore.StoreError: If the file cannot be written.
        """
        path = self.path_for(username)
        document['version'] = PREFERENCES_VERSION
        document['username'] = username
        document['updated_at'] = _now()
        with self._lock:
            jsonstore.ensure_directory(self._user_dir)
            jsonstore.write_json(path, document)
        return document

    def update(self, username: str,
               changes: Dict[str, Any]) -> Dict[str, Any]:
        """Applies a partial update to one operator's preferences.

        Unknown keys are ignored, so an older panel cannot wipe a field
        a newer one wrote.

        Args:
            username: The account name.
            changes: Field names mapped to new values.

        Returns:
            The stored document.

        Raises:
            UserError: If the name is not a plain account name.
            jsonstore.StoreError: If the file cannot be written.
        """
        with self._lock:
            document = self.preferences(username)
            if 'theme' in changes:
                document['theme'] = clean_theme(
                    changes['theme'], document['theme'])
            if 'device_card_order' in changes:
                document['device_card_order'] = clean_card_order(
                    changes['device_card_order'])
            return self.save(username, document)
