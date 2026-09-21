// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useState} from 'react';

export default function SignIn({message, pamError, onSubmit}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await onSubmit(username, password);
    } catch (err) {
      setError(err.message);
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  const complaint = error || message || '';

  return (
    <section className="signin">
      <form className="plate" onSubmit={submit}>
        <p className="engraved">Kasa Panel</p>
        <h1>Sign in</h1>
        <p className="note">
          Use your account on this machine. Passwords are checked by PAM;
          the panel keeps no password of its own.
        </p>

        {pamError
          ? <p className="alarm">{pamError}</p>
          : null}
        {complaint
          ? <p className="alarm" role="alert">{complaint}</p>
          : null}

        <label className="field">
          <span className="engraved">User</span>
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoFocus
          />
        </label>
        <label className="field">
          <span className="engraved">Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Checking…' : 'Sign in'}
        </button>
      </form>
    </section>
  );
}
