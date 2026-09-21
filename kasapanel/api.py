# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The JSON API.

This module holds the routing table and the handlers.  It knows nothing
about sockets: :mod:`kasapanel.httpd` turns an HTTP request into a
:class:`Request`, calls :func:`dispatch`, and turns the :class:`Response`
back into bytes.  That split keeps the handlers easy to call directly
from tests.

Every endpoint under ``/api/`` needs a session cookie, except signing
in.  Requests that change something must also echo the CSRF token that
was handed out at sign in, in the ``X-Kasa-CSRF`` header.
"""

import dataclasses
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

from kasapanel import actions
from kasapanel import auth
from kasapanel import devicestore
from kasapanel import jsonstore
from kasapanel import kasabridge
from kasapanel import schedule as schedule_lib
from kasapanel import snooze as snooze_lib

_LOG = logging.getLogger(__name__)

WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')

MAX_BODY_BYTES = 256 * 1024


@dataclasses.dataclass
class Request:
    """One decoded API request.

    Attributes:
        method: HTTP method, upper case.
        path: Path part of the URL.
        query: Decoded query string parameters.
        body: Decoded JSON body, empty for requests without one.
        cookies: Cookies sent by the browser.
        headers: Header lookup function taking a name and a default.
        remote: Client address.
        user_agent: Client user agent string.
    """

    method: str
    path: str
    query: Dict[str, List[str]] = dataclasses.field(default_factory=dict)
    body: Dict[str, Any] = dataclasses.field(default_factory=dict)
    cookies: Dict[str, str] = dataclasses.field(default_factory=dict)
    headers: Callable[[str, str], str] = lambda name, default='': default
    remote: str = ''
    user_agent: str = ''

    def text(self, name: str, default: str = '') -> str:
        """Reads a string field from the JSON body.

        Args:
            name: Field name.
            default: Value used when the field is missing.

        Returns:
            The field value as a string.
        """
        value = self.body.get(name, default)
        return default if value is None else str(value)

    def number(self, name: str, default: int = 0) -> int:
        """Reads an integer field from the query string or body.

        Args:
            name: Field name.
            default: Value used when the field is missing or invalid.

        Returns:
            The field value as an integer.
        """
        raw = self.query.get(name, [None])[0]
        if raw is None:
            raw = self.body.get(name, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def flag(self, name: str, default: bool = False) -> bool:
        """Reads a boolean field from the JSON body.

        Args:
            name: Field name.
            default: Value used when the field is missing.

        Returns:
            The field value as a boolean.
        """
        value = self.body.get(name, default)
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)


@dataclasses.dataclass
class Response:
    """One API response.

    Attributes:
        status: HTTP status code.
        payload: JSON serialisable body.
        cookies: Ready made ``Set-Cookie`` header values.
    """

    status: int = 200
    payload: Dict[str, Any] = dataclasses.field(default_factory=dict)
    cookies: List[str] = dataclasses.field(default_factory=list)


def error(status: int, message: str, **extra: Any) -> Response:
    """Builds an error response.

    Args:
        status: HTTP status code.
        message: Message shown to the operator.
        **extra: Extra fields merged into the payload.

    Returns:
        The response.
    """
    payload = {'error': message}
    payload.update(extra)
    return Response(status=status, payload=payload)


def session_token(request: 'Request') -> Tuple[str, str]:
    """Finds the session token the browser sent, and how it sent it.

    The cookie is the normal carrier, but a browser that refuses to keep
    it still needs to work.  Browsers drop ``Secure`` cookies from an
    origin whose certificate they do not trust, which is every
    self-signed certificate, so the panel also accepts the token in a
    request header.  A custom header cannot be attached by a cross-site
    form or image, so this carries no CSRF risk of its own.

    Args:
        request: The decoded request.

    Returns:
        The token, and the carrier it arrived in.
    """
    token = request.cookies.get(auth.SESSION_COOKIE, '')
    if token:
        return token, 'cookie'
    sent = request.headers(auth.SESSION_HEADER, '')
    if sent:
        return sent, 'header'
    return '', 'none'


def _explain_401(app: Any, request: 'Request', token: str,
                 carrier: str) -> None:
    """Logs why a request was refused, so a bounce can be diagnosed.

    A user thrown back to the sign in page sees nothing useful, and the
    interesting facts are all on this side: whether the browser sent
    anything at all, whether the token was simply unknown to us, and
    whether the daemon has restarted and lost its sessions.  No secret
    is written to the log; tokens are reported only as present or not.

    Args:
        app: The application.
        request: The refused request.
        token: The token that arrived, if any.
        carrier: Where the token arrived, if it did.
    """
    if token:
        why = 'the token is not one this process issued'
    else:
        why = 'no session cookie and no session header arrived'
    cookie_names = sorted(request.cookies) or ['none']
    forwarded = request.headers('X-Forwarded-For', '')
    _LOG.warning(
        'refused %s %s: %s (carrier=%s, cookies=%s, sessions=%d, '
        'daemon started %s, remote=%s%s, agent=%r)',
        request.method, request.path, why, carrier, ','.join(cookie_names),
        len(app.sessions.active()),
        app.started_at.isoformat(timespec='seconds'),
        request.remote,
        f' via proxy {forwarded}' if forwarded else '',
        request.user_agent[:80])


def _certificate(app: Any) -> Dict[str, Any]:
    """Describes the certificate the server is serving.

    Args:
        app: The application.

    Returns:
        The certificate summary, with what the watcher knows added.
    """
    from kasapanel import certwatch  # pylint: disable=import-outside-toplevel
    summary = certwatch.summarise(app.settings.resolved_certificate())
    watcher = getattr(app, 'certwatch', None)
    if watcher is None:
        summary['watcher'] = {}
        return summary
    status = watcher.status()
    summary['watcher'] = status
    # The watcher knows what is actually loaded, which is what matters
    # if the file has changed since and could not be read.
    if status.get('expires'):
        summary['expires'] = status['expires']
    if status.get('fingerprint'):
        summary['fingerprint'] = status['fingerprint']
    return summary


def _daemon_user(app: Any) -> Dict[str, Any]:
    """Describes the account the daemon is running as.

    Args:
        app: The application.

    Returns:
        The account description, empty when privileges were never
        arranged because the daemon is not running as root.
    """
    described = dict(getattr(app, 'daemon_user', {}) or {})
    described['privileged_helper'] = getattr(app, 'helper', None) is not None
    return described


def _session_cookie(token: str, seconds: int) -> str:
    """Builds the ``Set-Cookie`` value for the session cookie.

    Args:
        token: Session token, or an empty string to clear the cookie.
        seconds: Cookie lifetime; zero clears the cookie.

    Returns:
        A cookie header value.
    """
    parts = [
        f'{auth.SESSION_COOKIE}={token}',
        'Path=/',
        'HttpOnly',
        'Secure',
        # Lax rather than Strict: writes already require the CSRF header,
        # which a cross-site request cannot set, and Strict costs the
        # session on ordinary inbound links.
        'SameSite=Lax',
        f'Max-Age={seconds}',
    ]
    return '; '.join(parts)


def _device_payload(app: Any, device_id: str) -> Dict[str, Any]:
    """Builds the full view of one device.

    Args:
        app: The :class:`kasapanel.app.Application`.
        device_id: Identifier of the device.

    Returns:
        The device view, its schedule and its recent activity.
    """
    record = app.devices.get(device_id)
    document = app.devices.device_document(device_id)
    view = app.device_view(record)
    view['schedule'] = document.get('schedule', '')
    view['schedule_summary'] = schedule_lib.summarise(view['schedule'])
    view['activity'] = app.activity.records(limit=25, device_id=device_id)
    return view


def sign_in(app: Any, request: Request, params: Tuple[str, ...],
            session: Optional[auth.Session]) -> Response:
    """Signs a user in with PAM.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: Unused.

    Returns:
        The session description, plus the session cookie.
    """
    del params, session
    try:
        username = app.authenticator.authenticate(
            request.text('username'), request.text('password'))
    except auth.AuthError as err:
        app.activity.add(f'Sign in refused: {err}', level='warning',
                         source=request.text('username') or 'unknown')
        return error(401, str(err))
    session = app.sessions.create(
        username, remote=request.remote, user_agent=request.user_agent)
    app.activity.add(f'{username} signed in', source=username)
    seconds = int(app.settings.session_minutes) * 60
    return Response(
        status=200,
        payload={
            'username': username,
            'csrf_token': session.csrf_token,
            # The client keeps this and sends it back in the session
            # header, so sign in survives a browser that will not store
            # the cookie.
            'session_token': session.token,
            'groups': auth.user_groups(username),
        },
        cookies=[_session_cookie(session.token, seconds)])


def sign_out(app: Any, request: Request, params: Tuple[str, ...],
             session: Optional[auth.Session]) -> Response:
    """Signs the current user out.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: The session being ended.

    Returns:
        An empty response that clears the session cookie.
    """
    del params
    token, _ = session_token(request)
    if session is not None:
        app.activity.add(f'{session.username} signed out',
                         source=session.username)
    app.sessions.destroy(token)
    return Response(status=200, payload={'signed_out': True},
                    cookies=[_session_cookie('', 0)])


def whoami(app: Any, request: Request, params: Tuple[str, ...],
           session: Optional[auth.Session]) -> Response:
    """Describes the current session.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: The current session, if any.

    Returns:
        Who is signed in, or a flag saying nobody is.
    """
    del params
    if session is None:
        return Response(payload={
            'signed_in': False,
            'pam_available': auth.PAM_AVAILABLE,
            'pam_error': auth.pam_reason(),
        })
    return Response(payload={
        'signed_in': True,
        'username': session.username,
        'csrf_token': session.csrf_token,
        'auth_method': session_token(request)[1],
        'groups': auth.user_groups(session.username),
        'active_sessions': len(app.sessions.active()),
    })


def dashboard(app: Any, request: Request, params: Tuple[str, ...],
              session: Optional[auth.Session]) -> Response:
    """Returns the dashboard payload.

    Args:
        app: The application.
        request: Unused.
        params: Unused.
        session: Unused.

    Returns:
        Every device with its state and schedule summary.
    """
    del request, params, session
    return Response(payload=app.dashboard())


def list_devices(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Lists the device inventory.

    Args:
        app: The application.
        request: Unused.
        params: Unused.
        session: Unused.

    Returns:
        The inventory records with their cached state.
    """
    del request, params, session
    return Response(payload={
        'devices': [app.device_view(record)
                    for record in app.devices.records()],
    })


