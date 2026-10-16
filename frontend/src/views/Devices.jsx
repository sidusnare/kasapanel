// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useState} from 'react';
import {api} from '../api.js';
import ConfirmLock, {lockOutcome} from '../components/ConfirmLock.jsx';
import {useToast} from '../components/Toasts.jsx';
import {DASH, shortTime} from '../format.js';

export default function Devices({devices, onChanged}) {
  const notify = useToast();
  const [host, setHost] = useState('');
  const [alias, setAlias] = useState('');
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState(false);
  // The device being asked about, the question, and the request to send
  // if the answer is yes.
  const [ask, setAsk] = useState(null);

  async function add(target, name) {
    if (!target) {
      notify('Enter an address or hostname first.', true);
      return;
    }
    setBusy(true);
    try {
      const answer = await api.post('/api/devices',
        {host: target, alias: name || ''});
      notify(answer.warning
        ? `Added, but ${answer.warning}`
        : `Added ${answer.device.display_name}.`, Boolean(answer.warning));
      setHost('');
      setAlias('');
      onChanged();
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function scan() {
    setBusy(true);
    setFound(null);
    try {
      const answer = await api.post('/api/scan', {});
      setFound(answer.found || []);
      notify(`Scan finished: ${(answer.found || []).length} device(s).`);
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function rename(device) {
    const name = window.prompt('Name for this device', device.display_name);
    if (name === null) {
      return;
    }
    try {
      await api.patch(`/api/devices/${device.device_id}`, {alias: name});
      onChanged();
    } catch (err) {
      notify(err.message, true);
    }
  }

  async function setEnabled(device, enabled) {
    try {
      await api.patch(`/api/devices/${device.device_id}`, {enabled});
      onChanged();
    } catch (err) {
      notify(err.message, true);
    }
  }

  async function setLock(device, locked, confirmation) {
    try {
      await api.patch(`/api/devices/${device.device_id}`,
        {locked, ...(confirmation || {})});
      onChanged();
      return {done: true};
    } catch (err) {
      return lockOutcome(err);
    }
  }

  // Locking a device needs no ceremony; unlocking one is exactly what
  // the lock is there to slow down, so it asks first.
  async function toggleLock(device) {
    const run = (confirmation) => setLock(device, false, confirmation);
    const question = `Unlock ${device.display_name}? Switching it, saving `
      + 'its schedule and snoozing it will stop asking first.';
    if (device.locked) {
      setAsk({device, question, run});
      return;
    }
    const outcome = await setLock(device, true);
    if (!outcome.done) {
      notify(outcome.error, true);
    }
  }

  async function remove(device) {
    if (!window.confirm(`Remove ${device.display_name} and its schedule?`)) {
      return;
    }
    try {
      await api.remove(`/api/devices/${device.device_id}`);
      notify(`Removed ${device.display_name}.`);
      onChanged();
    } catch (err) {
      notify(err.message, true);
    }
  }

  return (
    <div className="split two">
      <div className="column wide">
        <div className="block">
          <h3 className="engraved">Inventory</h3>
          {devices.length === 0
            ? <p className="empty">No devices yet.</p>
            : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th><th>Address</th><th>Added</th>
                    <th>Rules</th><th>Locked</th><th />
                  </tr>
                </thead>
                <tbody>
                  {devices.map((device) => (
                    <tr key={device.device_id}>
                      <td>
                        {device.display_name}
                        {device.enabled
                          ? null
                          : <span className="badge">disabled</span>}
                      </td>
                      <td className="mono">{device.host}</td>
                      <td className="mono">{shortTime(device.added_at)}</td>
                      <td className="mono">{device.rule_count}</td>
                      <td>
                        <input
                          type="checkbox"
                          className="lock-box"
                          checked={Boolean(device.locked)}
                          aria-label={`Lock ${device.display_name}`}
                          onChange={() => toggleLock(device)}
                        />
                      </td>
                      <td>
                        <button type="button" className="quiet"
                          onClick={() => rename(device)}>Rename</button>
                        <button type="button" className="quiet"
                          onClick={() => setEnabled(device, !device.enabled)}>
                          {device.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button type="button" className="quiet"
                          onClick={() => remove(device)}>Remove</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
        {ask
          ? (
            <ConfirmLock
              title={`${ask.device.display_name} is locked`}
              question={ask.question}
              onConfirm={
                (password) => ask.run({confirm: true, password})}
              onClose={() => setAsk(null)}
            />
          )
          : null}
      </div>

      <div className="column">
        <div className="block">
          <h3 className="engraved">Add by address</h3>
          <label className="field">
            <span className="engraved">host or IP</span>
            <input value={host} onChange={(e) => setHost(e.target.value)}
              placeholder="192.168.1.40" />
          </label>
          <label className="field">
            <span className="engraved">name (optional)</span>
            <input value={alias} onChange={(e) => setAlias(e.target.value)}
              placeholder="Desk lamp" />
          </label>
          <div className="row">
            <button type="button" className="primary" disabled={busy}
              onClick={() => add(host, alias)}>Add device</button>
          </div>
        </div>

        <div className="block">
          <h3 className="engraved">Scan the network</h3>
          <p className="note">
            Sends a broadcast and waits for Kasa devices to answer. Devices
            on another subnet will not reply; add those by address.
          </p>
          <button type="button" className="quiet" disabled={busy}
            onClick={scan}>{busy ? 'Scanning…' : 'Scan now'}</button>
          {found === null ? null : found.length === 0
            ? <p className="empty">Nothing answered.</p>
            : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th><th>Address</th><th>Model</th><th />
                  </tr>
                </thead>
                <tbody>
                  {found.map((item) => (
                    <tr key={item.host}>
                      <td>{item.alias || DASH}</td>
                      <td className="mono">{item.host}</td>
                      <td className="mono">{item.model || DASH}</td>
                      <td>
                        {item.known
                          ? <span className="badge">known</span>
                          : (
                            <button type="button" className="quiet"
                              onClick={() => add(item.host, item.alias)}>
                              Add
                            </button>
                          )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      </div>
    </div>
  );
}
