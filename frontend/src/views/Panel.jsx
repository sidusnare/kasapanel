// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useState} from 'react';
import {api} from '../api.js';
import ConfirmLock, {lockOutcome} from '../components/ConfirmLock.jsx';
import Rocker from '../components/Rocker.jsx';
import Slider from '../components/Slider.jsx';
import {CustomSnooze, SnoozeMenu} from '../components/SnoozeMenu.jsx';
import {useToast} from '../components/Toasts.jsx';
import {DASH, clockTime, deviceSubtitle, shortTime, stateWord, watts}
  from '../format.js';
import {moveCard, orderDevices, shiftCard} from '../order.js';

function Card({device, reference, onChanged, drag}) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const [custom, setCustom] = useState(false);
  // What a locked device is being asked to confirm: the question to
  // show, and the request to send once somebody says yes.
  const [ask, setAsk] = useState(null);
  const state = device.state || {};
  const mark = stateWord(device);
  const nextRun = (device.next_runs || [])[0];

  // Every request below answers with an outcome rather than reporting
  // its own trouble, because a refusal from a locked device is not
  // trouble: it is the dialog's cue.
  async function send(action, args, confirmation) {
    setBusy(true);
    try {
      const answer = await api.post(
        `/api/devices/${device.device_id}/action`,
        {action, arguments: args || [], ...(confirmation || {})});
      onChanged(answer.device);
      return {done: true};
    } catch (err) {
      return lockOutcome(err);
    } finally {
      setBusy(false);
    }
  }

  async function snoozeRun(span, confirmation) {
    setBusy(true);
    try {
      const answer = await api.post(
        `/api/devices/${device.device_id}/snooze`,
        {for: span, ...(confirmation || {})});
      onChanged(answer.device);
      notify(`${device.display_name}: schedule snoozed until `
        + `${shortTime(answer.snoozed_until)}`);
      return {done: true};
    } catch (err) {
      return lockOutcome(err);
    } finally {
      setBusy(false);
    }
  }

  // An ordinary request: nothing to confirm, so only trouble is worth
  // saying anything about.
  async function report(run) {
    const outcome = await run();
    if (!outcome.done) {
      notify(`${device.display_name}: ${outcome.error}`, true);
    }
  }

  // A request a locked device will not take unasked.  The view can be a
  // poll behind the daemon, so a refusal that arrives anyway opens the
  // same plate rather than reading as an error.
  async function guard(question, run) {
    if (device.locked) {
      setAsk({question, run});
      return;
    }
    const outcome = await run();
    if (outcome.done) {
      return;
    }
    if (outcome.locked) {
      setAsk({question, run});
      return;
    }
    notify(`${device.display_name}: ${outcome.error}`, true);
  }

  function switchTo(action) {
    const words = {on: 'Switch on', off: 'Switch off', toggle: 'Toggle'};
    guard(`${words[action]} ${device.display_name}?`,
      (confirmation) => send(action, [], confirmation));
  }

  // Resolves true once the card has the request in hand: either the
  // snooze is set, or a locked device has moved the question to the
  // confirmation plate.  Either way the custom dialog is finished.
  async function snooze(span) {
    const question = `Snooze ${device.display_name} for ${span}?`;
    const run = (confirmation) => snoozeRun(span, confirmation);
    if (device.locked) {
      setAsk({question, run});
      return true;
    }
    const outcome = await run();
    if (outcome.done) {
      return true;
    }
    if (outcome.locked) {
      setAsk({question, run});
      return true;
    }
    notify(`${device.display_name}: ${outcome.error}`, true);
    return false;
  }

  async function resume() {
    setBusy(true);
    try {
      const answer = await api.remove(
        `/api/devices/${device.device_id}/snooze`);
      onChanged(answer.device);
      notify(`${device.display_name}: schedule resumed`);
    } catch (err) {
      notify(`${device.display_name}: ${err.message}`, true);
    } finally {
      setBusy(false);
    }
  }

  async function poll() {
    setBusy(true);
    try {
      const answer = await api.post(
        `/api/devices/${device.device_id}/refresh`);
      onChanged(answer.device);
    } catch (err) {
      notify(`${device.display_name}: ${err.message}`, true);
    } finally {
      setBusy(false);
    }
  }


  const moving = drag.moving === device.device_id;
  const marks = [`card ${mark.card}`];
  if (moving) {
    marks.push('is-moving');
  }
  if (drag.over === device.device_id && !moving) {
    marks.push('is-landing');
  }

  // The whole card takes a drop, because aiming at the header alone
  // asks more of the hand than rearranging cards is worth.  Only the
  // header starts one.
  return (
    <article
      className={marks.join(' ')}
      onDragOver={(event) => {
        if (!drag.moving) {
          return;
        }
        event.preventDefault();
        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = 'move';
        }
        drag.onOver(device.device_id);
      }}
      onDrop={(event) => {
        event.preventDefault();
        drag.onDrop(device.device_id);
      }}
    >
      <header
        className="card-head"
        draggable
        onDragStart={(event) => {
          if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
            // Firefox will not begin a drag that carries nothing, and
            // the identifier is the one thing worth carrying.
            event.dataTransfer.setData('text/plain', device.device_id);
          }
          drag.onStart(device.device_id);
        }}
        onDragEnd={drag.onEnd}
      >
        <button
          type="button"
          className="card-grip"
          title="Drag the header, or use the arrow keys, to move this card"
          aria-label={`Move ${device.display_name}; use the arrow keys`}
          onKeyDown={(event) => {
            const step = {ArrowLeft: -1, ArrowUp: -1,
              ArrowRight: 1, ArrowDown: 1}[event.key];
            if (!step) {
              return;
            }
            event.preventDefault();
            drag.onShift(device.device_id, step);
          }}
        >
          <span aria-hidden="true">{'\u283F'}</span>
        </button>
        <div>
          <h3 className="card-name">
            {device.display_name}
            {device.locked
              ? (
                <span className="card-lock" role="img" aria-label="locked">
                  {'\u{1F512}'}
                </span>
              )
              : null}
          </h3>
          <p className="card-sub mono">{deviceSubtitle(device)}</p>
        </div>
        <Rocker
          draggable={false}
          on={state.is_on}
          busy={busy}
          disabled={!device.enabled || state.reachable === false}
          name={device.display_name}
          onToggle={() => switchTo(state.is_on ? 'off' : 'on')}
        />
      </header>

      <dl className="readings">
        <div>
          <dt className="engraved">state</dt>
          <dd><b className={`state-word ${mark.tone}`}>{mark.word}</b></dd>
        </div>
        <div>
          <dt className="engraved">draw</dt>
          <dd className="mono">
            {state.power_watts === undefined ? DASH : watts(state.power_watts)}
          </dd>
        </div>
        <div>
          <dt className="engraved">on since</dt>
          <dd className="mono">{shortTime(state.on_since)}</dd>
        </div>
        <div>
          <dt className="engraved">polled</dt>
          <dd className="mono">{clockTime(state.polled_at || device.last_seen)}</dd>
        </div>
        <div>
          <dt className="engraved">signal</dt>
          <dd className="mono">
            {state.rssi === undefined || state.rssi === null
              ? DASH : `${state.rssi} dBm`}
          </dd>
        </div>
        <div>
          <dt className="engraved">next rule</dt>
          <dd className="mono">
            {nextRun ? `${shortTime(nextRun.when)} ${nextRun.command}` : DASH}
          </dd>
        </div>
      </dl>

      {device.snoozed_until
        ? (
          <p className="snoozed mono">
            Schedule snoozed until {shortTime(device.snoozed_until)}
          </p>
        )
        : null}
      {state.error ? <p className="alarm">{state.error}</p> : null}
      {device.schedule_problems
        ? (
          <p className="alarm">
            {device.schedule_problems} problem(s) in this schedule
          </p>
        )
        : null}

      {state.is_dimmable
        ? (
          <Slider
            label="brightness" min={1} max={100} unit="%"
            value={state.brightness} disabled={busy || !state.is_on}
            onCommit={(value) => report(() => send('brightness', [value]))}
          />
        )
        : null}
      {state.is_variable_white
        ? (
          <Slider
            label="warmth" min={2500} max={6500} step={100} unit="K"
            value={state.colour_temperature} disabled={busy || !state.is_on}
            onCommit={
              (value) => report(() => send('temperature', [value]))}
          />
        )
        : null}

      <footer className="card-actions">
        <button type="button" className="quiet" disabled={busy}
          onClick={poll}>Poll now</button>
        <button type="button" className="quiet" disabled={busy}
          onClick={() => switchTo('toggle')}>Toggle</button>
        {state.has_led
          ? (
            <button type="button" className="quiet" disabled={busy}
              onClick={() => report(() => send('led', ['off']))}>
              LED off
            </button>
          )
          : null}
        <SnoozeMenu
          snoozedUntil={device.snoozed_until}
          disabled={busy}
          onSnooze={snooze}
          onResume={resume}
          onCustom={() => setCustom(true)}
        />
      </footer>
      {custom
        ? (
          <CustomSnooze
            device={device}
            reference={reference}
            onSnooze={snooze}
            onClose={() => setCustom(false)}
          />
        )
        : null}
      {ask
        ? (
          <ConfirmLock
            title={`${device.display_name} is locked`}
            question={ask.question}
            onConfirm={
              (password) => ask.run({confirm: true, password})}
            onClose={() => setAsk(null)}
          />
        )
        : null}
    </article>
  );
}