def add_device(app: Any, request: Request, params: Tuple[str, ...],
               session: Optional[auth.Session]) -> Response:
    """Adds a device by host name or address.

    The host is contacted first so the model and MAC address can be
    stored, but a device that does not answer is still added, because
    an operator often adds a plug before it is powered up.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: The current session.

    Returns:
        The stored device view.
    """
    del params
    host = request.text('host').strip()
    if not host:
        return error(400, 'enter a host name or IP address')
    info: Dict[str, Any] = {}
    warning = ''
    try:
        info = app.bridge.probe(host)
    except kasabridge.BridgeError as err:
        warning = str(err)
    try:
        record = app.devices.add(
            host, alias=request.text('alias').strip(), source='manual',
            info=info)
    except devicestore.DeviceError as err:
        return error(400, str(err))
    if info:
        app.store_state(record, info)
    actor = session.username if session else 'dashboard'
    app.activity.add(f'Added {record.display_name} ({host})', source=actor,
                     device_id=record.device_id,
                     device_name=record.display_name)
    return Response(status=201, payload={
        'device': app.device_view(record),
        'warning': warning,
    })


def get_device(app: Any, request: Request, params: Tuple[str, ...],
               session: Optional[auth.Session]) -> Response:
    """Returns one device with its schedule and activity.

    Args:
        app: The application.
        request: Unused.
        params: Path parameters holding the device identifier.
        session: Unused.

    Returns:
        The device view.
    """
    del request, session
    return Response(payload={'device': _device_payload(app, params[0])})


