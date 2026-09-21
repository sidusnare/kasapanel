# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The daemon's log destination.

A long running daemon cannot assume the file it opened at start up is
still the file at that path.  Log rotation renames it, an operator
deletes it, a tidy-up script removes the whole directory.  Anything
written to the old handle after that lands in a file nobody can find, or
nowhere at all.

:class:`ReopeningFileHandler` therefore checks before every single line:
if the path is missing, or now names a different file than the one that
is open, it reopens -- creating the directory again if it has to.  There
is no rotation logic here on purpose; rotation belongs to logrotate or
to systemd, and this handler is what makes those safe to use.
"""

import errno
import logging
import os
import sys
from typing import Optional, Tuple

FILE_MODE = 0o600
DIR_MODE = 0o700

Marker = Optional[Tuple[int, int]]


class ReopeningFileHandler(logging.Handler):
    """Writes to a file, reopening it whenever it has been replaced."""

    def __init__(self, path: str, mode: int = FILE_MODE):
        """Opens the log file, creating its directory when needed.

        Args:
            path: File to append to.
            mode: Permission bits for the file when it is created.
        """
        super().__init__()
        self.path = os.path.abspath(path)
        self.mode = mode
        self._stream = None
        self._marker: Marker = None
        self._complained = False
        self._open()

    def _identify(self) -> Marker:
        """Returns the identity of the file at the configured path.

        Returns:
            The device and inode numbers, or None when nothing is there.
        """
        try:
            info = os.stat(self.path)
        except (OSError, ValueError):
            return None
        return (info.st_dev, info.st_ino)

    def _open(self) -> None:
        """Opens the log file and remembers which file it is."""
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, mode=DIR_MODE, exist_ok=True)
        existed = os.path.exists(self.path)
        # pylint: disable=consider-using-with
        self._stream = open(self.path, 'a', encoding='utf-8')
        if not existed:
            try:
                os.chmod(self.path, self.mode)
            except OSError:
                pass  # A mode we cannot set is not worth failing over.
        self._marker = self._identify()
        self._complained = False

    def _ensure(self) -> None:
        """Reopens the file if it is gone or is no longer the same one.

        This runs before every record, which is the whole point of the
        class: the window between the check and the write is one line,
        not one process lifetime.
        """
        if self._stream is None or self._stream.closed:
            self._open()
            return
        current = self._identify()
        if current is None or current != self._marker:
            try:
                self._stream.close()
            except OSError:
                pass
            self._open()

    def emit(self, record: logging.LogRecord) -> None:
        """Writes one record, reopening the file first if it must.

        Args:
            record: The record to write.
        """
        try:
            self._ensure()
            self._stream.write(self.format(record) + '\n')
            self._stream.flush()
        except (OSError, ValueError) as error:
            self._fallback(record, error)

    def _fallback(self, record: logging.LogRecord,
                  error: Exception) -> None:
        """Sends a record to stderr when the file cannot be written.

        Losing the log is not a reason to lose the daemon, so a failure
        here is reported once and the line is written to stderr instead.

        Args:
            record: The record that could not be written.
            error: Why the write failed.
        """
        if not self._complained:
            self._complained = True
            reason = error
            if isinstance(error, OSError) and error.errno:
                reason = os.strerror(error.errno)
            print(f'kasapanel: cannot write {self.path}: {reason}; '
                  'logging to stderr until it can be written again',
                  file=sys.stderr)
        try:
            print(self.format(record), file=sys.stderr)
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        """Closes the file."""
        try:
            if self._stream is not None and not self._stream.closed:
                self._stream.close()
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        finally:
            self._stream = None
            super().close()
