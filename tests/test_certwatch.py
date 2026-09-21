# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.logsink` and :mod:`kasapanel.certwatch`."""

import datetime
import http.server
import logging
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from typing import Any

from kasapanel import activity
from kasapanel import certwatch
from kasapanel import logsink

UTC = datetime.timezone.utc


def make_certificate(directory: str, name: str, common_name: str,
                     days: int = 30) -> str:
    """Writes a self-signed certificate and key.

    Args:
        directory: Where to write them.
        name: Base file name.
        common_name: Subject common name, to tell them apart.
        days: How long the certificate should be valid.

    Returns:
        The certificate path; the key sits beside it with a .key suffix.
    """
    certificate = os.path.join(directory, f'{name}.crt')
    key = os.path.join(directory, f'{name}.key')
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
         '-keyout', key, '-out', certificate, '-days', str(days),
         '-subj', f'/CN={common_name}'],
        check=True, capture_output=True)
    return certificate


class Settings:
    """The two settings fields the watcher reads."""

    def __init__(self, certificate: str, key: str):
        """Stores the paths.

        Args:
            certificate: Certificate file.
            key: Private key file.
        """
        self.certificate = certificate
        self.key = key

    def resolved_certificate(self) -> str:
        """Returns the certificate path."""
        return self.certificate

    def resolved_key(self) -> str:
        """Returns the key path."""
        return self.key


class LogSinkTest(unittest.TestCase):
    """The handler that reopens the log file before every line."""

    def setUp(self):
        """Builds a logger writing into a scratch directory."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, 'logs', 'kasapanel.log')
        self.handler = logsink.ReopeningFileHandler(self.path)
        self.handler.setFormatter(logging.Formatter('%(message)s'))
        self.log = logging.getLogger(f'test.{id(self)}')
        self.log.handlers = [self.handler]
        self.log.propagate = False
        self.log.setLevel(logging.INFO)
        self.addCleanup(self.handler.close)

    def read(self, path: str = '') -> str:
        """Reads a log file.

        Args:
            path: File to read; defaults to the configured log.

        Returns:
            Its contents.
        """
        with open(path or self.path, encoding='utf-8') as handle:
            return handle.read()

    def test_writes_and_creates_the_directory(self):
        """The parent directory is made if it is not there."""
        self.log.info('first line')
        self.assertIn('first line', self.read())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_follows_a_rotation(self):
        """After logrotate renames the file, writing starts a new one."""
        self.log.info('before')
        os.rename(self.path, self.path + '.1')
        self.log.info('after')
        self.assertIn('before', self.read(self.path + '.1'))
        self.assertIn('after', self.read())
        self.assertNotIn('after', self.read(self.path + '.1'))

    def test_survives_deletion(self):
        """A deleted log file is recreated on the next line."""
        self.log.info('before')
        os.unlink(self.path)
        self.log.info('after')
        self.assertEqual(self.read().strip(), 'after')

    def test_survives_the_directory_going_away(self):
        """Losing the whole directory is recoverable too."""
        self.log.info('before')
        shutil.rmtree(os.path.dirname(self.path))
        self.log.info('after')
        self.assertEqual(self.read().strip(), 'after')

    def test_checks_before_every_line(self):
        """Each record is preceded by its own check, not one at start."""
        self.log.info('one')
        os.rename(self.path, self.path + '.1')
        self.log.info('two')
        os.rename(self.path, self.path + '.2')
        self.log.info('three')
        self.assertEqual(self.read(self.path + '.1').strip(), 'one')
        self.assertEqual(self.read(self.path + '.2').strip(), 'two')
        self.assertEqual(self.read().strip(), 'three')

    def test_an_unwritable_file_does_not_raise(self):
        """A failure falls back to stderr rather than propagating."""
        self.handler.close()
        handler = logsink.ReopeningFileHandler(self.path)
        handler.setFormatter(logging.Formatter('%(message)s'))
        handler.path = os.path.join(self.tmp, 'no', 'such\x00path')
        record = logging.LogRecord('t', logging.INFO, __file__, 1,
                                   'still alive', None, None)
        handler.emit(record)  # Must not raise.
        handler.close()


class CertificateReadingTest(unittest.TestCase):
    """Reading fingerprints and expiry dates."""

    def setUp(self):
        """Creates a certificate to inspect."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.certificate = make_certificate(self.tmp, 'a', 'first.example')

    def test_describes_a_certificate(self):
        """The fingerprint is hex and the expiry is in the future."""
        fingerprint, expires = certwatch.describe(self.certificate)
        self.assertEqual(len(fingerprint), 64)
        self.assertGreater(expires, datetime.datetime.now(UTC))

    def test_two_certificates_differ(self):
        """Different certificates have different fingerprints."""
        other = make_certificate(self.tmp, 'b', 'second.example')
        self.assertNotEqual(certwatch.describe(self.certificate)[0],
                            certwatch.describe(other)[0])

    def test_missing_and_empty_files_are_errors(self):
        """Unreadable files raise rather than returning nonsense."""
        with self.assertRaises(certwatch.CertificateError):
            certwatch.describe(os.path.join(self.tmp, 'absent.crt'))
        empty = os.path.join(self.tmp, 'empty.crt')
        with open(empty, 'w', encoding='utf-8') as handle:
            handle.write('')
        with self.assertRaises(certwatch.CertificateError):
            certwatch.describe(empty)

    def test_the_openssl_reader_agrees(self):
        """The fallback path returns the same answer as cryptography."""
        # pylint: disable=protected-access
        expected = certwatch.describe(self.certificate)
        found = certwatch._describe_with_openssl(self.certificate)
        self.assertEqual(found[0], expected[0])
        self.assertEqual(found[1], expected[1])