def update_device(app: Any, request: Request, params: Tuple[str, ...],
                  session: Optional[auth.Session]) -> Response:
    """Updates the editable fields of a device.

    Args:
        app: The application.
        request: The decoded request.
        params: Path parameters holding the device identifier.
        session: The current session.

    Returns:
        The updated device view.
    """
    changes = {name: request.body[name]
               for name in ('alias', 'host', 'enabled', 'notes')
               if name in request.body}
    record = app.devices.update(params[0], changes)
    if 'host' in changes:
        app.bridge.forget(record.host)
    actor = session.username if session else 'dashboard'
    app.activity.add(f'Updated {record.display_name}', source=actor,
                     device_id=record.device_id,
                     device_name=record.display_name)
    return Response(payload={'device': app.device_view(record)})


def delete_device(app: Any, request: Request, params: Tuple[str, ...],
                  session: Optional[auth.Session]) -> Response:
    """Removes a device from the inventory.

    Args:
        app: The application.
        request: Unused.
        params: Path parameters holding the device identifier.
        session: The current session.

    Returns:
        A confirmation payload.
    """
    del request
    record = app.devices.remove(params[0])
    app.bridge.forget(record.host)
    actor = session.username if session else 'dashboard'
    app.activity.add(f'Removed {record.display_name}', source=actor)
    return Response(payload={'removed': record.device_id})


