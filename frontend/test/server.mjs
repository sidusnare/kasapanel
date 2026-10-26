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

// The daemon's own zone, or the one this machine keeps, so a test can
// mount a panel whose browser agrees with the daemon about the clock.
function localZone() {
  const minutes = -new Date().getTimezoneOffset();
  const pad = (value) => String(Math.abs(value)).padStart(2, '0');
  const parts = new Intl.DateTimeFormat(undefined, {timeZoneName: 'short'})
    .formatToParts(new Date());
  return {
    server_utc_offset: `${minutes < 0 ? '-' : '+'}`
      + `${pad(Math.trunc(minutes / 60))}:${pad(minutes % 60)}`,
    server_timezone: (parts.find((part) => part.type === 'timeZoneName')
      || {}).value || 'local',
  };
}

// Stand-ins for devices the recorded dashboard does not have, for the
// tests that need more than one card to rearrange.
export const EXTRA_CARDS = [
  ['7c1e4b80-2d6a-4f13-8b05-91ad3e6c7f22', 'Hall lamp'],
  ['b5390af6-41c7-4d2e-ae18-6f70c2d9b483', 'Shed heater'],
];

export function createServer({acceptCookie = true, lastResort = false,
  hostZone = '', locked = false, securePassword = '', extraCards = 0,
  theme = 'light', cardOrder = []} = {}) {
  const calls = [];
  let signedIn = false;
  const token = 'session-token-xyz';
  const csrf = 'csrf-token-abc';
  const devices = fixture('dashboard').devices
    .map((d) => ({...d, locked: Boolean(locked)}));
  EXTRA_CARDS.slice(0, extraCards).forEach(([deviceId, name]) => {
    devices.push({...devices[0], device_id: deviceId, alias: name,
      display_name: name});
  });

  // The per-user preference file, as far as a browser can tell.
  const preferences = {
    version: 1,
    username: 'ada',
    theme,
    device_card_order: [...cardOrder],
  };

  // The lock as the daemon enforces it: a change to a locked device
  // needs confirm, and a secure_locked one needs the password too --
  // which the browser is never told in advance, only asked for.
  function refuseUnconfirmed(device, body, what) {
    if (!device || !device.locked) {
      return null;
    }
    if (!body.confirm) {
      return {status: 409, payload: {
        error: `${device.display_name} is locked: confirm ${what}.`,
        locked: true, needs_password: Boolean(securePassword)}};
    }
    if (securePassword && body.password !== securePassword) {
      return {status: 403, payload: {
        error: body.password
          ? 'that username and password did not match'
          : 'enter a password',
        locked: true, needs_password: true}};
    }
    return null;
  }

  async function handle(url, options) {
    const method = (options.method || 'GET').toUpperCase();
    const pathname = String(url).split('?')[0];
    const headers = options.headers || {};
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({method, pathname, headers, body});

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
      const payload = fixture('dashboard');
      const summary = hostZone === 'local'
        ? {...payload.summary, ...localZone()}
        : payload.summary;
      return reply(200, {...payload, summary, devices,
        daemon_user: account});
    }
    if (pathname === '/api/reference') {
      return reply(200, fixture('reference'));
    }
    if (pathname === '/api/preferences' && method === 'GET') {
      return reply(200, {preferences});
    }
    if (pathname === '/api/preferences' && method === 'PUT') {
      const changes = body.preferences || body;
      if (['light', 'dark'].includes(changes.theme)) {
        preferences.theme = changes.theme;
      }
      if (Array.isArray(changes.device_card_order)) {
        preferences.device_card_order = [...changes.device_card_order];
      }
      return reply(200, {preferences});
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
    const patch = pathname.match(/^\/api\/devices\/([^/]+)$/);
    if (patch && method === 'PATCH') {
      const device = devices.find((d) => d.device_id === patch[1]);
      if ('locked' in body && !body.locked) {
        const refused = refuseUnconfirmed(device, body, 'unlocking it');
        if (refused) {
          return reply(refused.status, refused.payload);
        }
      }
      const next = {...device, ...('locked' in body
        ? {locked: Boolean(body.locked)} : {}),
      ...('alias' in body
        ? {alias: body.alias, display_name: body.alias || device.host} : {})};
      devices[devices.indexOf(device)] = next;
      return reply(200, {device: next});
    }
    const action = pathname.match(/^\/api\/devices\/([^/]+)\/action$/);
    if (action) {
      const device = devices.find((d) => d.device_id === action[1]);
      if (['on', 'off', 'toggle'].includes(body.action)) {
        const refused = refuseUnconfirmed(device, body,
          `switching it (${body.action})`);
        if (refused) {
          return reply(refused.status, refused.payload);
        }
      }
      const next = {...device,
        state: {...device.state, is_on: body.action === 'on'
          ? true : body.action === 'off' ? false : !device.state.is_on}};
      devices[devices.indexOf(device)] = next;
      return reply(200, {device: next, result: 'ok'});
    }
    const snooze = pathname.match(/^\/api\/devices\/([^/]+)\/snooze$/);
    if (snooze) {
      const device = devices.find((d) => d.device_id === snooze[1]);
      if (method === 'DELETE') {
        const next = {...device, snoozed_until: ''};
        devices[devices.indexOf(device)] = next;
        return reply(200,
          {device: next, lifted: Boolean(device.snoozed_until)});
      }
      const refused = refuseUnconfirmed(device, body,
        'snoozing its schedule');
      if (refused) {
        return reply(refused.status, refused.payload);
      }
      if (!/^\d/.test(body.for) && !body.for.startsWith('tomorrow')) {
        return reply(400, {error: `"${body.for}" is not a time span or a `
          + 'timestamp'});
      }
      const until = '2026-07-29T16:55:00';
      const next = {...device, snoozed_until: until};
      devices[devices.indexOf(device)] = next;
      return reply(200, {device: next, snoozed_until: until});
    }
    if (pathname === '/api/snooze/check') {
      return /^\d/.test(body.for) || body.for.startsWith('tomorrow')
        ? reply(200, {valid: true, error: '',
          snoozed_until: '2026-07-30T07:00:00'})
        : reply(200, {valid: false, snoozed_until: '',
          error: `"${body.for}" is not a time span or a timestamp`});
    }
    const schedule = pathname.match(/^\/api\/devices\/([^/]+)\/schedule$/);
    if (schedule && method === 'GET') {
      return reply(200, fixture('schedule'));
    }
    if (schedule && method === 'PUT') {
      const device = devices.find((d) => d.device_id === schedule[1]);
      const refused = refuseUnconfirmed(device, body,
        'saving its schedule');
      if (refused) {
        return reply(refused.status, refused.payload);
      }
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
  return {calls, handle, token, csrf, preferences};
}
