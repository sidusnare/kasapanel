# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Privilege separation for the parts that need to stay privileged.

PAM has to read the shadow file, which means root.  Nothing else here
does: serving HTTPS, talking to plugs on the LAN and writing JSON need
no privilege at all.  Running the whole daemon as root so that one
function can read one file is the arrangement this module exists to
avoid.

Threads cannot do it.  Credentials on Linux are per task in the kernel,
but glibc deliberately broadcasts ``setuid`` to every thread in the
process, so a process cannot be part root and part not.  Even if that
were bypassed with raw syscalls, a root thread sharing an address space
with the HTTP threads would give almost nothing: anyone who can run code
in the process can reach that thread's memory.  Isolation needs an
address space of its own.

So the daemon forks a helper before it drops anything.  The helper keeps
root, speaks a two-message protocol over a socket pair, and does exactly
one thing: check a username and password against PAM.  The daemon drops
to an unprivileged account and asks the helper whenever somebody signs
in.  Compromising the web server yields a socket on which the only
possible sentence is "is this password correct", answered yes or no.

The fork happens before any thread starts, which is the only safe time
to fork, and before the listening socket is created, so the helper never
holds one.
"""

import ctypes
import errno
import grp
import json
import logging
import os
import pwd
import signal
import socket
import struct
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

_LOG = logging.getLogger(__name__)

# Tried in order when no daemon user is configured.
FALLBACK_USERS = ('www-data', 'httpd')

# The last resort, which the panel complains about until it is changed.
LAST_RESORT_USER = 'nobody'

# How long the helper will spend on one PAM conversation, and how long
# the daemon waits for its answer.
HELPER_TIMEOUT_SECONDS = 30
CLIENT_TIMEOUT_SECONDS = HELPER_TIMEOUT_SECONDS + 5

# A password is the largest thing on the wire and is not large.
MAX_MESSAGE_BYTES = 64 * 1024

# The capabilities a privileged start actually needs, and what each one
# is for.  A uid 0 process that lacks these fails with EPERM, which on
# its own looks like a file permission problem and is not one.
NEEDED_CAPABILITIES = {
    'CAP_SETUID': (7, 'become the daemon account'),
    'CAP_SETGID': (6, 'set the daemon group'),
    'CAP_CHOWN': (0, 'hand its files to that account'),
    'CAP_FOWNER': (3, 'hand over files it does not own'),
}

_LENGTH = struct.Struct('!I')


class PrivilegeError(Exception):
    """Raised when privileges cannot be arranged as configured."""


class HelperError(Exception):
    """Raised when the authentication helper cannot answer."""


def effective_capabilities() -> Optional[int]:
    """Reads the effective capability set of this process.

    Returns:
        The capability bitmask, or None when it cannot be read, which
        is the case on kernels without ``/proc``.
    """
    try:
        with open('/proc/self/status', encoding='utf-8') as handle:
            for line in handle:
                if line.startswith('CapEff:'):
                    return int(line.split()[1], 16)
    except (OSError, ValueError, IndexError):  # pragma: no cover
        return None
    return None  # pragma: no cover


# The kernel's capability interface, used directly because the standard
# library has no binding for it and the alternative is a dependency.
_CAP_VERSION_3 = 0x20080522


class _CapHeader(ctypes.Structure):
    """The header struct capget and capset take."""

    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class _CapData(ctypes.Structure):
    """One 32 bit slice of the three capability sets."""

    _fields_ = [('effective', ctypes.c_uint32),
                ('permitted', ctypes.c_uint32),
                ('inheritable', ctypes.c_uint32)]


def raise_effective(names: List[str]) -> List[str]:
    """Moves capabilities the process holds into its effective set.

    A capability in the permitted set but not the effective set cannot
    be used, but the process is entitled to raise it whenever it likes;
    that is what the permitted set means.  Service managers and exec
    paths can leave capabilities sitting in permitted, so rather than
    fail on a capability we are allowed to have, take it.

    Args:
        names: Capability names to raise.

    Returns:
        The names that were raised, empty when nothing needed raising
        or the raise failed.
    """
    wanted = [NEEDED_CAPABILITIES[name][0] for name in names
              if name in NEEDED_CAPABILITIES]
    if not wanted:
        return []
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        header = _CapHeader(_CAP_VERSION_3, 0)
        data = (_CapData * 2)()
        if libc.capget(ctypes.byref(header), ctypes.byref(data)) != 0:
            return []
        raised = []
        for name in names:
            bit = NEEDED_CAPABILITIES[name][0]
            slot, offset = divmod(bit, 32)
            if not data[slot].permitted & (1 << offset):
                continue  # Not ours to take.
            data[slot].effective |= (1 << offset)
            raised.append(name)
        if not raised:
            return []
        if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
            return []
        return raised
    except (OSError, AttributeError, ValueError):  # pragma: no cover
        return []


def clear_capabilities() -> bool:
    """Empties every capability set of this process.

    Called after the drop.  Changing uid away from root normally clears
    the capability sets by itself, but a process that was given ambient
    capabilities can keep them, and the whole point of dropping is to
    hold nothing afterwards.  Clearing explicitly makes that true
    whatever the process was started with.

    Returns:
        True when the sets are empty afterwards.
    """
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        header = _CapHeader(_CAP_VERSION_3, 0)
        data = (_CapData * 2)()
        for slot in range(2):
            data[slot].effective = 0
            data[slot].permitted = 0
            data[slot].inheritable = 0
        libc.capset(ctypes.byref(header), ctypes.byref(data))
    except (OSError, AttributeError, ValueError):  # pragma: no cover
        pass
    held = effective_capabilities()
    return held == 0 or held is None


def privilege_report() -> Dict[str, Any]:
    """Collects everything that decides whether a drop can work.

    Four things can stop a uid 0 process becoming somebody else, and
    they need different fixes, so all four are reported together:
    the capabilities it holds, whether a seccomp filter is in force,
    whether it is confined to a user namespace where the target account
    does not exist, and whether new privileges are barred.

    Returns:
        A mapping of what the kernel says about this process.
    """
    report: Dict[str, Any] = {
        'uid': os.getuid(),
        'cap_effective': '', 'cap_permitted': '', 'cap_bounding': '',
        'no_new_privs': '', 'seccomp': '', 'user_namespace': False,
        'uid_map': '',
    }
    fields = {
        'CapEff:': 'cap_effective',
        'CapPrm:': 'cap_permitted',
        'CapBnd:': 'cap_bounding',
        'NoNewPrivs:': 'no_new_privs',
        'Seccomp:': 'seccomp',
    }
    try:
        with open('/proc/self/status', encoding='utf-8') as handle:
            for line in handle:
                for prefix, name in fields.items():
                    if line.startswith(prefix):
                        report[name] = line.split()[1]
    except OSError:  # pragma: no cover
        return report
    try:
        with open('/proc/self/uid_map', encoding='utf-8') as handle:
            mapping = handle.read().strip()
        report['uid_map'] = mapping
        # The identity map is what an unconfined process has; anything
        # else means a user namespace, where most uids do not exist.
        report['user_namespace'] = (
            mapping.split() != ['0', '0', '4294967295'])
    except OSError:  # pragma: no cover
        pass
    return report


def describe_privileges() -> str:
    """Renders the privilege report as one log line.

    Returns:
        A short summary suitable for the journal.
    """
    report = privilege_report()
    uid = report['uid']
    effective = report['cap_effective']
    bounding = report['cap_bounding']
    barred = report['no_new_privs']
    filtered = report['seccomp']
    namespaced = 'yes' if report['user_namespace'] else 'no'
    return (f'uid={uid} CapEff={effective} CapBnd={bounding} '
            f'NoNewPrivs={barred} Seccomp={filtered} '
            f'userns={namespaced}')


def missing_capabilities() -> List[str]:
    """Lists the capabilities a privileged start needs but lacks.

    Returns:
        Capability names, empty when everything needed is held or when
        the set could not be read.
    """
    held = effective_capabilities()
    if held is None:
        return []
    return sorted(name for name, (bit, _) in NEEDED_CAPABILITIES.items()
                  if not held & (1 << bit))


def capability_advice(missing: List[str]) -> str:
    """Explains a missing capability in terms of the unit file.

    Running as uid 0 is not the same as being able to act as root: a
    service manager can start a process as root and still remove the
    capabilities that make root mean anything.  When that happens every
    privileged call fails with EPERM, which reads like a file
    permission problem and is not one, so the message says where to
    look.

    Args:
        missing: Capability names that are not held.

    Returns:
        A sentence naming the cause and the fix.
    """
    if not missing:
        return ''
    wanted = ' '.join(sorted(NEEDED_CAPABILITIES))
    reasons = ', '.join(
        f'{name} to {NEEDED_CAPABILITIES[name][1]}' for name in missing)
    absent = ', '.join(missing)
    return (
        f'this process is uid 0 but does not hold {absent}, '
        f'so it cannot {reasons}. Under systemd this is almost always '
        f'CapabilityBoundingSet in the unit file: it must include '
        f'{wanted}. Check with: systemctl show kasapanel '
        '-p CapabilityBoundingSet')


def capability_report() -> str:
    """Reports the capability sets in a form that fits one log line.

    When a drop fails, this is the evidence that decides between the two
    explanations -- a capability the process was never given, or
    something else refusing -- without a second round trip.

    Returns:
        The raw sets and the state of each capability the daemon needs.
    """
    sets = {}
    try:
        with open('/proc/self/status', encoding='utf-8') as handle:
            for line in handle:
                for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd',
                             'CapAmb'):
                    if line.startswith(name + ':'):
                        sets[name] = line.split()[1]
    except OSError:  # pragma: no cover
        return 'the capability sets could not be read'
    held = int(sets.get('CapEff', '0'), 16)
    marks = []
    for name, (bit, _) in sorted(NEEDED_CAPABILITIES.items()):
        marks.append(f'{name}=' + ('yes' if held & (1 << bit) else 'NO'))
    state = ' '.join(marks)
    raw = ' '.join(f'{name}={value}' for name, value in sorted(sets.items()))
    return f'{state}; {raw}'


def check_can_drop() -> None:
    """Makes sure privileges can be dropped before anything is changed.

    Called before any file changes hands.  Discovering afterwards that
    the drop is impossible would leave the files belonging to an
    account the daemon never became.

    Raises:
        PrivilegeError: If a privileged start cannot complete.
    """
    if os.getuid() != 0:
        return
    _LOG.info('privileged start: %s', describe_privileges())
    missing = missing_capabilities()
    if missing:
        # They may be sitting in the permitted set, which the process is
        # entitled to draw on.  Take them before complaining.
        raised = raise_effective(missing)
        if raised:
            _LOG.info('raised %s from the permitted set into the '
                      'effective set', ', '.join(raised))
            missing = missing_capabilities()
    if missing:
        raise PrivilegeError(
            f'{capability_advice(missing)} [{describe_privileges()}] '
            f'[{capability_report()}]')


def resolve_user(configured: str) -> Tuple[pwd.struct_passwd, bool]:
    """Chooses the account the daemon should run as.

    A configured name must exist; falling back from it silently would
    put the daemon somewhere the operator did not ask for.  With nothing
    configured, the usual unprivileged service accounts are tried in
    turn, and ``nobody`` last.

    Args:
        configured: The account named in the configuration, if any.

    Returns:
        The password database entry, and whether it is the last resort.

    Raises:
        PrivilegeError: If the configured account does not exist, or if
            nothing suitable could be found at all.
    """
    if configured:
        try:
            record = pwd.getpwnam(configured)
        except KeyError as error:
            raise PrivilegeError(
                f'the configured daemon user "{configured}" does not exist '
                'on this system; create it or change daemon_user'
            ) from error
        _refuse_privileged(record)
        # Naming the last resort explicitly is still the last resort.
        return record, record.pw_name == LAST_RESORT_USER
    for name in FALLBACK_USERS:
        try:
            record = pwd.getpwnam(name)
        except KeyError:
            continue
        _LOG.info('no daemon user configured; using "%s"', name)
        return record, False
    try:
        record = pwd.getpwnam(LAST_RESORT_USER)
    except KeyError as error:
        tried = ', '.join(FALLBACK_USERS)
        raise PrivilegeError(
            f'no daemon user is configured, none of {tried} exist, and '
            f'there is no "{LAST_RESORT_USER}" account to fall back to; '
            'set daemon_user in the configuration to an unprivileged '
            'account'
        ) from error
    _LOG.warning('falling back to "%s"; set daemon_user to a dedicated '
                 'account instead', LAST_RESORT_USER)
    return record, True


def _refuse_privileged(record: pwd.struct_passwd) -> None:
    """Rejects an account that would leave the daemon privileged.

    Running as root is the arrangement all of this exists to avoid, and
    accepting it here would be worse than useless: the daemon would hand
    its files to uid 0, find it could still return to root, and stop --
    having changed the ownership of everything on the way.  Better to
    refuse before touching anything.

    Args:
        record: The account that was asked for.

    Raises:
        PrivilegeError: If the account is privileged.
    """
    if record.pw_uid == 0 or record.pw_gid == 0:
        raise PrivilegeError(
            f'daemon_user is set to "{record.pw_name}", which is uid '
            f'{record.pw_uid} and gid {record.pw_gid}; the daemon has to '
            'drop to an unprivileged account, or the privileged helper '
            'protects nothing. Create a dedicated account and name that '
            'instead.')


def describe_user(record: pwd.struct_passwd, last_resort: bool) -> dict:
    """Summarises the daemon account for the settings tab.

    Args:
        record: The password database entry in use.
        last_resort: Whether it is the last-resort account.

    Returns:
        A JSON friendly mapping.
    """
    return {
        'name': record.pw_name,
        'uid': record.pw_uid,
        'gid': record.pw_gid,
        'last_resort': last_resort,
        'warning': (
            f'Kasa Panel is running as "{record.pw_name}", a shared '
            'account it does not own. Set a daemon user: create a '
            'dedicated account and put its name in daemon_user below, '
            'then restart the daemon.'
        ) if last_resort else '',
    }


def own_tree(paths: List[str], uid: int, gid: int) -> None:
    """Hands files to the daemon account before privileges are dropped.

    Args:
        paths: Files and directories to hand over; missing ones are
            skipped.
        uid: New owner.
        gid: New group.
    """
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            os.chown(path, uid, gid)
        except OSError as error:
            _LOG.warning('cannot give %s to uid %d: %s%s', path, uid, error,
                         _chown_hint())
            continue
        if not os.path.isdir(path):
            continue
        for base, directories, files in os.walk(path):
            for name in directories + files:
                try:
                    os.chown(os.path.join(base, name), uid, gid)
                except OSError as error:
                    _LOG.warning('cannot give %s to uid %d: %s',
                                 os.path.join(base, name), uid, error)


def own_directory_if_allowed(path: str, uid: int, gid: int,
                             allowed: Set[str]) -> bool:
    """Hands over a directory, but only one that is genuinely ours.

    Writing a file atomically means writing a temporary file beside it
    and renaming, so the daemon needs write permission on the directory
    holding its configuration, not just on the file.

    Which directories qualify is decided by the caller and passed in,
    rather than guessed from the name.  Guessing was a mistake: a rule
    like "any directory called kasapanel" also matches a source
    checkout, and quietly changing the ownership of somebody's working
    directory is not a thing a daemon should do at start up.  Anything
    not on the list is left exactly as it was found and reported
    instead.

    Args:
        path: The directory to consider.
        uid: New owner.
        gid: New group.
        allowed: Directories the daemon may take ownership of.

    Returns:
        True when the directory was handed over.
    """
    if not path or not os.path.isdir(path):
        return False
    here = os.path.realpath(path)
    if here not in {os.path.realpath(one) for one in allowed if one}:
        return False
    try:
        os.chown(path, uid, gid)
    except OSError as error:
        _LOG.warning('cannot give %s to uid %d: %s', path, uid, error)
        return False
    return True


def refusal_advice(record: pwd.struct_passwd) -> str:
    """Explains a refused drop in terms of the unit file.

    Args:
        record: The account the daemon was trying to become.

    Returns:
        A sentence naming the likely cause and where to look.
    """
    missing = missing_capabilities()
    state = f'{describe_privileges()}] [{capability_report()}'
    if missing:
        return f'{capability_advice(missing)} [{state}]'
    report = privilege_report()
    if report['user_namespace']:
        return (
            f'the capabilities needed are held, but this process is in a '
            f'user namespace where {record.pw_name} does not exist. Under '
            f'systemd that is DynamicUser= or PrivateUsers= in the unit; '
            f'neither can be used with a daemon that drops privileges '
            f'itself. [{state}]')
    if report['seccomp'] not in ('', '0'):
        return (
            'the capabilities needed are held and the account exists, so '
            'the refusal is most likely a seccomp filter blocking the '
            'setuid family. Check SystemCallFilter= in the unit; it must '
            f'allow @setuid. [{state}]')
    return (
        'the capabilities needed are held, so the refusal comes from '
        'something outside this daemon: a security module such as '
        'AppArmor or SELinux, or a unit setting that confines the '
        'process. Check "systemctl cat kasapanel" for drop-ins, and the '
        f'audit log for a denial at this moment. [{state}]')


def _chown_hint() -> str:
    """Adds the capability explanation to a failed chown, once.

    Returns:
        A short hint, or an empty string when capabilities are fine.
    """
    missing = [name for name in missing_capabilities()
               if name in ('CAP_CHOWN', 'CAP_FOWNER')]
    if not missing:
        return ''
    absent = ', '.join(missing)
    return f' ({absent} is not held by this process)'


def drop_privileges(record: pwd.struct_passwd) -> None:
    """Becomes the daemon account, permanently.

    Order matters: the group and the supplementary groups have to be set
    while still root, because after ``setuid`` there is no way back.

    Args:
        record: The account to become.

    Raises:
        PrivilegeError: If the change did not take, or if the process
            can still return to root afterwards.
    """
    if os.getuid() != 0:
        return
    try:
        os.initgroups(record.pw_name, record.pw_gid)
        os.setgid(record.pw_gid)
        os.setuid(record.pw_uid)
    except OSError as error:
        advice = capability_advice(missing_capabilities())
        if not advice:
            # The capabilities are all held, so the refusal is coming
            # from somewhere else.  Say so rather than repeating advice
            # that has already been followed.
            advice = (
                'the capabilities needed are all held, so something else '
                'is refusing: look for a drop-in under '
                '/etc/systemd/system/kasapanel.service.d/, for '
                'User= being overridden, or for a security module '
                '(AppArmor, SELinux) denying the change -- check '
                '"systemctl cat kasapanel" and the audit log')
        raise PrivilegeError(
            f'cannot become {record.pw_name} (uid {record.pw_uid}): '
            f'{error}. {advice} [{capability_report()}]') from error
    if os.getuid() != record.pw_uid or os.geteuid() != record.pw_uid:
        raise PrivilegeError(
            f'privileges were not dropped to {record.pw_name}')
    try:
        os.setuid(0)
    except OSError:
        pass  # Good: root is gone for this process.
    else:
        raise PrivilegeError(
            'privileges were dropped but root can still be regained')
    clear_capabilities()
    left = effective_capabilities()
    if left:
        _LOG.warning('capabilities remain after dropping: %#x', left)
    _LOG.info('running as %s (uid %d, gid %d), capabilities %s',
              record.pw_name, record.pw_uid, record.pw_gid,
              'cleared' if not left else f'{left:#x}')


def _send(sock: socket.socket, payload: dict) -> None:
    """Writes one length-prefixed JSON message.

    Args:
        sock: Connected socket.
        payload: The message.

    Raises:
        HelperError: If the socket will not take it.
    """
    body = json.dumps(payload).encode('utf-8')
    try:
        sock.sendall(_LENGTH.pack(len(body)) + body)
    except OSError as error:
        raise HelperError(f'cannot reach the helper: {error}') from error


def _receive(sock: socket.socket) -> Optional[dict]:
    """Reads one length-prefixed JSON message.

    Args:
        sock: Connected socket.

    Returns:
        The message, or None when the other end has gone.

    Raises:
        HelperError: If the message is unreadable or absurdly large.
    """
    header = _read_exactly(sock, _LENGTH.size)
    if header is None:
        return None
    (size,) = _LENGTH.unpack(header)
    if size > MAX_MESSAGE_BYTES:
        raise HelperError('the helper sent an oversized message')
    body = _read_exactly(sock, size)
    if body is None:
        return None
    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise HelperError(f'unreadable message: {error}') from error


def _read_exactly(sock: socket.socket, size: int) -> Optional[bytes]:
    """Reads exactly so many bytes.

    Args:
        sock: Connected socket.
        size: How many bytes are wanted.

    Returns:
        The bytes, or None at end of stream.

    Raises:
        HelperError: If the socket fails.
    """
    chunks = []
    left = size
    while left:
        try:
            piece = sock.recv(left)
        except socket.timeout as error:
            raise HelperError(
                'the helper did not answer in time') from error
        except OSError as error:
            raise HelperError(f'cannot read from the helper: '
                              f'{error}') from error
        if not piece:
            return None
        chunks.append(piece)
        left -= len(piece)
    return b''.join(chunks)


def describe_exit(status: Optional[int]) -> str:
    """Explains how the helper process ended.

    Args:
        status: The wait status, or None when it could not be read.

    Returns:
        A sentence naming the signal or the exit code.
    """
    if status is None:
        return 'the helper process disappeared'
    if os.WIFSIGNALED(status):
        number = os.WTERMSIG(status)
        try:
            name = signal.Signals(number).name
        except ValueError:  # pragma: no cover - unknown signal number
            name = f'signal {number}'
        if number == signal.SIGKILL:
            return (f'the helper was killed by {name}, which usually means '
                    'the out-of-memory killer or an administrator')
        return f'the helper was killed by {name}'
    code = os.WEXITSTATUS(status)
    if code == 0:
        return 'the helper exited cleanly without being asked to'
    return f'the helper exited with status {code}'


class AuthHelper:
    """The daemon's end of the conversation with the helper process."""

    def __init__(self, sock: socket.socket, pid: int):
        """Wraps the socket to the helper.

        Args:
            sock: Socket connected to the helper.
            pid: Process id of the helper.
        """
        self._sock = sock
        self._sock.settimeout(CLIENT_TIMEOUT_SECONDS)
        self.pid = pid
        self._lock = threading.Lock()
        self._closed = False
        self._alive = True
        self._expected = False
        self._watcher: Optional[threading.Thread] = None
        self.exit_reason = ''

    @property
    def alive(self) -> bool:
        """Says whether the helper process is still there."""
        return self._alive and not self._closed

    def watch(self, on_death: Any) -> None:
        """Reports the helper's death as soon as it happens.

        Without this, a dead helper is only discovered by whoever tries
        to sign in next, and the daemon carries on in a state where
        nobody can.  It is better to stop and let the service manager
        start a whole, working daemon again.

        Args:
            on_death: Called with a sentence explaining how the helper
                ended.  Not called when the helper was closed on
                purpose.
        """
        def wait() -> None:
            """Blocks until the helper exits, then reports it."""
            status = None
            try:
                _, status = os.waitpid(self.pid, 0)
            except OSError:
                status = None
            self._alive = False
            self.exit_reason = describe_exit(status)
            if self._expected:
                return
            _LOG.critical('authentication helper (pid %d) has gone: %s',
                          self.pid, self.exit_reason)
            on_death(self.exit_reason)

        self._watcher = threading.Thread(
            target=wait, name='kasa-helper-watch', daemon=True)
        self._watcher.start()

    def authenticate(self, username: str, password: str,
                     service: str) -> bool:
        """Asks the helper to check one password.

        Args:
            username: Account name.
            password: The password as typed.
            service: PAM service name.

        Returns:
            Whether PAM accepted it.

        Raises:
            HelperError: If the helper cannot answer.
        """
        if self._closed:
            raise HelperError('the authentication helper has been closed')
        gone = ('the authentication helper stopped; the daemon must be '
                'restarted before anyone can sign in')
        with self._lock:
            try:
                _send(self._sock, {'op': 'auth', 'username': username,
                                   'password': password,
                                   'service': service})
                answer = _receive(self._sock)
            except HelperError as error:
                if 'in time' in str(error):
                    raise  # PAM is slow, not absent.
                self._closed = True
                raise HelperError(gone) from error
        if answer is None:
            self._closed = True
            raise HelperError(gone)
        if answer.get('error'):
            raise HelperError(str(answer['error']))
        return bool(answer.get('accepted'))

    def close(self) -> None:
        """Closes the socket and reaps the helper.

        The helper notices the closed socket and exits, so this is also
        how it is stopped.  The death is marked expected first, so the
        watcher does not read a deliberate shutdown as a failure.
        """
        if self._closed:
            return
        self._expected = True
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass
        if self._watcher is not None:
            self._watcher.join(timeout=5)
            return
        try:
            os.waitpid(self.pid, 0)
        except OSError:
            pass


