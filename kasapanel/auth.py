# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Sign in with Linux PAM, plus session bookkeeping.

Passwords are checked against PAM, so the panel has no user database of
its own and inherits whatever the host already enforces.  Two extra
rules are applied on top:

* the account must appear in ``allowed_users`` or belong to one of the
  ``allowed_groups``, unless both lists are empty;
* repeated failures lock an account out for a while.

Sessions are held in memory only.  Restarting the daemon signs everyone
out, which is the behaviour an operator expects from a daemon that
holds no session database.

Reading shadow passwords normally needs privileges.  Running the daemon
as root works; running it as an unprivileged user usually needs that
user to be in the ``shadow`` group, or a PAM service that does not read
the shadow file.
"""

import ctypes.util
import dataclasses
import datetime
import grp
import logging
import pwd
import secrets
import sys
import threading
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

def _import_pam():
    """Imports python-pam without letting a broken install stop us.

    A package that is present but unimportable is a real situation:
    python-pam 2.0.2 imports ``six`` without declaring it, so a plain
    install can leave a module behind that raises on import.  The daemon
    still starts, and says why nobody can sign in.

    Returns:
        The module, or ``None``, and the text of any import failure.
    """
    try:  # pragma: no cover - depends on the host having python-pam.
        import pam  # pylint: disable=import-outside-toplevel
        return pam, ''
    except ImportError as error:  # pragma: no cover
        return None, str(error)
    except Exception as error:  # pylint: disable=broad-except
        return None, f'{type(error).__name__}: {error}'


pam_module, _IMPORT_ERROR = _import_pam()
PAM_AVAILABLE = pam_module is not None

SESSION_COOKIE = 'kasapanel_session'
SESSION_HEADER = 'X-Kasa-Session'
CSRF_HEADER = 'X-Kasa-CSRF'


class AuthError(Exception):
    """Raised when a sign in attempt is refused."""


def pam_reason() -> str:
    """Explains why PAM is unavailable, in one sentence.

    The message names the actual cause rather than assuming the package
    is absent.  python-pam 2.0.2 in particular imports ``six`` without
    declaring it as a dependency, so a plain ``pip install python-pam``
    can leave an installed but unimportable module behind.

    Returns:
        An empty string when PAM works, otherwise the reason.
    """
    if PAM_AVAILABLE:
        return ''
    if not _IMPORT_ERROR:
        return 'PAM support is unavailable.'
    if "named 'six'" in _IMPORT_ERROR:
        return ('python-pam is installed but cannot be imported: it needs '
                'the "six" module, which it does not declare as a '
                'dependency and which is not installed.')
    if "named 'pam'" in _IMPORT_ERROR:
        return ('python-pam is not installed for the interpreter running '
                'this daemon.')
    return f'python-pam could not be imported: {_IMPORT_ERROR}'


def pam_advice() -> str:
    """Suggests the command that fixes an unimportable python-pam.

    Returns:
        An empty string when PAM works, otherwise a suggestion.
    """
    if PAM_AVAILABLE:
        return ''
    if "named 'six'" in _IMPORT_ERROR:
        return f'{sys.executable} -m pip install six'
    return f'{sys.executable} -m pip install python-pam six'


def pam_diagnosis() -> Dict[str, Any]:
    """Collects everything an operator needs to fix a PAM problem.

    The interpreter path is the useful part: installing python-pam for
    one interpreter and running the daemon under another, or installing
    it for your own account while the daemon runs as root, are the two
    ways this goes wrong after the package itself is present.

    Returns:
        A mapping describing the PAM environment.
    """
    return {
        'available': PAM_AVAILABLE,
        'reason': pam_reason(),
        'advice': pam_advice(),
        'import_error': _IMPORT_ERROR,
        'interpreter': sys.executable,
        'module': getattr(pam_module, '__file__', '') or '',
        'libpam': ctypes.util.find_library('pam') or '',
    }


def new_authenticator() -> Any:
    """Builds a python-pam authenticator across its two API names.

    Returns:
        A python-pam authenticator object.

    Raises:
        AuthError: If PAM is unavailable or libpam cannot be loaded.
    """
    if not PAM_AVAILABLE:
        raise AuthError(pam_reason())
    factory = getattr(pam_module, 'pam', None)
    if factory is None:
        factory = getattr(pam_module, 'PamAuthenticator', None)
    if factory is None:
        raise AuthError('python-pam is installed but exposes no '
                        'authenticator; check its version.')
    try:
        return factory()  # pylint: disable=not-callable
    except (OSError, AttributeError, TypeError) as error:
        _LOG.error('libpam could not be loaded: %s', error)
        raise AuthError(
            'the system PAM library could not be loaded; install libpam '
            '(libpam0g on Debian and Ubuntu) and make sure ldconfig is on '
            'the daemon PATH.') from error


@dataclasses.dataclass
class Session:
    """One signed in browser session.

    Attributes:
        token: Secret value stored in the session cookie.
        csrf_token: Secret the browser must echo on every write.
        username: The account that signed in.
        created_at: When the session was created.
        last_seen: When the session was last used.
        remote: Client address, kept for the sessions list.
        user_agent: Client user agent, kept for the sessions list.
    """

    token: str
    csrf_token: str
    username: str
    created_at: datetime.datetime
    last_seen: datetime.datetime
    remote: str = ''
    user_agent: str = ''

    def to_dict(self) -> Dict[str, Any]:
        """Returns a JSON friendly view without the secret token."""
        return {
            'username': self.username,
            'created_at': self.created_at.isoformat(timespec='seconds'),
            'last_seen': self.last_seen.isoformat(timespec='seconds'),
            'remote': self.remote,
            'user_agent': self.user_agent[:120],
        }


def user_groups(username: str) -> List[str]:
    """Lists the groups a local account belongs to.

    Args:
        username: Account name.

    Returns:
        Group names, primary group included.  An unknown account gives
        an empty list.
    """
    try:
        record = pwd.getpwnam(username)
    except KeyError:
        return []
    names = set()
    try:
        names.add(grp.getgrgid(record.pw_gid).gr_name)
    except KeyError:  # pragma: no cover - inconsistent group database.
        pass
    for group in grp.getgrall():
        if username in group.gr_mem:
            names.add(group.gr_name)
    return sorted(names)


class Authenticator:
    """Checks passwords against PAM and applies the account policy."""

    def __init__(self, settings: Any, verifier: Any = None):
        """Initialises the authenticator.

        Args:
            settings: A :class:`kasapanel.config.PanelConfig` instance.
            verifier: Optional callable taking a username, a password
                and a PAM service name and returning whether PAM
                accepted them.  The daemon passes the privileged helper
                here once it has dropped privileges; without one, PAM is
                used in this process.  Either way the policy below --
                the allow lists and the lockout -- stays on this side.
        """
        self._settings = settings
        self._verifier = verifier
        self._lock = threading.Lock()
        self._failures: Dict[str, int] = {}
        self._locked_until: Dict[str, datetime.datetime] = {}

    def use_verifier(self, verifier: Any) -> None:
        """Sends password checks to a helper instead of to local PAM.

        Args:
            verifier: The callable described in :meth:`__init__`.
        """
        self._verifier = verifier

    def update_settings(self, settings: Any) -> None:
        """Replaces the settings used for the account policy.

        Args:
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        self._settings = settings

    def _is_allowed(self, username: str) -> bool:
        """Applies the allowed users and groups policy.

        Args:
            username: Account name.

        Returns:
            True when the account may use the panel.
        """
        users = [name.strip() for name
                 in getattr(self._settings, 'allowed_users', [])
                 if name.strip()]
        groups = [name.strip() for name
                  in getattr(self._settings, 'allowed_groups', [])
                  if name.strip()]
        if not users and not groups:
            return True
        if username in users:
            return True
        return bool(set(groups) & set(user_groups(username)))

    def _check_lockout(self, username: str) -> None:
        """Refuses accounts that are currently locked out.

        Args:
            username: Account name.

        Raises:
            AuthError: If the account is locked out.
        """
        with self._lock:
            until = self._locked_until.get(username)
            if until is None:
                return
            now = datetime.datetime.now()
            if now >= until:
                self._locked_until.pop(username, None)
                self._failures.pop(username, None)
                return
            wait = int((until - now).total_seconds())
            raise AuthError(
                f'too many failed attempts; try again in {wait} seconds')

    def _record_failure(self, username: str) -> None:
        """Counts a failed attempt and locks the account if needed.

        Args:
            username: Account name.
        """
        limit = int(getattr(self._settings, 'max_login_failures', 5))
        seconds = int(getattr(self._settings, 'lockout_seconds', 300))
        with self._lock:
            count = self._failures.get(username, 0) + 1
            self._failures[username] = count
            if limit > 0 and count >= limit:
                self._locked_until[username] = (
                    datetime.datetime.now()
                    + datetime.timedelta(seconds=seconds))
                _LOG.warning('locked out %s for %s seconds', username, seconds)

    def _record_success(self, username: str) -> None:
        """Clears the failure counters for an account.

        Args:
            username: Account name.
        """
        with self._lock:
            self._failures.pop(username, None)
            self._locked_until.pop(username, None)

    def authenticate(self, username: str, password: str) -> str:
        """Checks a username and password against PAM.

        Args:
            username: Account name.
            password: The password as typed.

        Returns:
            The account name, normalised.

        Raises:
            AuthError: If PAM is unavailable, the credentials are wrong,
                the account is not allowed, or it is locked out.
        """
        username = (username or '').strip()
        if not username or not password:
            raise AuthError('enter a username and a password')
        if self._verifier is None and not PAM_AVAILABLE:
            raise AuthError(pam_reason())
        self._check_lockout(username)
        service = getattr(self._settings, 'pam_service', 'login')
        session = None
        try:
            if self._verifier is not None:
                accepted = self._verifier(username, password, service)
            else:
                session = new_authenticator()
                accepted = session.authenticate(username, password,
                                                service=service)
        except AuthError:
            raise
        except Exception as error:
            # python-pam raises bare exceptions for conversation and
            # service problems; a misconfigured service should read as a
            # server fault, not as a wrong password.
            # pylint: disable=broad-except
            _LOG.error('PAM service %r failed: %s', service, error)
            raise AuthError(
                f'the PAM service "{service}" could not be used: {error}'
            ) from error
        if not accepted:
            self._record_failure(username)
            # Only local PAM reports a reason; a helper answers yes or no.
            reason = getattr(session, 'reason', 'refused by PAM')
            _LOG.info('failed sign in for %s: %s', username, reason)
            raise AuthError('that username and password did not match')
        if not self._is_allowed(username):
            self._record_failure(username)
            raise AuthError('that account is not allowed to use this panel')
        self._record_success(username)
        _LOG.info('signed in %s', username)
        return username


