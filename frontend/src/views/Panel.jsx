// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useState} from 'react';
import {api} from '../api.js';
import Rocker from '../components/Rocker.jsx';
import Slider from '../components/Slider.jsx';
import {CustomSnooze, SnoozeMenu} from '../components/SnoozeMenu.jsx';
import {useToast} from '../components/Toasts.jsx';
import {DASH, clockTime, deviceSubtitle, shortTime, stateWord, watts}
  from '../format.js';

function Card({device, reference, onChanged}) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const [custom, setCustom] = useState(false);
  const state = device.state || {};
  const mark = stateWord(device);
  const nextRun = (device.next_runs || [])[0];

  async function send(action, args) {
    setBusy(true);
    try {
      const answer = await api.post(
        `/api/devices/${device.device_id}/action`,
        {action, arguments: args || []});
      onChanged(answer.device);
    } catch (err) {
      notify(`${device.display_name}: ${err.message}`, true);
    } finally {
      setBusy(false);
    }
  }

  // Resolves true when the snooze was set, so the dialog knows to close.
  async function snooze(span) {
    setBusy(true);
    try {
      const answer = await api.post(
        `/api/devices/${device.device_id}/snooze`, {for: span});
      onChanged(answer.device);
      notify(`${device.display_name}: schedule snoozed until `
        + `${shortTime(answer.snoozed_until)}`);
      return true;
    } catch (err) {
      notify(`${device.display_name}: ${err.message}`, true);
      return false;
    } finally {
      setBusy(false);
    }
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

  return (
    <article className={`card ${mark.card}`}>
      <header className="card-head">
        <div>
          <h3 className="card-name">{device.display_name}</h3>
          <p className="card-sub mono">{deviceSubtitle(device)}</p>
        </div>
        <Rocker
          on={state.is_on}
          busy={busy}
          disabled={!device.enabled || state.reachable === false}
          name={device.display_name}
          onToggle={() => send(state.is_on ? 'off' : 'on')}
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
            onCommit={(value) => send('brightness', [value])}
          />
        )
        : null}
      {state.is_variable_white
        ? (
          <Slider
            label="warmth" min={2500} max={6500} step={100} unit="K"
            value={state.colour_temperature} disabled={busy || !state.is_on}
            onCommit={(value) => send('temperature', [value])}
          />
        )
        : null}

      <footer className="card-actions">
        <button type="button" className="quiet" disabled={busy}
          onClick={poll}>Poll now</button>
        <button type="button" className="quiet" disabled={busy}
          onClick={() => send('toggle')}>Toggle</button>
        {state.has_led
          ? (
            <button type="button" className="quiet" disabled={busy}
              onClick={() => send('led', ['off'])}>LED off</button>
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
    </article>
  );
}

export default function Panel({dashboard, reference, onChanged}) {
  const devices = (dashboard && dashboard.devices) || [];
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
      {devices.map((device) => (
        <Card key={device.device_id} device={device} reference={reference}
          onChanged={onChanged} />
      ))}
    </div>
  );
}
