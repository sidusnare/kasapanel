// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useState} from 'react';
import {api} from '../api.js';
import {useToast} from '../components/Toasts.jsx';
import {DASH, shortTime} from '../format.js';

export default function Devices({devices, onChanged}) {
  const notify = useToast();
  const [host, setHost] = useState('');
  const [alias, setAlias] = useState('');
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState(false);

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
                    <th>Rules</th><th />
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
