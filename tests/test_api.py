# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.api` using a stand-in device bridge."""

import os
import tempfile
import unittest
from typing import Any, Dict, List
from unittest import mock

from kasapanel import api
from kasapanel import app as app_lib
from kasapanel import auth
from kasapanel import jsonstore
from kasapanel import kasabridge


class FakeBridge:
    """A device bridge that answers from memory instead of the network."""

    def __init__(self):
        """Sets up one reachable device and an empty command log."""
        self.states: Dict[str, Dict[str, Any]] = {
            '10.0.0.5': {'reachable': True, 'is_on': False, 'alias': 'Lamp',
                         'model': 'HS100', 'mac': 'aa:bb:cc:00:11:22',
                         'device_type': 'plug', 'host': '10.0.0.5'},
        }
        self.commands: List[str] = []
        self.forgotten: List[str] = []

    def update_settings(self, settings: Any) -> None:
        """Accepts new settings and ignores them.

        Args:
            settings: Unused.
        """
        del settings

    def probe(self, host: str) -> Dict[str, Any]:
        """Returns the canned state of a host.

        Args:
            host: Host name or address.

        Returns:
            The state snapshot.

        Raises:
            kasabridge.BridgeError: If the host is not known.
        """
        if host not in self.states:
            raise kasabridge.BridgeError(f'{host}: no route to device')
        return dict(self.states[host])

    def snapshot(self, record: Any) -> Dict[str, Any]:
        """Returns the canned state of a device record.

        Args:
            record: The inventory record.

        Returns:
            The state snapshot, reachable or not.
        """
        try:
            return self.probe(record.host)
        except kasabridge.BridgeError as err:
            return {'reachable': False, 'error': str(err), 'is_on': False}

    def run_action(self, record: Any, action: str,
                   arguments: List[str]) -> str:
        """Applies an action to the canned state.

        Args:
            record: The inventory record.
            action: Action keyword.
            arguments: Raw arguments.

        Returns:
            A short report.

        Raises:
            kasabridge.BridgeError: If the device is unreachable.
        """
        state = self.states.get(record.host)
        if state is None:
            raise kasabridge.BridgeError(f'{record.host}: no route to device')
        self.commands.append(' '.join([action] + list(arguments)))
        if action == 'on':
            state['is_on'] = True
        elif action == 'off':
            state['is_on'] = False
        elif action == 'toggle':
            state['is_on'] = not state['is_on']
        return f'{action} done'

    def discover(self, seconds: int = 0) -> List[Dict[str, Any]]:
        """Returns the canned devices as scan results.

        Args:
            seconds: Unused.

        Returns:
            One entry per known device.
        """
        del seconds
        return [dict(state) for state in self.states.values()]

    def forget(self, host: str) -> None:
        """Records that a cached connection was dropped.

        Args:
            host: Host name or address.
        """
        self.forgotten.append(host)