class CadenceTest(unittest.TestCase):
    """When the watcher decides to look."""

    def build(self, expires_in: datetime.timedelta) -> Any:
        """Builds a watcher whose live certificate expires at a time.

        Args:
            expires_in: How far from now the certificate expires.

        Returns:
            The watcher.
        """
        now = datetime.datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        watcher = certwatch.CertificateWatcher(
            Settings('/nowhere.crt', '/nowhere.key'), ssl.SSLContext(
                ssl.PROTOCOL_TLS_SERVER), clock=lambda: now)
        watcher.expires = now + expires_in
        return watcher

    def test_quiet_while_the_certificate_is_healthy(self):
        """Well before expiry nothing is read."""
        watcher = self.build(datetime.timedelta(days=30))
        self.assertFalse(watcher.watching())
        self.assertEqual(watcher.interval(), certwatch.MAX_IDLE_SECONDS)

    def test_every_fifteen_minutes_inside_the_window(self):
        """The last 48 hours are checked four times an hour."""
        for hours in (47, 24, 1):
            watcher = self.build(datetime.timedelta(hours=hours))
            self.assertTrue(watcher.watching(), f'{hours}h before expiry')
            self.assertEqual(watcher.interval(),
                             certwatch.NEAR_EXPIRY_SECONDS)

    def test_the_window_opens_at_exactly_48_hours(self):
        """The boundary belongs to the watching side."""
        self.assertTrue(self.build(datetime.timedelta(hours=48)).watching())
        self.assertFalse(
            self.build(datetime.timedelta(hours=48, seconds=1)).watching())

    def test_every_minute_once_expired(self):
        """After expiry the daemon is broken, so it looks far more often."""
        for delta in (datetime.timedelta(seconds=-1),
                      datetime.timedelta(days=-3)):
            watcher = self.build(delta)
            self.assertTrue(watcher.watching())
            self.assertEqual(watcher.interval(),
                             certwatch.AFTER_EXPIRY_SECONDS)

    def test_an_unreadable_expiry_still_gets_checked(self):
        """Not knowing when it expires is not a reason to stop looking."""
        watcher = self.build(datetime.timedelta(days=30))
        watcher.expires = None
        self.assertTrue(watcher.watching())
        self.assertEqual(watcher.interval(), certwatch.NEAR_EXPIRY_SECONDS)