def _serve_helper(sock: socket.socket) -> None:
    """Answers authentication requests until the daemon goes away.

    This is the whole of the privileged program.  It never touches the
    network, never reads a configuration file and never writes one; the
    only thing it can be asked is whether a password is correct.

    Args:
        sock: Socket connected to the daemon.
    """
    from kasapanel import auth  # pylint: disable=import-outside-toplevel

    def _time_is_up(number: int, frame: Any) -> None:
        """Abandons a PAM conversation that will not finish.

        Args:
            number: Signal number.
            frame: Unused stack frame.

        Raises:
            TimeoutError: Always.
        """
        del number, frame
        raise TimeoutError('PAM did not answer in time')

    try:
        signal.signal(signal.SIGALRM, _time_is_up)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        alarms = True
    except ValueError:
        # Only the main thread of a process may install handlers.  The
        # forked helper always is one; the tests drive this loop in a
        # thread, where a PAM call simply goes untimed.
        alarms = False

    while True:
        try:
            request = _receive(sock)
        except HelperError:
            break
        if request is None:
            break  # The daemon has exited.
        answer = {'accepted': False}
        if request.get('op') != 'auth':
            answer = {'error': 'unsupported request'}
        else:
            if alarms:
                signal.alarm(HELPER_TIMEOUT_SECONDS)
            try:
                session = auth.new_authenticator()
                answer = {'accepted': bool(session.authenticate(
                    str(request.get('username', '')),
                    str(request.get('password', '')),
                    service=str(request.get('service', 'login'))))}
            except TimeoutError as error:
                answer = {'error': str(error)}
            except auth.AuthError as error:
                answer = {'error': str(error)}
            except Exception as error:  # pylint: disable=broad-except
                # The helper must outlive any single bad request; if it
                # dies, nobody can sign in until the daemon restarts.
                answer = {'error': f'PAM failed: {error}'}
            finally:
                if alarms:
                    signal.alarm(0)
        try:
            _send(sock, answer)
        except HelperError:
            break