def device_action(app: Any, request: Request, params: Tuple[str, ...],
                  session: Optional[auth.Session]) -> Response:
    """Runs one action against a device.

    Args:
        app: The application.
        request: The decoded request.
        params: Path parameters holding the device identifier.
        session: The current session.

    Returns:
        The result message and the fresh device view.
    """
    action = request.text('action')
    raw_arguments = request.body.get('arguments', [])
    if isinstance(raw_arguments, (str, int, float)):
        raw_arguments = [raw_arguments]
    arguments = [str(item) for item in raw_arguments]
    actor = session.username if session else 'dashboard'
    try:
        result = app.run_action(params[0], action, arguments, actor=actor)
    except actions.ActionError as err:
        return error(400, str(err))
    except kasabridge.BridgeError as err:
        return error(502, str(err))
    return Response(payload={
        'result': result,
        'device': app.device_view(app.devices.get(params[0])),
    })


def refresh_device(app: Any, request: Request, params: Tuple[str, ...],
                   session: Optional[auth.Session]) -> Response:
    """Polls one device immediately.

    Args:
        app: The application.
        request: Unused.
        params: Path parameters holding the device identifier.
        session: Unused.

    Returns:
        The fresh device view.
    """
    del request, session
    app.refresh(params[0])
    return Response(payload={
        'device': app.device_view(app.devices.get(params[0])),
    })