class ReloadTest(unittest.TestCase):
    """Swapping the certificate on a live server."""

    def setUp(self):
        """Starts an HTTPS server holding the first certificate."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.first = make_certificate(self.tmp, 'first', 'first.example')
        self.second = make_certificate(self.tmp, 'second', 'second.example')
        # The daemon watches one path; renewal replaces what is at it.
        self.live = os.path.join(self.tmp, 'panel.crt')
        self.live_key = os.path.join(self.tmp, 'panel.key')
        shutil.copy(self.first, self.live)
        shutil.copy(self.first.replace('.crt', '.key'), self.live_key)

        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(self.live, self.live_key)

        class Handler(http.server.BaseHTTPRequestHandler):
            """Answers anything with a short body."""

            def do_GET(self):  # pylint: disable=invalid-name
                """Answers a GET."""
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

            def log_message(self, *args, **kwargs):
                """Stays quiet during tests."""

        self.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.server.socket = self.context.wrap_socket(
            self.server.socket, server_side=True)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.05},
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        self.activity = activity.ActivityLog()
        self.watcher = certwatch.CertificateWatcher(
            Settings(self.live, self.live_key), self.context, self.activity)
        self.watcher.adopt(self.live)

    def served_fingerprint(self) -> str:
        """Connects and reports the certificate the server presented.

        Returns:
            The SHA-256 fingerprint as lower case hex.
        """
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        with socket.create_connection(('127.0.0.1', self.port)) as raw:
            with client.wrap_socket(raw) as tls:
                der = tls.getpeercert(binary_form=True)
        import hashlib  # pylint: disable=import-outside-toplevel
        return hashlib.sha256(der).hexdigest()

    def renew(self) -> None:
        """Replaces the files at the watched path, as certbot would."""
        shutil.copy(self.second, self.live)
        shutil.copy(self.second.replace('.crt', '.key'), self.live_key)

    def test_serves_the_first_certificate(self):
        """The fixture is wired up the way the test assumes."""
        self.assertEqual(self.served_fingerprint(), self.watcher.fingerprint)

    def test_nothing_happens_when_the_file_is_unchanged(self):
        """An identical file is not a reload."""
        self.assertFalse(self.watcher.check(force=True))
        self.assertEqual(self.watcher.reloads, 0)

    def test_a_renewed_certificate_is_served_without_restarting(self):
        """The point of the whole module."""
        before = self.served_fingerprint()
        self.renew()
        self.assertTrue(self.watcher.check(force=True))
        after = self.served_fingerprint()
        self.assertNotEqual(before, after)
        self.assertEqual(after, self.watcher.fingerprint)
        self.assertEqual(self.watcher.reloads, 1)
        self.assertEqual(certwatch.describe(self.second)[0], after)

    def test_open_connections_are_not_disturbed(self):
        """A request in flight survives the swap."""
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(('127.0.0.1', self.port))
        tls = client.wrap_socket(raw)
        self.addCleanup(tls.close)

        self.renew()
        self.assertTrue(self.watcher.check(force=True))

        tls.sendall(b'GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        answer = tls.recv(1024)
        self.assertIn(b'200 OK', answer)

    def test_outside_the_window_the_file_is_not_read(self):
        """A healthy certificate is left alone until the window opens."""
        self.renew()
        self.assertFalse(self.watcher.check())  # 30 days of validity left
        self.assertEqual(self.watcher.reloads, 0)

    def test_a_broken_replacement_keeps_the_old_certificate(self):
        """A mismatched key must not take the server down."""
        good = self.watcher.fingerprint
        shutil.copy(self.second, self.live)  # certificate only, wrong key
        self.assertFalse(self.watcher.check(force=True))
        self.assertEqual(self.watcher.fingerprint, good)
        self.assertEqual(self.served_fingerprint(), good)
        self.assertIn('not a usable pair', self.watcher.last_error)
        self.assertEqual(
            self.activity.records()[0]['level'], 'error')

    def test_a_half_written_file_is_tolerated(self):
        """A truncated file is a "try again later", not a crash."""
        with open(self.live, 'w', encoding='utf-8') as handle:
            handle.write('-----BEGIN CERTIFICATE-----\ntrunc')
        self.assertFalse(self.watcher.check(force=True))
        self.assertEqual(self.served_fingerprint(), self.watcher.fingerprint)

    def test_a_reload_is_recorded_in_the_activity_log(self):
        """Operators see the swap in the panel, not only in the log."""
        self.renew()
        self.watcher.check(force=True)
        newest = self.activity.records()[0]
        self.assertEqual(newest['source'], 'tls')
        self.assertIn('new TLS certificate', newest['message'])

    def test_the_thread_starts_and_stops(self):
        """The watching thread is well behaved."""
        self.watcher.start()
        self.watcher.check_now()
        self.watcher.stop(timeout=5)
        self.assertIsNone(self.watcher._thread)  # pylint: disable=W0212


if __name__ == '__main__':
    unittest.main()
