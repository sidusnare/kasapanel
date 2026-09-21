// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// The snooze menu on a device card, and the dialog behind its "Custom"
// entry.  Every choice is sent to the daemon in systemd.time syntax, the
// presets included, so there is one parser and it lives on the server.

import React, {useEffect, useRef, useState} from 'react';
import {api} from '../api.js';
import {shortTime} from '../format.js';
import Modal from './Modal.jsx';

export const PRESETS = [
  ['5min', '5 minutes'],
  ['15min', '15 minutes'],
  ['30min', '30 minutes'],
  ['1h', '1 hour'],
  ['3h', '3 hours'],
  ['6h', '6 hours'],
  ['12h', '12 hours'],
  ['1d', '1 day'],
  ['1w', '1 week'],
];

const CHECK_DELAY_MS = 250;

export function SnoozeMenu({snoozedUntil, disabled, onSnooze, onResume,
  onCustom}) {
  const [open, setOpen] = useState(false);
  const holder = useRef(null);

  // Close on a click anywhere else, or on Escape.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    function onDown(event) {
      if (holder.current && !holder.current.contains(event.target)) {
        setOpen(false);
      }
    }
    function onKey(event) {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  function choose(action) {
    setOpen(false);
    action();
  }

  return (
    <div className="menu-holder" ref={holder}>
      <button
        type="button"
        className={snoozedUntil ? 'quiet is-snoozed' : 'quiet'}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {snoozedUntil ? 'Snoozed' : 'Snooze'} {'▾'}
      </button>
      {open
        ? (
          <ul className="menu" role="menu" aria-label="Snooze the schedule">
            {snoozedUntil
              ? (
                <li role="none">
                  <button type="button" role="menuitem" className="menu-resume"
                    onClick={() => choose(onResume)}>
                    Resume schedule now
                  </button>
                </li>
              )
              : null}
            {PRESETS.map(([span, label]) => (
              <li role="none" key={span}>
                <button type="button" role="menuitem"
                  onClick={() => choose(() => onSnooze(span))}>
                  {label}
                </button>
              </li>
            ))}
            <li role="none" className="menu-rule">
              <button type="button" role="menuitem"
                onClick={() => choose(onCustom)}>
                Custom{'…'}
              </button>
            </li>
          </ul>
        )
        : null}
    </div>
  );
}

export function CustomSnooze({device, reference, onClose, onSnooze}) {
  const [text, setText] = useState('');
  const [check, setCheck] = useState(null);
  const [sending, setSending] = useState(false);
  const examples = (reference && reference.snooze
    && reference.snooze.examples) || [];

  // Ask the daemon what the text means as it is typed, so a mistake is
  // explained before anything is saved.
  useEffect(() => {
    if (!text.trim()) {
      setCheck(null);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const answer = await api.post('/api/snooze/check', {for: text});
        if (!cancelled) {
          setCheck(answer);
        }
      } catch (err) {
        if (!cancelled) {
          setCheck({valid: false, error: err.message});
        }
      }
    }, CHECK_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [text]);

  async function submit(event) {
    event.preventDefault();
    if (!text.trim()) {
      return;
    }
    setSending(true);
    const done = await onSnooze(text);
    setSending(false);
    if (done) {
      onClose();
    }
  }

  return (
    <Modal title={`Snooze ${device.display_name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="note">
          Hold this device's schedule until a time given in{' '}
          <span className="mono">systemd.time</span> syntax: a span counted
          from now, or a date and time. Rules due in the meantime are
          skipped, not caught up.
        </p>
        <label className="field">
          <span className="engraved">Snooze until</span>
          <input
            type="text"
            name="snooze"
            autoComplete="off"
            spellCheck="false"
            placeholder="2h 30min"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <p className={check && !check.valid ? 'snooze-check bad'
          : 'snooze-check'} aria-live="polite">
          {check
            ? (check.valid
              ? `Resumes ${shortTime(check.snoozed_until)}`
              : check.error)
            : ' '}
        </p>
        {examples.length
          ? (
            <div className="examples">
              <p className="engraved">Examples</p>
              <ul>
                {examples.map((example) => (
                  <li key={example.text}>
                    <button type="button" className="link mono"
                      onClick={() => setText(example.text)}>
                      {example.text}
                    </button>
                    <span>{example.means}</span>
                  </li>
                ))}
              </ul>
            </div>
          )
          : null}
        <div className="row modal-actions">
          <button type="button" className="quiet" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary"
            disabled={sending || !text.trim()
              || Boolean(check && !check.valid)}>
            Snooze
          </button>
        </div>
      </form>
    </Modal>
  );
}
