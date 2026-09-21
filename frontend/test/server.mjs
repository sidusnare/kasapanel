// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// A stand-in daemon.  It answers with payloads recorded from the real
// API, and — this is the point of it — it refuses any request that does
// not carry the session, exactly as the daemon does.  A browser that
// will not store the cookie must still get through on the header.

import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

export function fixture(name) {
  return JSON.parse(
    fs.readFileSync(path.join(here, 'fixtures', `${name}.json`), 'utf8'));
}

export function createServer({acceptCookie = true, lastResort = false} = {}) {
  const calls = [];
  let signedIn = false;
  const token = 'session-token-xyz';
  const csrf = 'csrf-token-abc';
  const devices = fixture('dashboard').devices.map((d) => ({...d}));

  async function handle(url, options) {
    const method = (options.method || 'GET').toUpperCase();
    const pathname = String(url).split('?')[0];
    const headers = options.headers || {};
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({method, pathname, headers});

    const carriesSession = headers['X-Kasa-Session'] === token
      || (acceptCookie && signedIn && options.credentials === 'same-origin'
        && cookieJar.length > 0);

    const reply = (status, payload) => ({
      ok: status < 400,
      status,
      json: async () => payload,
    });

    if (pathname === '/api/session' && method === 'POST') {
      if (body.password !== 'secret') {
        return reply(401, {error: 'that username and password did not match'});
      }
      signedIn = true;
      if (acceptCookie) {
        cookieJar.push('kasapanel_session');
      }
      return reply(200, {
        username: body.username,
        csrf_token: csrf,
        session_token: token,
        groups: ['staff'],
      });
    }
    if (pathname === '/api/session' && method === 'GET') {
      if (!carriesSession) {
        return reply(200, {signed_in: false, pam_available: true,
          pam_error: ''});
      }
      return reply(200, {signed_in: true, username: 'ada', csrf_token: csrf,
        auth_method: 'header', groups: ['staff'], active_sessions: 1});
    }
    if (pathname === '/api/session' && method === 'DELETE') {
      signedIn = false;
      cookieJar.length = 0;
      return reply(200, {signed_out: true});
    }

    if (!carriesSession) {
      return reply(401, {error: 'sign in to continue'});
    }
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)
        && headers['X-Kasa-CSRF'] !== csrf) {
      return reply(403, {error: 'the session token did not match'});
    }

    const account = lastResort
      ? {name: 'nobody', uid: 65534, gid: 65534, last_resort: true,
        warning: 'Kasa Panel is running as "nobody", a shared account it '
          + 'does not own. Set a daemon user.', privileged_helper: true}
      : {name: 'kasapanel', uid: 992, gid: 992, last_resort: false,
        warning: '', privileged_helper: true};
    if (pathname === '/api/dashboard') {
      return reply(200, {...fixture('dashboard'), devices,
        daemon_user: account});
    }
    if (pathname === '/api/reference') {
      return reply(200, fixture('reference'));
    }
    if (pathname === '/api/activity') {
      return reply(200, fixture('activity'));
    }
    if (pathname === '/api/sessions') {
      return reply(200, fixture('sessions'));
    }
    if (pathname === '/api/settings' && method === 'GET') {
      return reply(200, {...fixture('settings'), daemon_user: account});
    }
    if (pathname === '/api/settings' && method === 'PUT') {
      return reply(200, {settings: fixture('settings').settings});
    }
    if (pathname === '/api/devices' && method === 'GET') {
      return reply(200, {devices});
    }
    if (pathname === '/api/devices' && method === 'POST') {
      const device = {...devices[0], device_id: 'host-new', host: body.host,
        alias: body.alias || '', display_name: body.alias || body.host};
      devices.push(device);
      return reply(201, {device});
    }
    if (pathname === '/api/scan') {
      return reply(200, {found: [
        {host: '10.0.0.7', alias: 'Hall lamp', model: 'HS100', known: false},
      ], seconds: 1});
    }
    const action = pathname.match(/^\/api\/devices\/([^/]+)\/action$/);
    if (action) {
      const device = devices.find((d) => d.device_id === action[1]);
      const next = {...device,
        state: {...device.state, is_on: body.action === 'on'
          ? true : body.action === 'off' ? false : !device.state.is_on}};
      devices[devices.indexOf(device)] = next;
      return reply(200, {device: next, result: 'ok'});
    }
    const schedule = pathname.match(/^\/api\/devices\/([^/]+)\/schedule$/);
    if (schedule && method === 'GET') {
      return reply(200, fixture('schedule'));
    }
    if (schedule && method === 'PUT') {
      if (body.schedule.includes('levitate')) {
        return reply(400, {error: 'the schedule has errors', summary: {
          valid: false, rule_count: 0, rules: [], next_runs: [],
          problems: [{line_number: 1, text: body.schedule,
            message: 'unknown action "levitate"'}],
        }});
      }
      return reply(200, {...fixture('schedule'), schedule: body.schedule});
    }
    if (pathname === '/api/schedule/check') {
      return reply(200, {summary: fixture('schedule').summary});
    }
    return reply(404, {error: 'no such endpoint'});
  }

  const cookieJar = [];
  return {calls, handle, token, csrf};
}