def start_helper() -> Optional[AuthHelper]:
    """Forks the privileged authentication helper.

    Call this while still root and before any thread is started: forking
    a process that already has threads is not safe, and the helper must
    not inherit the listening socket.

    Returns:
        The daemon's end of the connection, or None when the daemon is
        not privileged and therefore has nothing to separate.

    Raises:
        PrivilegeError: If the helper cannot be started.
    """
    if os.getuid() != 0:
        _LOG.info('not running as root; PAM will be used in this process')
        return None
    parent_end, child_end = socket.socketpair(socket.AF_UNIX,
                                              socket.SOCK_STREAM)
    try:
        pid = os.fork()
    except OSError as error:
        parent_end.close()
        child_end.close()
        raise PrivilegeError(
            f'cannot start the authentication helper: {error}') from error
    if pid == 0:
        # The helper. Nothing here may return to the daemon's code.
        status = 0
        try:
            parent_end.close()
            _serve_helper(child_end)
        except BaseException:  # pylint: disable=broad-except
            status = 1
        finally:
            try:
                child_end.close()
            except OSError:
                pass
            os._exit(status)  # pylint: disable=protected-access
    child_end.close()
    _LOG.info('authentication helper started as pid %d', pid)
    return AuthHelper(parent_end, pid)


def group_names(record: pwd.struct_passwd) -> List[str]:
    """Lists the groups the daemon account belongs to.

    Args:
        record: The account.

    Returns:
        Group names, primary first.

    Raises:
        PrivilegeError: If the primary group does not exist.
    """
    try:
        primary = grp.getgrgid(record.pw_gid).gr_name
    except KeyError as error:
        raise PrivilegeError(
            f'{record.pw_name} has no group {record.pw_gid}') from error
    others = [group.gr_name for group in grp.getgrall()
              if record.pw_name in group.gr_mem]
    return [primary] + sorted(others)


