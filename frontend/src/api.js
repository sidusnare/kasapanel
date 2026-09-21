// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// The one place that talks to the daemon.
//
// The session travels two ways: the daemon sets an HttpOnly cookie, and
// it also hands back the token so we can send it in the X-Kasa-Session
// header.  The header is what makes the panel work behind a self-signed
// certificate, because browsers refuse to store a Secure cookie from an
// origin whose certificate they do not trust.  A custom header cannot be
// attached by a cross-site form, so this costs nothing in CSRF terms.

const STORE_KEY = 'kasapanel.credentials';

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload || {};
  }
}

function readStore() {
  try {
    const raw = window.sessionStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;  // Private mode, or storage disabled: memory will do.
  }
}

function writeStore(value) {
  try {
    if (value) {
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(value));
    } else {
      window.sessionStorage.removeItem(STORE_KEY);
    }
  } catch (err) {
    // Not fatal; the credentials stay in memory for this page load.
  }
}

let credentials = readStore() || {session: '', csrf: ''};

export function getCredentials() {
  return credentials;
}

export function setCredentials(session, csrf) {
  credentials = {session: session || '', csrf: csrf || ''};
  writeStore(credentials);
}

export function clearCredentials() {
  credentials = {session: '', csrf: ''};
  writeStore(null);
}

export async function call(method, path, body) {
  const headers = {Accept: 'application/json'};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (credentials.session) {
    headers['X-Kasa-Session'] = credentials.session;
  }
  if (credentials.csrf) {
    headers['X-Kasa-CSRF'] = credentials.csrf;
  }
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(`cannot reach the daemon: ${err.message}`, 0, {});
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (err) {
    payload = {error: 'the daemon sent something that was not JSON'};
  }
  if (!response.ok) {
    throw new ApiError(
      payload.error || `request failed (${response.status})`,
      response.status, payload);
  }
  return payload;
}

export const api = {
  get: (path) => call('GET', path),
  post: (path, body) => call('POST', path, body === undefined ? {} : body),
  put: (path, body) => call('PUT', path, body),
  patch: (path, body) => call('PATCH', path, body),
  remove: (path) => call('DELETE', path),
};
