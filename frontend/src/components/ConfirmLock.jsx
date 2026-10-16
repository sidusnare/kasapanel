// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// The yes/no plate a locked device puts in front of anything that would
// switch it, reschedule it or snooze it.
//
// The password field is not drawn until the daemon asks for it.  A
// device can be marked secure_locked in its file, which makes the
// confirmation need the operator's password as well as their consent,
// and that field is deliberately not a setting the panel can read: the
// panel finds out the same way anything else holding a session would,
// by being told so when it tries.  So "Yes" is sent first, and if the
// answer comes back asking for a password the dialog stays open and
// asks for one.

import React, {useEffect, useRef, useState} from 'react';
import Modal from './Modal.jsx';

// Turns a refused request into what this dialog needs to know.
export function lockOutcome(err) {
  const payload = (err && err.payload) || {};
  return {
    done: false,
    locked: Boolean(payload.locked),
    needsPassword: Boolean(payload.needs_password),
    error: (err && err.message) || 'that change was refused',
  };
}

export default function ConfirmLock({title, question, onConfirm, onClose}) {
  const [password, setPassword] = useState('');
  const [needsPassword, setNeedsPassword] = useState(false);
  const [problem, setProblem] = useState('');
  const [sending, setSending] = useState(false);
  const field = useRef(null);

  // Focus the password field the moment it appears, so the second "Yes"
  // is a matter of typing rather than hunting for the box.
  useEffect(() => {
    if (needsPassword && field.current) {
      field.current.focus();
    }
  }, [needsPassword]);

  async function yes(event) {
    event.preventDefault();
    if (needsPassword && !password) {
      return;
    }
    setSending(true);
    const outcome = await onConfirm(password);
    setSending(false);
    if (outcome.done) {
      onClose();
      return;
    }
    setPassword('');
    setProblem(outcome.error || '');
    if (outcome.needsPassword) {
      setNeedsPassword(true);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={yes}>
        <p className="confirm-question">{question}</p>
        {needsPassword
          ? (
            <label className="field">
              <span className="engraved">your password</span>
              <input
                type="password"
                name="confirm-password"
                autoComplete="current-password"
                ref={field}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
          )
          : null}
        <p className="confirm-problem" aria-live="polite">
          {problem || ' '}
        </p>
        <div className="row modal-actions">
          <button type="button" className="quiet" onClick={onClose}>
            No
          </button>
          <button type="submit" className="primary"
            disabled={sending || (needsPassword && !password)}>
            Yes
          </button>
        </div>
      </form>
    </Modal>
  );
}
