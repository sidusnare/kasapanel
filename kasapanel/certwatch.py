# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Picks up a renewed TLS certificate without restarting the daemon.

Certificates are renewed by something else -- certbot, a company CA, a
script -- which writes a new file over the configured path and has no
way to tell this daemon about it.  Restarting to pick it up would drop
every session and every open connection, which is a poor trade for a
file that changed.

So a thread watches instead.  It compares the certificate in the
configured file with the one the running server is serving, and when
they differ it loads the file into the live TLS context.  Python's
:class:`ssl.SSLContext` hands its certificate to each connection as that
connection is accepted, so re-keying the context is enough: connections
already open finish under the old certificate, everything accepted
afterwards gets the new one, the listening socket is never rebound and
no session is lost.

The watch is deliberately quiet.  A certificate with months left needs
no attention, so nothing is read until the last two days, when checks
run every fifteen minutes.  Once the live certificate has actually
expired the daemon is serving something browsers reject, so the check
goes to once a minute to shorten that outage as far as it can.  An
operator who renews early can hurry it along with SIGHUP.
"""

import datetime
import logging
import ssl
import subprocess
import threading
from typing import Any, Callable, Optional, Tuple

_LOG = logging.getLogger(__name__)

# The last stretch before expiry, and how often to look during it.
WATCH_WINDOW = datetime.timedelta(hours=48)
NEAR_EXPIRY_SECONDS = 15 * 60

# Once the certificate is dead, look far more often.
AFTER_EXPIRY_SECONDS = 60

# Never sleep longer than this, so a changed configuration or a clock
# jump is noticed in reasonable time.
MAX_IDLE_SECONDS = 60 * 60

try:  # pragma: no cover - depends on the host having cryptography.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    x509 = None
    hashes = None
    CRYPTOGRAPHY_AVAILABLE = False


class CertificateError(Exception):
    """Raised when a certificate file cannot be read or understood."""


def describe(path: str) -> Tuple[str, Optional[datetime.datetime]]:
    """Reads the fingerprint and expiry of a certificate file.

    Args:
        path: PEM certificate file.

    Returns:
        The SHA-256 fingerprint as lower case hex, and the moment the
        certificate stops being valid, in UTC.

    Raises:
        CertificateError: If the file cannot be read or parsed.  A file
            caught mid-write raises this too, which is why callers
            treat a failure as "try again later" rather than as fatal.
    """
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError as error:
        raise CertificateError(f'cannot read {path}: {error}') from error
    if not data.strip():
        raise CertificateError(f'{path} is empty')
    if CRYPTOGRAPHY_AVAILABLE:
        return _describe_with_cryptography(data, path)
    return _describe_with_openssl(path)


def _describe_with_cryptography(
        data: bytes, path: str) -> Tuple[str, Optional[datetime.datetime]]:
    """Parses a certificate with the cryptography package.

    Args:
        data: The file contents.
        path: The file name, for error messages.

    Returns:
        The fingerprint and expiry.

    Raises:
        CertificateError: If the certificate cannot be parsed.
    """
    try:
        certificate = x509.load_pem_x509_certificate(data)
    except ValueError as error:
        raise CertificateError(f'{path} is not a certificate: '
                               f'{error}') from error
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    expires = getattr(certificate, 'not_valid_after_utc', None)
    if expires is None:  # cryptography older than 42
        expires = certificate.not_valid_after.replace(
            tzinfo=datetime.timezone.utc)
    return fingerprint, expires


def _describe_with_openssl(
        path: str) -> Tuple[str, Optional[datetime.datetime]]:
    """Parses a certificate by shelling out to openssl.

    Args:
        path: PEM certificate file.

    Returns:
        The fingerprint and expiry.

    Raises:
        CertificateError: If openssl is missing or unhappy.
    """
    try:
        result = subprocess.run(
            ['openssl', 'x509', '-in', path, '-noout', '-enddate',
             '-fingerprint', '-sha256'],
            check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise CertificateError(
            f'cannot inspect {path}: {error}') from error
    fingerprint = ''
    expires = None
    for line in result.stdout.splitlines():
        if line.startswith('notAfter='):
            stamp = line.split('=', 1)[1].strip()
            try:
                expires = datetime.datetime.strptime(
                    stamp, '%b %d %H:%M:%S %Y %Z').replace(
                        tzinfo=datetime.timezone.utc)
            except ValueError:
                expires = None
        elif '=' in line and 'Fingerprint' in line:
            fingerprint = line.split('=', 1)[1].replace(':', '').lower()
    if not fingerprint:
        raise CertificateError(f'no fingerprint found for {path}')
    return fingerprint, expires


def summarise(path: str) -> dict:
    """Describes a certificate file for the settings page.

    Args:
        path: PEM certificate file.

    Returns:
        A JSON friendly mapping.  On any problem the mapping carries an
        ``error`` and the fields it could not read are left empty, so
        the page can still render.
    """
    summary = {
        'path': path, 'subject': '', 'issuer': '', 'fingerprint': '',
        'not_before': '', 'expires': '', 'self_signed': None, 'error': '',
    }
    try:
        fingerprint, expires = describe(path)
    except CertificateError as error:
        summary['error'] = str(error)
        return summary
    summary['fingerprint'] = fingerprint
    summary['expires'] = expires.isoformat() if expires else ''
    if not CRYPTOGRAPHY_AVAILABLE:
        return summary
    try:
        with open(path, 'rb') as handle:
            certificate = x509.load_pem_x509_certificate(handle.read())
    except (OSError, ValueError) as error:  # pragma: no cover
        summary['error'] = str(error)
        return summary
    summary['subject'] = _common_name(certificate.subject)
    summary['issuer'] = _common_name(certificate.issuer)
    summary['self_signed'] = certificate.subject == certificate.issuer
    starts = getattr(certificate, 'not_valid_before_utc', None)
    if starts is None:  # pragma: no cover - cryptography older than 42
        starts = certificate.not_valid_before.replace(
            tzinfo=datetime.timezone.utc)
    summary['not_before'] = starts.isoformat()
    names = []
    try:
        extension = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName)
        names = [str(name) for name
                 in extension.value.get_values_for_type(x509.DNSName)]
        names += [str(address) for address
                  in extension.value.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:  # pragma: no cover
        pass
    summary['names'] = names
    return summary


def _common_name(name: Any) -> str:
    """Pulls the common name out of a certificate name.

    Args:
        name: An x509 name.

    Returns:
        The common name, or the whole name when there is none.
    """
    from cryptography.x509.oid import NameOID  # pylint: disable=C0415
    found = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    if found:
        return str(found[0].value)
    return name.rfc4514_string()


class CertificateWatcher:
    """Watches the certificate file and re-keys the server when it moves."""

    def __init__(self, settings: Any, context: ssl.SSLContext,
                 activity: Any = None,
                 clock: Callable[[], datetime.datetime] = None):
        """Prepares the watcher without starting it.

        Args:
            settings: The configuration in effect; read again on every
                pass so an edited certificate path is honoured.
            context: The TLS context the running server hands to each
                new connection.
            activity: Activity log to record reloads in, if any.
            clock: Returns the current UTC time; injected by the tests.
        """
        self._settings = settings
        self._context = context
        self._activity = activity
        self._clock = clock or (
            lambda: datetime.datetime.now(datetime.timezone.utc))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.fingerprint = ''
        self.expires: Optional[datetime.datetime] = None
        self.reloads = 0
        self.last_error = ''

    def adopt(self, path: str) -> None:
        """Records the certificate the server started with.

        Args:
            path: The certificate file that was loaded at start up.
        """
        try:
            self.fingerprint, self.expires = describe(path)
        except CertificateError as error:
            self.last_error = str(error)
            _LOG.warning('cannot read the live certificate: %s', error)

    def update_settings(self, settings: Any) -> None:
        """Replaces the settings and looks again straight away.

        Args:
            settings: The new configuration.
        """
        self._settings = settings
        self.check_now()

    def start(self) -> None:
        """Starts the watching thread."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, name='kasa-certwatch', daemon=True)
        self._thread.start()
        _LOG.info('certificate watch started; live certificate expires %s',
                  self.expires.isoformat() if self.expires else 'unknown')

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the watching thread.

        Args:
            timeout: Seconds to wait for the thread to finish.
        """
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def check_now(self) -> None:
        """Asks the thread to compare the files immediately."""
        self._wake.set()

    def interval(self, now: datetime.datetime = None) -> float:
        """Returns how long to wait before the next comparison.

        Args:
            now: Current time; defaults to the injected clock.

        Returns:
            Seconds to sleep.
        """
        moment = now or self._clock()
        if self.expires is None:
            return NEAR_EXPIRY_SECONDS
        remaining = self.expires - moment
        if remaining <= datetime.timedelta(0):
            return AFTER_EXPIRY_SECONDS
        if remaining <= WATCH_WINDOW:
            return NEAR_EXPIRY_SECONDS
        # Still comfortable: sleep until the window opens, but wake up
        # from time to time in case the configuration changed.
        until_window = (remaining - WATCH_WINDOW).total_seconds()
        return min(until_window, MAX_IDLE_SECONDS)

    def watching(self, now: datetime.datetime = None) -> bool:
        """Says whether the certificate is close enough to look at.

        Args:
            now: Current time; defaults to the injected clock.

        Returns:
            True inside the last 48 hours, or after expiry.
        """
        moment = now or self._clock()
        if self.expires is None:
            return True
        return self.expires - moment <= WATCH_WINDOW

    def check(self, force: bool = False) -> bool:
        """Compares the configured file with the live certificate.

        Args:
            force: Compare even outside the watch window.

        Returns:
            True when the certificate was replaced.
        """
        if not force and not self.watching():
            return False
        path = self._settings.resolved_certificate()
        key = self._settings.resolved_key()
        try:
            fingerprint, expires = describe(path)
        except CertificateError as error:
            self._note_error(f'certificate watch: {error}')
            return False
        if fingerprint == self.fingerprint:
            return False
        _LOG.info('the certificate file at %s has changed; loading it', path)
        return self._reload(path, key, fingerprint, expires)

    def _reload(self, path: str, key: str, fingerprint: str,
                expires: Optional[datetime.datetime]) -> bool:
        """Loads a new certificate into the live TLS context.

        Args:
            path: Certificate file.
            key: Private key file.
            fingerprint: Fingerprint of the new certificate.
            expires: When the new certificate stops being valid.

        Returns:
            True when the swap succeeded.
        """
        # Try the pair in a throwaway context first.  Loading a
        # certificate and its key into a live context is not atomic:
        # OpenSSL takes the certificate, then the key, and if the key
        # does not match, the context is left holding a pair that
        # cannot complete a handshake at all.  Proving the pair
        # elsewhere first means the live context only ever sees a
        # combination that works.
        scratch = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            scratch.load_cert_chain(certfile=path, keyfile=key)
        except (ssl.SSLError, OSError, ValueError) as error:
            self._note_error(
                f'certificate watch: {path} and {key} are not a usable '
                f'pair, keeping the current certificate: {error}')
            return False
        with self._lock:
            try:
                self._context.load_cert_chain(certfile=path, keyfile=key)
            except (ssl.SSLError, OSError, ValueError) as error:
                self._note_error(
                    f'certificate watch: {path} could not be loaded, '
                    f'keeping the current certificate: {error}')
                return False
            self.fingerprint = fingerprint
            self.expires = expires
            self.reloads += 1
            self.last_error = ''
        when = expires.isoformat() if expires else 'unknown'
        _LOG.warning('TLS certificate replaced without a restart; '
                     'new certificate expires %s', when)
        if self._activity is not None:
            self._activity.add(
                f'Loaded a new TLS certificate, valid until {when}',
                level='info', source='tls')
        return True

    def _note_error(self, message: str) -> None:
        """Logs a problem once rather than on every pass.

        Args:
            message: What went wrong.
        """
        if message == self.last_error:
            return
        self.last_error = message
        _LOG.error('%s', message)
        if self._activity is not None:
            self._activity.add(message, level='error', source='tls')

    def status(self) -> dict:
        """Returns what the watcher knows, for the settings tab.

        Returns:
            A JSON friendly mapping.
        """
        return {
            'fingerprint': self.fingerprint,
            'expires': self.expires.isoformat() if self.expires else '',
            'watching': self.watching(),
            'check_seconds': self.interval(),
            'reloads': self.reloads,
            'last_error': self.last_error,
        }

    def _serve(self) -> None:
        """Compares the files on the cadence the expiry calls for."""
        while not self._stop.is_set():
            delay = self.interval()
            woken = self._wake.wait(timeout=delay)
            if self._stop.is_set():
                break
            self._wake.clear()
            try:
                self.check(force=woken)
            except Exception as error:  # pylint: disable=broad-except
                # The watcher failing must never take the daemon with it.
                _LOG.exception('certificate watch failed: %s', error)
