# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Checks that the files the daemon writes can actually be written.

A daemon that cannot write its configuration is not broken in any way
it can notice: it starts, it serves, it controls devices, and then the
first person to save a setting gets a permission error. The same is
true of the log file it recreates after rotation and the pid file it
removes on the way out. Every one of those failures happens hours after
the cause, to somebody who was not there when the daemon started.

So the daemon tries them all at start up and says what it finds.

The timing matters. These checks run **after** privileges have been
dropped, because the question is not whether root can write the file --
root can write anything -- but whether the account the daemon has become
can. Running them before the drop would produce a clean report and a
broken daemon.

Two things are tested for each file: the file itself when it exists, and
the directory holding it. The directory is not an afterthought. Writing
JSON atomically means creating a temporary file beside the target and
renaming it over the top, so a writable file in a read-only directory
cannot be saved either.

Each result records whether the path was written down in the
configuration or is a built-in default, because the two need different
answers: a path somebody chose is usually a permission to fix, while a
default that does not work usually means the daemon is running somewhere
it was not designed for.
"""

import logging
import os
from typing import Any, Dict, List

from kasapanel import privsep

_LOG = logging.getLogger(__name__)

# What a path is for, and what stops working when it is not writable.
CONSEQUENCES = {
    'configuration': 'settings saved from the panel will fail',
    'inventory': 'devices cannot be added, renamed or removed',
    'schedules': 'schedules cannot be saved',
    'state': 'nothing can be stored between restarts',
    'log file': 'the log cannot be written or recreated after rotation',
    'pid file': 'the pid file will be left behind at shutdown',
    'TLS certificate': 'a renewed certificate cannot be loaded',
    'TLS key': 'a renewed certificate cannot be loaded',
}


def _access(path: str, mode: int, account: Any = None) -> bool:
    """Tests access, as the daemon account rather than as this process.

    After the drop the two are the same and this is a plain
    ``os.access``.  Run from ``kasapanel check`` as root they are not,
    so the question is asked of the account that will actually be doing
    the work.  Either way the kernel answers it, which means ACLs count:
    a key distributed by Ansible with ``setfacl -m u:www-data:r`` reads
    0600 root:root and is readable all the same.

    Args:
        path: File or directory to test.
        mode: An ``os.access`` mode.
        account: The account to ask about, or None for this process.

    Returns:
        True when the access is permitted.
    """
    if account is not None:
        return privsep.access_as(account, path, mode)
    effective = os.access in os.supports_effective_ids
    try:
        return os.access(path, mode, effective_ids=effective)
    except (OSError, ValueError, NotImplementedError):  # pragma: no cover
        return False


def _examine(path: str, purpose: str, source: str,
             writing: bool = True, account: Any = None) -> Dict[str, Any]:
    """Works out whether one path can be used as intended.

    Args:
        path: The file in question.
        purpose: Short name for what it is for.
        source: ``configured`` or ``default``.
        writing: True to test for writing, False to test for reading.
        account: The account to ask about, or None for this process.

    Returns:
        A JSON friendly report.
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    report: Dict[str, Any] = {
        'purpose': purpose,
        'path': path,
        'directory': directory,
        'source': source,
        'access': 'write' if writing else 'read',
        'exists': os.path.exists(path),
        'ok': True,
        'problem': '',
        'consequence': CONSEQUENCES.get(purpose, ''),
    }
    if not writing:
        if not report['exists']:
            report['ok'] = False
            report['problem'] = 'the file is not there'
        elif not _access(path, os.R_OK, account):
            report['ok'] = False
            report['problem'] = 'the file cannot be read'
        return report

    if not os.path.isdir(directory):
        report['ok'] = False
        report['problem'] = 'the directory does not exist'
        return report
    if not _access(directory, os.W_OK | os.X_OK, account):
        report['ok'] = False
        # Say why this matters, because a writable file in a read-only
        # directory looks fine until the first save.
        report['problem'] = (
            'the directory cannot be written, so the temporary file an '
            'atomic write needs cannot be created there')
        return report
    if report['exists'] and not _access(path, os.W_OK, account):
        report['ok'] = False
        report['problem'] = 'the file itself cannot be written'
    return report


def inspect(app: Any, account: Any = None) -> List[Dict[str, Any]]:
    """Checks every file the daemon writes, and the TLS pair it reads.

    Args:
        app: The application.
        account: Ask on behalf of this account rather than this
            process; used by ``check``, which runs as whoever invoked
            it rather than as the daemon.

    Returns:
        One report per path, in the order they matter.
    """
    settings = app.settings
    store = app.config_store

    def source(name: str) -> str:
        """Says where a setting's value came from.

        Args:
            name: Setting name.

        Returns:
            ``configured`` or ``default``.
        """
        return 'configured' if store.is_explicit(name) else 'default'

    reports = [
        _examine(store.path, 'configuration',
                 'configured' if app.config_path else 'default',
                 account=account),
        _examine(settings.devices_file, 'inventory', source('state_dir'),
                 account=account),
        _examine(os.path.join(settings.device_dir, 'a-device.json'),
                 'schedules', source('state_dir'), account=account),
        _examine(settings.resolved_certificate(), 'TLS certificate',
                 source('tls_certificate'), writing=False, account=account),
        _examine(settings.resolved_key(), 'TLS key',
                 source('tls_key'), writing=False, account=account),
    ]
    if settings.log_file:
        reports.append(_examine(settings.log_file, 'log file',
                                source('log_file'), account=account))
    pidfile = getattr(app, 'pidfile', '')
    if pidfile:
        reports.append(_examine(pidfile, 'pid file', 'configured',
                                account=account))
    return reports


def problems(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filters a report list down to the failures.

    Args:
        reports: Reports from :func:`inspect`.

    Returns:
        Only the entries that are not usable.
    """
    return [report for report in reports if not report['ok']]


def log_reports(reports: List[Dict[str, Any]]) -> None:
    """Writes the findings to the daemon log.

    Args:
        reports: Reports from :func:`inspect`.
    """
    broken = problems(reports)
    if not broken:
        _LOG.info('file check: all %d paths are usable', len(reports))
        return
    for report in broken:
        consequence = report['consequence']
        _LOG.warning(
            'file check: the %s at %s (%s) is not usable: %s%s',
            report['purpose'], report['path'], report['source'],
            report['problem'],
            f'; {consequence}' if consequence else '')