class SessionManager:
    """Holds the signed in sessions of every connected user."""

    def __init__(self, settings: Any):
        """Initialises the manager.

        Args:
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        self._settings = settings
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}

    def update_settings(self, settings: Any) -> None:
        """Replaces the settings used for session lifetimes.

        Args:
            settings: A :class:`kasapanel.config.PanelConfig` instance.
        """
        self._settings = settings

    @property
    def _idle_limit(self) -> datetime.timedelta:
        """Returns how long a session may sit idle."""
        minutes = int(getattr(self._settings, 'session_minutes', 720))
        return datetime.timedelta(minutes=max(minutes, 1))

    def create(self, username: str, remote: str = '',
               user_agent: str = '') -> Session:
        """Starts a session for an account.

        Args:
            username: The account that signed in.
            remote: Client address.
            user_agent: Client user agent string.

        Returns:
            The new session.
        """
        now = datetime.datetime.now()
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(24),
            username=username,
            created_at=now,
            last_seen=now,
            remote=remote,
            user_agent=user_agent)
        with self._lock:
            self._sessions[session.token] = session
        return session

    def get(self, token: str) -> Optional[Session]:
        """Looks a session up and refreshes its idle timer.

        Args:
            token: Value of the session cookie.

        Returns:
            The session, or None when it is unknown or has expired.
        """
        if not token:
            return None
        now = datetime.datetime.now()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if now - session.last_seen > self._idle_limit:
                self._sessions.pop(token, None)
                return None
            session.last_seen = now
            return session

    def destroy(self, token: str) -> None:
        """Ends one session.

        Args:
            token: Value of the session cookie.
        """
        with self._lock:
            self._sessions.pop(token, None)

    def prune(self) -> int:
        """Drops sessions that have been idle for too long.

        Returns:
            How many sessions were dropped.
        """
        now = datetime.datetime.now()
        limit = self._idle_limit
        with self._lock:
            stale = [token for token, session in self._sessions.items()
                     if now - session.last_seen > limit]
            for token in stale:
                self._sessions.pop(token, None)
        return len(stale)

    def active(self) -> List[Dict[str, Any]]:
        """Returns a JSON friendly list of the current sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
        sessions.sort(key=lambda item: item.created_at)
        return [session.to_dict() for session in sessions]