export default function Panel({dashboard, reference, order, onChanged,
  onReorder}) {
  const devices = (dashboard && dashboard.devices) || [];
  // Which card is in the hand, and which one it is hovering over.  Both
  // are identifiers rather than positions: the dashboard poll can
  // rebuild the list mid-drag, and a position would then mean a
  // different card than the one under the pointer.
  const [moving, setMoving] = useState('');
  const [over, setOver] = useState('');
  const shown = orderDevices(devices, order);
  const ids = shown.map((device) => device.device_id);

  function rearrange(next) {
    if (onReorder && next.join() !== ids.join()) {
      onReorder(next);
    }
  }

  const drag = {
    moving,
    over,
    onStart: (id) => { setMoving(id); setOver(''); },
    onOver: (id) => setOver(id),
    onEnd: () => { setMoving(''); setOver(''); },
    onDrop: (id) => {
      setOver('');
      if (moving) {
        setMoving('');
        rearrange(moveCard(ids, moving, id));
      }
    },
    onShift: (id, step) => rearrange(shiftCard(ids, id, step)),
  };

  if (!devices.length) {
    return (
      <p className="empty">
        No devices yet. Open the Devices tab to scan the network or add one
        by address.
      </p>
    );
  }
  return (
    <div className="grid">
      {shown.map((device) => (
        <Card key={device.device_id} device={device} reference={reference}
          onChanged={onChanged} drag={drag} />
      ))}
    </div>
  );
}