def get_schedule(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Returns the schedule script of a device.

    Args:
        app: The application.
        request: Unused.
        params: Path parameters holding the device identifier.
        session: Unused.

    Returns:
        The script, whether it is enabled, and a run preview.
    """
    del request, session
    device_id = params[0]
    app.devices.get(device_id)
    document = app.devices.device_document(device_id)
    script = document.get('schedule', '')
    return Response(payload={
        'device_id': device_id,
        'schedule': script,
        'schedule_enabled': bool(document.get('schedule_enabled', True)),
        'summary': schedule_lib.summarise(script),
    })


def put_schedule(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Validates and stores the schedule script of a device.

    Args:
        app: The application.
        request: The decoded request.
        params: Path parameters holding the device identifier.
        session: The current session.

    Returns:
        The stored script and a run preview.
    """
    device_id = params[0]
    record = app.devices.get(device_id)
    script = request.text('schedule')
    summary = schedule_lib.summarise(script)
    if not summary['valid']:
        return error(400, 'the schedule has errors', summary=summary)
    app.devices.set_schedule(device_id, script)
    if 'schedule_enabled' in request.body:
        app.devices.set_schedule_enabled(
            device_id, request.flag('schedule_enabled', True))
    document = app.devices.device_document(device_id)
    actor = session.username if session else 'dashboard'
    rule_count = summary['rule_count']
    app.activity.add(
        f'Saved schedule for {record.display_name} '
        f'({rule_count} rule(s))', source=actor,
        device_id=device_id, device_name=record.display_name)
    return Response(payload={
        'device_id': device_id,
        'schedule': script,
        'schedule_enabled': bool(document.get('schedule_enabled', True)),
        'summary': summary,
    })


def check_schedule(app: Any, request: Request, params: Tuple[str, ...],
                   session: Optional[auth.Session]) -> Response:
    """Validates a schedule script without storing it.

    Args:
        app: Unused.
        request: The decoded request.
        params: Unused.
        session: Unused.

    Returns:
        The validation summary.
    """
    del app, params, session
    return Response(payload={
        'summary': schedule_lib.summarise(request.text('schedule')),
    })


def snooze_device(app: Any, request: Request, params: Tuple[str, ...],
                  session: Optional[auth.Session]) -> Response:
    """Snoozes a device's schedule.

    The body carries ``for``: a time span or timestamp in systemd.time
    syntax, such as ``2h`` or ``tomorrow 07:00``.

    Args:
        app: The application.
        request: The decoded request.
        params: Path parameters holding the device identifier.
        session: The current session.

    Returns:
        When the snooze ends, and the fresh device view.
    """
    actor = session.username if session else 'dashboard'
    until = app.snooze(params[0], request.text('for'), actor=actor)
    return Response(payload={
        'snoozed_until': until.isoformat(timespec='seconds'),
        'device': app.device_view(app.devices.get(params[0])),
    })


def unsnooze_device(app: Any, request: Request, params: Tuple[str, ...],
                    session: Optional[auth.Session]) -> Response:
    """Lifts a snooze so the device's schedule runs again.

    Args:
        app: The application.
        request: Unused.
        params: Path parameters holding the device identifier.
        session: The current session.

    Returns:
        Whether a snooze was lifted, and the fresh device view.
    """
    del request
    actor = session.username if session else 'dashboard'
    lifted = app.unsnooze(params[0], actor=actor)
    return Response(payload={
        'lifted': lifted,
        'device': app.device_view(app.devices.get(params[0])),
    })


def check_snooze(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Works out when a snooze would end, without setting one.

    The custom snooze dialog calls this as the operator types, so a
    mistake is explained before anything is saved.

    Args:
        app: Unused.
        request: The decoded request.
        params: Unused.
        session: Unused.

    Returns:
        Whether the text is usable, when it ends, or what is wrong.
    """
    del app, params, session
    try:
        until = snooze_lib.resolve(request.text('for'))
    except snooze_lib.SnoozeError as err:
        return Response(payload={'valid': False, 'error': str(err),
                                 'snoozed_until': ''})
    return Response(payload={
        'valid': True, 'error': '',
        'snoozed_until': until.isoformat(timespec='seconds'),
    })


def scan(app: Any, request: Request, params: Tuple[str, ...],
         session: Optional[auth.Session]) -> Response:
    """Scans the local network for devices.

    Found devices are reported but not added, so the operator chooses
    what belongs in the inventory.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: The current session.

    Returns:
        One entry per device found, flagged as known or new.
    """
    del params
    seconds = request.number('seconds', app.settings.discovery_seconds)
    actor = session.username if session else 'dashboard'
    try:
        found = app.bridge.discover(seconds=seconds)
    except kasabridge.BridgeError as err:
        return error(502, str(err))
    results = []
    for entry in found:
        host = entry.get('host', '')
        known = app.devices.find_by_host(host)
        results.append({
            'host': host,
            'alias': entry.get('alias', ''),
            'model': entry.get('model', ''),
            'device_type': entry.get('device_type', ''),
            'mac': entry.get('mac', ''),
            'is_on': entry.get('is_on', False),
            'known': known is not None,
            'device_id': known.device_id if known else '',
        })
    results.sort(key=lambda item: (item['known'], item['host']))
    app.activity.add(f'Network scan found {len(results)} device(s)',
                     source=actor)
    return Response(payload={'found': results, 'seconds': seconds})


def get_settings(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Returns the daemon settings with secrets removed.

    Args:
        app: The application.
        request: Unused.
        params: Unused.
        session: Unused.

    Returns:
        The settings and the paths they resolve to.
    """
    del request, params, session
    return Response(payload={
        'settings': app.settings.redacted(),
        'daemon_user': _daemon_user(app),
        'certificate': _certificate(app),
        'file_checks': list(getattr(app, 'file_checks', [])),
        'paths': {
            'config': app.config_store.path,
            'devices': app.settings.devices_file,
            'device_dir': app.settings.device_dir,
            'certificate': app.settings.resolved_certificate(),
            'key': app.settings.resolved_key(),
        },
        'restart_required_fields': [
            'listen_host', 'listen_port', 'tls_certificate', 'tls_key',
            'state_dir', 'daemon_user',
        ],
    })


def put_settings(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Saves changed settings.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: The current session.

    Returns:
        The settings now in effect.
    """
    del params
    changes = request.body.get('settings', request.body)
    if not isinstance(changes, dict):
        return error(400, 'settings must be sent as an object')
    changes.pop('version', None)
    app.apply_settings(changes)
    actor = session.username if session else 'dashboard'
    app.activity.add('Settings saved', source=actor)
    return Response(payload={'settings': app.settings.redacted()})


def get_activity(app: Any, request: Request, params: Tuple[str, ...],
                 session: Optional[auth.Session]) -> Response:
    """Returns recent activity records.

    Args:
        app: The application.
        request: The decoded request.
        params: Unused.
        session: Unused.

    Returns:
        The most recent records, newest first.
    """
    del params, session
    device_id = request.query.get('device', [''])[0]
    limit = request.number('limit', 100)
    return Response(payload={
        'activity': app.activity.records(limit=limit, device_id=device_id),
    })


def list_sessions(app: Any, request: Request, params: Tuple[str, ...],
                  session: Optional[auth.Session]) -> Response:
    """Lists the users currently signed in.

    Args:
        app: The application.
        request: Unused.
        params: Unused.
        session: Unused.

    Returns:
        One entry per active session.
    """
    del request, params, session
    return Response(payload={'sessions': app.sessions.active()})


def reference(app: Any, request: Request, params: Tuple[str, ...],
              session: Optional[auth.Session]) -> Response:
    """Returns the schedule language reference for the help panel.

    Args:
        app: Unused.
        request: Unused.
        params: Unused.
        session: Unused.

    Returns:
        The action vocabulary and the schedule keywords.
    """
    del app, request, params, session
    from kasapanel import cron  # pylint: disable=import-outside-toplevel
    return Response(payload={
        'actions': actions.catalogue(),
        'aliases': actions.ALIASES,
        'keywords': [
            {'name': name, 'expands_to': value}
            for name, value in sorted(cron.MACROS.items())
        ],
        'example': schedule_lib.EXAMPLE_SCRIPT,
        'snooze': snooze_lib.reference(),
    })


Handler = Callable[[Any, Request, Tuple[str, ...], Optional[auth.Session]],
                   Response]

ROUTES: List[Tuple[str, Pattern[str], Handler, bool]] = [
    ('POST', re.compile(r'^/api/session$'), sign_in, False),
    ('DELETE', re.compile(r'^/api/session$'), sign_out, True),
    ('GET', re.compile(r'^/api/session$'), whoami, False),
    ('GET', re.compile(r'^/api/dashboard$'), dashboard, True),
    ('GET', re.compile(r'^/api/devices$'), list_devices, True),
    ('POST', re.compile(r'^/api/devices$'), add_device, True),
    ('GET', re.compile(r'^/api/devices/([\w.-]+)$'), get_device, True),
    ('PATCH', re.compile(r'^/api/devices/([\w.-]+)$'), update_device, True),
    ('DELETE', re.compile(r'^/api/devices/([\w.-]+)$'), delete_device, True),
    ('POST', re.compile(r'^/api/devices/([\w.-]+)/action$'),
     device_action, True),
    ('POST', re.compile(r'^/api/devices/([\w.-]+)/refresh$'),
     refresh_device, True),
    ('GET', re.compile(r'^/api/devices/([\w.-]+)/schedule$'),
     get_schedule, True),
    ('PUT', re.compile(r'^/api/devices/([\w.-]+)/schedule$'),
     put_schedule, True),
    ('POST', re.compile(r'^/api/devices/([\w.-]+)/snooze$'),
     snooze_device, True),
    ('DELETE', re.compile(r'^/api/devices/([\w.-]+)/snooze$'),
     unsnooze_device, True),
    ('POST', re.compile(r'^/api/schedule/check$'), check_schedule, True),
    ('POST', re.compile(r'^/api/snooze/check$'), check_snooze, True),
    ('POST', re.compile(r'^/api/scan$'), scan, True),
    ('GET', re.compile(r'^/api/settings$'), get_settings, True),
    ('PUT', re.compile(r'^/api/settings$'), put_settings, True),
    ('GET', re.compile(r'^/api/activity$'), get_activity, True),
    ('GET', re.compile(r'^/api/sessions$'), list_sessions, True),
    ('GET', re.compile(r'^/api/reference$'), reference, True),
]


def _match(request: Request) -> Tuple[Optional[Handler],
                                      Tuple[str, ...], bool, bool]:
    """Finds the handler for a request.

    Args:
        request: The decoded request.

    Returns:
        A tuple of handler, path parameters, whether a session is
        required, and whether the path exists under another method.
    """
    path_exists = False
    for method, pattern, handler, needs_auth in ROUTES:
        match = pattern.match(request.path)
        if match is None:
            continue
        path_exists = True
        if method == request.method:
            return handler, match.groups(), needs_auth, path_exists
    return None, (), True, path_exists


def dispatch(app: Any, request: Request) -> Response:
    """Routes a request to its handler and turns errors into responses.

    Args:
        app: The :class:`kasapanel.app.Application`.
        request: The decoded request.

    Returns:
        The response to send.
    """
    handler, params, needs_auth, path_exists = _match(request)
    if handler is None:
        if path_exists:
            return error(405, f'{request.method} is not allowed here')
        return error(404, 'no such endpoint')
    token, carrier = session_token(request)
    session = app.sessions.get(token)
    if needs_auth and session is None:
        _explain_401(app, request, token, carrier)
        if token:
            return error(401, 'that session has expired; sign in again')
        return error(401, 'sign in to continue')
    if request.method in WRITE_METHODS and session is not None:
        sent = request.headers(auth.CSRF_HEADER, '')
        if sent != session.csrf_token:
            return error(403, 'the session token did not match; reload the '
                              'page and sign in again')
    try:
        return handler(app, request, params, session)
    except devicestore.DeviceError as err:
        return error(404, str(err))
    except schedule_lib.ScheduleError as err:
        return error(400, str(err))
    except snooze_lib.SnoozeError as err:
        return error(400, str(err))
    except actions.ActionError as err:
        return error(400, str(err))
    except kasabridge.BridgeError as err:
        return error(502, str(err))
    except auth.AuthError as err:
        return error(401, str(err))
    except jsonstore.StoreError as err:
        _LOG.error('storage failure on %s: %s', request.path, err)
        return error(500, f'could not save to disk: {err}')