def access_as(record: pwd.struct_passwd, path: str, mode: int) -> bool:
    """Asks whether an account may use a path, by becoming it and trying.

    Working this out from the mode bits is wrong on any system that
    uses POSIX ACLs, and plenty do: a key that reads 0600 root:root can
    still be readable by a service account through
    ``setfacl -m u:www-data:r``, which is exactly how certificates are
    usually distributed.  Reading st_mode would call that unreadable and
    send somebody looking for a permission problem that does not exist.

    So the question is put to the kernel instead.  A short-lived child
    becomes the account -- supplementary groups included -- and calls
    ``access``, which consults ACLs, the mode bits and everything else
    the kernel would consult for real.

    Args:
        record: The account to ask about.
        path: File or directory in question.
        mode: An ``os.access`` mode.

    Returns:
        True when that account may use the path that way.
    """
    if record is None or os.getuid() != 0 or record.pw_uid == os.getuid():
        return _plain_access(path, mode)
    try:
        pid = os.fork()
    except OSError:  # pragma: no cover - out of processes
        return _mode_access(path, mode, record)
    if pid == 0:  # pragma: no cover - the child never returns
        code = 1
        try:
            os.initgroups(record.pw_name, record.pw_gid)
            os.setgid(record.pw_gid)
            os.setuid(record.pw_uid)
            code = 0 if _plain_access(path, mode) else 1
        except OSError:
            code = 2
        finally:
            os._exit(code)  # pylint: disable=protected-access
    try:
        _, status = os.waitpid(pid, 0)
    except OSError:  # pragma: no cover
        return False
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def _plain_access(path: str, mode: int) -> bool:
    """Tests access as whoever this process currently is.

    Args:
        path: File or directory in question.
        mode: An ``os.access`` mode.

    Returns:
        True when the access is permitted.
    """
    effective = os.access in os.supports_effective_ids
    try:
        return os.access(path, mode, effective_ids=effective)
    except (OSError, ValueError, NotImplementedError):  # pragma: no cover
        return False


