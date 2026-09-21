# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The threaded HTTPS server.

One thread per connection, which is what lets several people use the
panel at once.  Threads are daemon threads so a shutdown never waits for
an idle keep-alive connection.

The server only speaks TLS.  Certificates are read from disk; see
:mod:`kasapanel.certs` for generating a self-signed pair when no proper
certificate is available.
"""

import http.cookies
import http.server
import json
import logging
import mimetypes
import os
import posixpath
import socket
import socketserver
import ssl
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from kasapanel import api
from kasapanel import metrics

_LOG = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'static')

INDEX_FILE = 'index.html'

SECURITY_HEADERS = (
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'DENY'),
    ('Referrer-Policy', 'no-referrer'),
    ('Cross-Origin-Opener-Policy', 'same-origin'),
    ('Content-Security-Policy',
     "default-src 'none'; script-src 'self'; style-src 'self'; "
     "img-src 'self' data:; connect-src 'self'; form-action 'none'; "
     "base-uri 'none'; frame-ancestors 'none'"),
)

CACHE_CONTROL_STATIC = 'no-cache'
CACHE_CONTROL_API = 'no-store'


class PanelServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """A threaded HTTPS server that carries the application with it."""

    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET

    def __init__(self, address: Tuple[str, int], handler: Any,
                 application: Any, context: ssl.SSLContext):
        """Initialises and binds the server.

        Args:
            address: Host and port to bind.
            handler: Request handler class.
            application: The :class:`kasapanel.app.Application`.
            context: The TLS context used for every connection.
        """
        self.application = application
        self.tls_context = context
        super().__init__(address, handler)
        self.socket = context.wrap_socket(self.socket, server_side=True)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Logs a connection error instead of printing a traceback.

        Args:
            request: The failing request object.
            client_address: Address of the client.
        """
        del request
        _LOG.debug('connection from %s failed', client_address,
                   exc_info=True)