class ApiTest(unittest.TestCase):
    """Routing, authentication and the handlers."""

    def setUp(self):
        """Builds an application in a scratch directory."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_path = os.path.join(self.tmp.name, 'config.json')
        jsonstore.write_json(config_path, {
            'state_dir': os.path.join(self.tmp.name, 'state'),
            'log_level': 'CRITICAL',
        })
        self.app = app_lib.Application(config_path)
        self.app.load()
        self.app.bridge = FakeBridge()
        self.session = self.app.sessions.create('tester', remote='10.0.0.9')

    def request(self, method: str, path: str,
                body: Any = None, signed_in: bool = True,
                csrf: bool = True, carrier: str = 'cookie') -> api.Response:
        """Builds and dispatches one API request.

        Args:
            method: HTTP method.
            path: Request path, query string included.
            body: JSON body, if any.
            signed_in: Whether to send the session cookie.
            csrf: Whether to send a valid CSRF header.
            carrier: Whether to send the session as a cookie or in the
                session header.

        Returns:
            The response.
        """
        query = ''
        if '?' in path:
            path, _, query = path.partition('?')
        cookies = ({auth.SESSION_COOKIE: self.session.token}
                   if signed_in and carrier == 'cookie' else {})
        token = self.session.csrf_token if csrf else 'wrong'
        headers = {auth.CSRF_HEADER: token}
        if signed_in and carrier == 'header':
            headers[auth.SESSION_HEADER] = self.session.token
        request = api.Request(
            method=method,
            path=path,
            query={key: [value] for key, value in
                   (item.split('=', 1) for item in query.split('&') if item)},
            body=body or {},
            cookies=cookies,
            headers=lambda name, default='': headers.get(name, default),
            remote='10.0.0.9')
        return api.dispatch(self.app, request)

    def add_device(self, host: str = '10.0.0.5') -> str:
        """Adds a device through the API.

        Args:
            host: Host to add.

        Returns:
            The identifier of the new device.
        """
        response = self.request('POST', '/api/devices', {'host': host})
        self.assertEqual(response.status, 201)
        return response.payload['device']['device_id']

    def test_session_header_works_without_a_cookie(self):
        """A browser that will not keep the cookie can still work."""
        response = self.request('GET', '/api/dashboard', carrier='header')
        self.assertEqual(response.status, 200)
        whoami = self.request('GET', '/api/session', carrier='header')
        self.assertEqual(whoami.payload['auth_method'], 'header')
        self.assertTrue(whoami.payload['signed_in'])

    def test_writes_work_through_the_session_header(self):
        """The header carries writes too, CSRF check included."""
        added = self.request('POST', '/api/devices', {'host': '10.0.0.5'},
                             carrier='header')
        self.assertEqual(added.status, 201)
        refused = self.request('POST', '/api/devices', {'host': '10.0.0.5'},
                               carrier='header', csrf=False)
        self.assertEqual(refused.status, 403)

    def test_sign_in_returns_a_session_token(self):
        """The payload carries the token the header needs."""
        self.app.authenticator = mock.Mock()
        self.app.authenticator.authenticate.return_value = 'tester'
        response = self.request('POST', '/api/session',
                                {'username': 'tester', 'password': 'x'},
                                signed_in=False)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.payload['session_token'])
        self.assertIsNotNone(
            self.app.sessions.get(response.payload['session_token']))

    def test_cookie_is_lax_not_strict(self):
        """The cookie survives an ordinary inbound link."""
        self.app.authenticator = mock.Mock()
        self.app.authenticator.authenticate.return_value = 'tester'
        response = self.request('POST', '/api/session',
                                {'username': 'tester', 'password': 'x'},
                                signed_in=False)
        cookie = response.cookies[0]
        self.assertIn('SameSite=Lax', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('Secure', cookie)

    def test_expired_session_is_named_as_such(self):
        """A stale token reads differently from no token at all."""
        self.app.sessions.destroy(self.session.token)
        stale = self.request('GET', '/api/dashboard', carrier='header')
        self.assertEqual(stale.status, 401)
        self.assertIn('expired', stale.payload['error'])
        absent = self.request('GET', '/api/dashboard', signed_in=False)
        self.assertIn('sign in', absent.payload['error'])

    def test_routing_errors(self):
        """Unknown paths and wrong methods are reported apart."""
        self.assertEqual(self.request('GET', '/api/nothing').status, 404)
        self.assertEqual(self.request('POST', '/api/dashboard').status, 405)

    def test_session_is_required(self):
        """Without a cookie the API refuses everything but sign in."""
        self.assertEqual(
            self.request('GET', '/api/dashboard', signed_in=False).status,
            401)
        anonymous = self.request('GET', '/api/session', signed_in=False)
        self.assertEqual(anonymous.status, 200)
        self.assertFalse(anonymous.payload['signed_in'])

    def test_csrf_token_is_required_for_writes(self):
        """A write without the matching token is refused."""
        response = self.request('POST', '/api/devices', {'host': '10.0.0.5'},
                                csrf=False)
        self.assertEqual(response.status, 403)

    def test_sign_in_failure_is_reported(self):
        """A refused sign in returns 401 and no cookie."""
        response = self.request('POST', '/api/session',
                                {'username': 'nobody', 'password': ''},
                                signed_in=False)
        self.assertEqual(response.status, 401)
        self.assertEqual(response.cookies, [])
        self.assertIn('error', response.payload)

    def test_sign_out_clears_the_cookie(self):
        """Signing out drops the session and expires the cookie."""
        response = self.request('DELETE', '/api/session')
        self.assertEqual(response.status, 200)
        self.assertIn('Max-Age=0', response.cookies[0])
        self.assertIsNone(self.app.sessions.get(self.session.token))

    def test_add_probe_and_control_a_device(self):
        """A device can be added, polled and switched."""
        device_id = self.add_device()
        response = self.request('POST', f'/api/devices/{device_id}/action',
                                {'action': 'on'})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.payload['device']['state']['is_on'])
        self.assertEqual(self.app.bridge.commands, ['on'])

    def test_unknown_action_is_refused(self):
        """An action outside the vocabulary gives 400."""
        device_id = self.add_device()
        response = self.request('POST', f'/api/devices/{device_id}/action',
                                {'action': 'explode'})
        self.assertEqual(response.status, 400)

    def test_unreachable_device_gives_bad_gateway(self):
        """A device that does not answer gives 502, not 500."""
        response = self.request('POST', '/api/devices',
                                {'host': '10.0.0.99'})
        self.assertEqual(response.status, 201)
        self.assertIn('no route', response.payload['warning'])
        device_id = response.payload['device']['device_id']
        failed = self.request('POST', f'/api/devices/{device_id}/action',
                              {'action': 'on'})
        self.assertEqual(failed.status, 502)

    def test_unknown_device_gives_not_found(self):
        """An identifier that is not in the inventory gives 404."""
        absent = '00000000-0000-4000-8000-000000000000'
        self.assertEqual(
            self.request('GET', f'/api/devices/{absent}').status, 404)

    # The identifier arrives in a URL, so it is exactly the sort of
    # input that must never reach a path.
    def test_a_hostile_identifier_is_refused(self):
        """Nothing that is not one of our identifiers touches disk."""
        for nasty in ('..', '../../etc/passwd', 'mac-aabbcc001122',
                      '%2e%2e', 'a' * 300):
            for method, path, body in (
                    ('GET', f'/api/devices/{nasty}', None),
                    ('DELETE', f'/api/devices/{nasty}', None),
                    ('GET', f'/api/devices/{nasty}/schedule', None),
                    ('PUT', f'/api/devices/{nasty}/schedule',
                     {'schedule': '@daily off'}),
                    ('POST', f'/api/devices/{nasty}/action',
                     {'action': 'on'}),
            ):
                response = self.request(method, path, body)
                self.assertIn(response.status, (400, 404),
                              f'{method} {path} gave {response.status}')
        # Nothing was created outside the devices directory.
        self.assertEqual(os.listdir(self.app.settings.device_dir), [])

    def test_schedule_round_trip(self):
        """A valid schedule saves, and a bad one is refused."""
        device_id = self.add_device()
        good = self.request('PUT', f'/api/devices/{device_id}/schedule',
                            {'schedule': '0 6 * * mon-fri on\n@daily off\n'})
        self.assertEqual(good.status, 200)
        self.assertTrue(good.payload['summary']['valid'])
        self.assertEqual(good.payload['summary']['rule_count'], 2)
        stored = self.request('GET', f'/api/devices/{device_id}/schedule')
        self.assertIn('@daily off', stored.payload['schedule'])
        bad = self.request('PUT', f'/api/devices/{device_id}/schedule',
                           {'schedule': '0 6 * * * levitate'})
        self.assertEqual(bad.status, 400)
        self.assertFalse(bad.payload['summary']['valid'])
        self.assertIn('@daily off',
                      self.app.devices.get_schedule(device_id))

    def test_schedule_check_does_not_store(self):
        """Checking a script reports on it without saving it."""
        device_id = self.add_device()
        response = self.request('POST', '/api/schedule/check',
                                {'schedule': '@hourly refresh'})
        self.assertTrue(response.payload['summary']['valid'])
        stored = self.app.devices.get_schedule(device_id)
        self.assertNotIn('@hourly', stored)

    def test_scan_marks_known_devices(self):
        """Scan results say which devices are already in the inventory."""
        self.add_device()
        response = self.request('POST', '/api/scan', {'seconds': 1})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.payload['found'][0]['known'])

    def test_update_and_remove(self):
        """A device can be renamed, disabled and removed."""
        device_id = self.add_device()
        renamed = self.request('PATCH', f'/api/devices/{device_id}',
                               {'alias': 'Desk lamp', 'enabled': False})
        self.assertEqual(renamed.payload['device']['display_name'],
                         'Desk lamp')
        self.assertFalse(renamed.payload['device']['enabled'])
        removed = self.request('DELETE', f'/api/devices/{device_id}')
        self.assertEqual(removed.payload['removed'], device_id)
        self.assertEqual(self.app.devices.records(), [])

    def test_settings_hide_secrets(self):
        """Settings can be read and written without leaking the password."""
        self.request('PUT', '/api/settings',
                     {'settings': {'device_password': 'hunter2',
                                   'poll_seconds': 45}})
        response = self.request('GET', '/api/settings')
        self.assertEqual(response.payload['settings']['poll_seconds'], 45)
        self.assertNotIn('device_password', response.payload['settings'])
        self.assertTrue(
            response.payload['settings']['device_password_set'])
        self.assertEqual(self.app.settings.device_password, 'hunter2')

    def test_dashboard_summarises(self):
        """The dashboard counts devices and reports the scheduler."""
        device_id = self.add_device()
        self.request('POST', f'/api/devices/{device_id}/action',
                     {'action': 'on'})
        payload = self.request('GET', '/api/dashboard').payload
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['on'], 1)
        self.assertIn('enabled', payload['scheduler'])

    def test_activity_and_sessions(self):
        """Actions leave a trail and the session is listed."""
        device_id = self.add_device()
        self.request('POST', f'/api/devices/{device_id}/action',
                     {'action': 'toggle'})
        activity = self.request('GET', '/api/activity?limit=10')
        self.assertTrue(activity.payload['activity'])
        sessions = self.request('GET', '/api/sessions')
        self.assertEqual(sessions.payload['sessions'][0]['username'],
                         'tester')

    def test_an_unsupported_device_explains_itself(self):
        """A protocol python-kasa cannot speak is not a panel fault."""
        message = kasabridge.explain(
            '10.5.3.53',
            RuntimeError('Unsupported device 10.5.3.53 of type '
                         "SMART.KASAPLUG with encrypt_scheme "
                         "EncryptionScheme(encrypt_type='TPAP')"))
        self.assertIn('TPAP', message)
        self.assertIn('python-kasa', message)
        self.assertIn('not a fault in the panel', message)

    def test_an_authentication_failure_names_the_settings(self):
        """The commonest fixable case says what to fill in."""
        message = kasabridge.explain(
            'Hall plug', RuntimeError('Invalid authentication'))
        self.assertIn('device_username', message)
        self.assertIn('device_password', message)

    def test_an_ordinary_failure_is_left_alone(self):
        """Nothing is bolted onto a message that needs no help."""
        message = kasabridge.explain('10.0.0.5',
                                     RuntimeError('timed out'))
        self.assertEqual(message, '10.0.0.5: timed out')

    def test_reference_lists_the_language(self):
        """The reference endpoint feeds the help panel."""
        payload = self.request('GET', '/api/reference').payload
        names = [item['name'] for item in payload['actions']]
        self.assertIn('brightness', names)
        keywords = [item['name'] for item in payload['keywords']]
        self.assertIn('@daily', keywords)


if __name__ == '__main__':
    unittest.main()