def _mode_access(path: str, mode: int,
                 record: pwd.struct_passwd) -> bool:
    """Guesses from the mode bits, when asking properly is not possible.

    Only used if a child cannot be started.  It cannot see ACLs, so it
    errs towards saying yes rather than raising a false alarm.

    Args:
        path: File or directory in question.
        mode: An ``os.access`` mode.
        record: The account to guess about.

    Returns:
        True when the mode bits allow it.
    """
    try:
        info = os.stat(path)
    except OSError:
        return False
    wanted = 0
    if mode & os.R_OK:
        wanted |= 0o444
    if mode & os.W_OK:
        wanted |= 0o222
    if mode & os.X_OK:
        wanted |= 0o111
    if info.st_uid == record.pw_uid:
        return bool(info.st_mode & wanted & 0o700)
    groups = {record.pw_gid}
    try:
        groups.update(group.gr_gid for group in grp.getgrall()
                      if record.pw_name in group.gr_mem)
    except OSError:  # pragma: no cover
        pass
    if info.st_gid in groups:
        return bool(info.st_mode & wanted & 0o070)
    return bool(info.st_mode & wanted & 0o007)


def readable_by(path: str, record: pwd.struct_passwd) -> bool:
    """Says whether the daemon account will be able to read a file.

    Args:
        path: File to test.
        record: The account that will be doing the reading.

    Returns:
        True when the account can read it.
    """
    return access_as(record, path, os.R_OK)


def writable_by(path: str, record: pwd.struct_passwd) -> bool:
    """Says whether the daemon account can write inside a directory.

    Removing or replacing a file needs write permission on the
    directory holding it, not on the file.

    Args:
        path: Directory to test.
        record: The account that will be doing the writing.

    Returns:
        True when the account may create and remove entries there.
    """
    return access_as(record, path, os.W_OK | os.X_OK)


def helper_is_gone(error: OSError) -> bool:
    """Says whether an error means the helper has exited.

    Args:
        error: The error raised by a socket call.

    Returns:
        True for the errors a dead peer produces.
    """
    return error.errno in (errno.EPIPE, errno.ECONNRESET, errno.EBADF)