class PanelHandler(http.server.BaseHTTPRequestHandler):
    """Serves the dashboard files and the JSON API."""

    server_version = 'KasaPanel'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, format: str, *args: Any) -> None:
        """Sends request logs to the logging module.

        Args:
            format: Printf style format string.
            *args: Format arguments.
        """
        # pylint: disable=redefined-builtin
        _LOG.debug('%s %s', self.address_string(), format % args)

    def log_error(self, format: str, *args: Any) -> None:
        """Sends handler errors to the logging module.

        Args:
            format: Printf style format string.
            *args: Format arguments.
        """
        # pylint: disable=redefined-builtin
        _LOG.warning('%s %s', self.address_string(), format % args)

    @property
    def application(self) -> Any:
        """Returns the application carried by the server."""
        return self.server.application

    def do_GET(self) -> None:
        """Handles a GET request."""
        # pylint: disable=invalid-name
        self._handle('GET')

    def do_HEAD(self) -> None:
        """Handles a HEAD request."""
        # pylint: disable=invalid-name
        self._handle('HEAD')

    def do_POST(self) -> None:
        """Handles a POST request."""
        # pylint: disable=invalid-name
        self._handle('POST')

    def do_PUT(self) -> None:
        """Handles a PUT request."""
        # pylint: disable=invalid-name
        self._handle('PUT')

    def do_PATCH(self) -> None:
        """Handles a PATCH request."""
        # pylint: disable=invalid-name
        self._handle('PATCH')

    def do_DELETE(self) -> None:
        """Handles a DELETE request."""
        # pylint: disable=invalid-name
        self._handle('DELETE')

    def _handle(self, method: str) -> None:
        """Routes one request to the API or to a static file.

        Args:
            method: HTTP method, upper case.
        """
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path == '/healthz':
                self._send_bytes(200, b'ok\n', 'text/plain; charset=utf-8',
                                 method == 'HEAD')
            elif path == '/metrics':
                self._serve_metrics(method)
            elif path.startswith('/api/'):
                self._serve_api(method, path, parsed.query)
            elif method in ('GET', 'HEAD'):
                self._serve_static(path, method == 'HEAD')
            else:
                self._send_json(405, {'error': f'{method} is not allowed'})
        except (BrokenPipeError, ConnectionResetError):
            _LOG.debug('client %s went away', self.address_string())
        except Exception as err:  # pylint: disable=broad-except
            _LOG.exception('unhandled error on %s %s: %s', method, path, err)
            self._send_json(500, {'error': 'the daemon hit an internal error'})

    def _read_body(self) -> Dict[str, Any]:
        """Reads and decodes a JSON request body.

        Returns:
            The decoded object, or an empty dictionary when there is no
            body.

        Raises:
            ValueError: If the body is too large or is not valid JSON.
        """
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError as err:
            raise ValueError('the Content-Length header is not a number'
                             ) from err
        if length <= 0:
            return {}
        if length > api.MAX_BODY_BYTES:
            raise ValueError('the request body is too large')
        raw = self.rfile.read(length)
        try:
            document = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ValueError('the request body is not valid JSON') from err
        if not isinstance(document, dict):
            raise ValueError('the request body must be a JSON object')
        return document

    def _cookies(self) -> Dict[str, str]:
        """Returns the cookies sent with the request."""
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:  # pragma: no cover - defensive.
            return {}
        return {name: morsel.value for name, morsel in jar.items()}

    def _serve_metrics(self, method: str) -> None:
        """Serves the Prometheus endpoint, without polling anything.

        Args:
            method: The HTTP method used.
        """
        application = self.server.application
        if not getattr(application.settings, 'metrics_enabled', True):
            self._send_json(404, {'error': 'metrics are disabled'})
            return
        if method not in ('GET', 'HEAD'):
            self._send_json(405, {'error': f'{method} is not allowed'})
            return
        body = metrics.render(application).encode('utf-8')
        self._send_bytes(200, body, metrics.CONTENT_TYPE, method == 'HEAD')

    def _serve_api(self, method: str, path: str, query: str) -> None:
        """Builds an API request, dispatches it and sends the answer.

        Args:
            method: HTTP method, upper case.
            path: Decoded path.
            query: Raw query string.
        """
        try:
            body = self._read_body()
        except ValueError as err:
            self._send_json(400, {'error': str(err)})
            return
        request = api.Request(
            method=method,
            path=path,
            query=urllib.parse.parse_qs(query),
            body=body,
            cookies=self._cookies(),
            headers=lambda name, default='': self.headers.get(name, default),
            remote=self.address_string(),
            user_agent=self.headers.get('User-Agent', ''))
        response = api.dispatch(self.application, request)
        self._send_json(response.status, response.payload, response.cookies)

    def _serve_static(self, path: str, head_only: bool) -> None:
        """Serves a file from the bundled static directory.

        Args:
            path: Decoded request path.
            head_only: True to send headers without a body.
        """
        target = self._resolve_static(path)
        if target is None:
            self._send_json(404, {'error': 'no such page'})
            return
        try:
            with open(target, 'rb') as handle:
                payload = handle.read()
        except OSError:
            self._send_json(404, {'error': 'no such page'})
            return
        guessed, _ = mimetypes.guess_type(target)
        self._send_bytes(200, payload, guessed or 'application/octet-stream',
                         head_only)

    @staticmethod
    def _resolve_static(path: str) -> Optional[str]:
        """Maps a request path to a file inside the static directory.

        Args:
            path: Decoded request path.

        Returns:
            An absolute path inside the static directory, or None when
            the path escapes it or names nothing.
        """
        if path in ('', '/'):
            path = '/' + INDEX_FILE
        clean = posixpath.normpath(path).lstrip('/')
        if not clean or clean.startswith('..'):
            return None
        target = os.path.normpath(os.path.join(STATIC_DIR, clean))
        if not target.startswith(STATIC_DIR + os.sep):
            return None
        if not os.path.isfile(target):
            return None
        return target

    def _send_bytes(self, status: int, payload: bytes, content_type: str,
                    head_only: bool = False,
                    cookies: Optional[list] = None,
                    cache: str = CACHE_CONTROL_STATIC) -> None:
        """Writes a complete response.

        Args:
            status: HTTP status code.
            payload: Response body.
            content_type: Value of the Content-Type header.
            head_only: True to send headers without the body.
            cookies: Ready made Set-Cookie header values.
            cache: Value of the Cache-Control header.
        """
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', cache)
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        for cookie in cookies or []:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def _send_json(self, status: int, payload: Dict[str, Any],
                   cookies: Optional[list] = None) -> None:
        """Writes a JSON response.

        Args:
            status: HTTP status code.
            payload: JSON serialisable body.
            cookies: Ready made Set-Cookie header values.
        """
        encoded = json.dumps(payload, default=str).encode('utf-8')
        self._send_bytes(status, encoded, 'application/json; charset=utf-8',
                         head_only=False, cookies=cookies,
                         cache=CACHE_CONTROL_API)


def build_tls_context(certificate: str, key: str) -> ssl.SSLContext:
    """Builds the TLS context used by the server.

    Args:
        certificate: Path of the PEM certificate.
        key: Path of the PEM private key.

    Returns:
        A server side TLS context that requires TLS 1.2 or better.

    Raises:
        OSError: If the certificate or key cannot be read.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certificate, keyfile=key)
    return context


def create_server(application: Any) -> PanelServer:
    """Builds a bound, ready to serve HTTPS server.

    Args:
        application: The :class:`kasapanel.app.Application`.

    Returns:
        The server, not yet serving.

    Raises:
        OSError: If the TLS material is unreadable or the port is busy.
    """
    settings = application.settings
    context = build_tls_context(settings.resolved_certificate(),
                                settings.resolved_key())
    server = PanelServer(
        (settings.listen_host, int(settings.listen_port)),
        PanelHandler, application, context)
    _LOG.info('listening on https://%s:%d/',
              settings.listen_host, settings.listen_port)
    return server
