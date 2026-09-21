// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useCallback, useEffect, useState} from 'react';
import {api} from '../api.js';
import Countdown from '../components/Countdown.jsx';
import {useToast} from '../components/Toasts.jsx';
import {DASH, shortTime} from '../format.js';

// Labels and hints live here rather than on the daemon: they are a
// property of the interface, and the daemon already publishes which
// fields need a restart.

const FIELDS = [
  ['daemon_user', 'daemon user',
    'The unprivileged account the daemon runs as. Leave blank and it '
    + 'falls back to www-data, then httpd, then nobody.'],
  ['listen_host', 'listen address', 'Use 127.0.0.1 to keep the panel local.'],
  ['listen_port', 'listen port', ''],
  ['tls_certificate', 'TLS certificate', 'Leave blank for the self-signed pair.'],
  ['tls_key', 'TLS key', ''],
  ['pam_service', 'PAM service', 'A file name from /etc/pam.d.'],
  ['allowed_users', 'allowed users', 'Comma separated; empty means anyone PAM accepts.'],
  ['allowed_groups', 'allowed groups', 'Comma separated.'],
  ['session_minutes', 'session idle minutes', ''],
  ['max_login_failures', 'failures before lockout', ''],
  ['lockout_seconds', 'lockout seconds', ''],
  ['poll_seconds', 'poll interval (s)', 'How often devices are asked for their state.'],
  ['discovery_seconds', 'scan seconds', ''],
  ['discovery_target', 'scan target', 'Broadcast address for discovery.'],
  ['command_timeout_seconds', 'device timeout (s)', ''],
  ['device_username', 'device account', 'Only needed by newer firmware.'],
  ['device_password', 'device password', 'Leave blank to keep the stored one.'],
  ['scheduler_enabled', 'run schedules', ''],
  ['history_limit', 'activity log size', ''],
  ['metrics_enabled', 'serve /metrics',
    'Prometheus endpoint. It needs no sign-in and publishes no '
    + 'credentials, but it does list your devices; firewall it or turn '
    + 'it off if that matters.'],
  ['log_level', 'log level', 'DEBUG, INFO, WARNING or ERROR.'],
  ['log_file', 'log file', 'Empty logs to the terminal or journal.'],
];

function controlFor(name, value, current, onChange) {
  if (name === 'device_password') {
    return (
      <input type="password" value={current} placeholder="unchanged"
        onChange={(event) => onChange(event.target.value)} />
    );
  }
  if (typeof value === 'boolean') {
    return (
      <input type="checkbox" checked={Boolean(current)}
        onChange={(event) => onChange(event.target.checked)} />
    );
  }
  if (typeof value === 'number') {
    return (
      <input type="number" value={current}
        onChange={(event) => onChange(event.target.value)} />
    );
  }
  return (
    <input type="text" value={current}
      onChange={(event) => onChange(event.target.value)} />
  );
}

function Certificate({certificate}) {
  const watcher = certificate.watcher || {};
  if (certificate.error) {
    return (
      <div className="block certificate">
        <h3 className="engraved">TLS certificate</h3>
        <p className="alarm">{certificate.error}</p>
        <p className="note mono">{certificate.path}</p>
      </div>
    );
  }
  const rows = [
    ['expires in', <Countdown until={certificate.expires} key="left" />],
    ['expires', <span className="mono" key="on">
      {shortTime(certificate.expires)}</span>],
    ['subject', <span className="mono" key="s">
      {certificate.subject || DASH}</span>],
    ['issued by', <span className="mono" key="i">
      {certificate.self_signed
        ? 'itself (self-signed)'
        : certificate.issuer || DASH}</span>],
    ['issued', <span className="mono" key="nb">
      {shortTime(certificate.not_before)}</span>],
  ];
  if ((certificate.names || []).length) {
    rows.push(['valid for', <span className="mono" key="n">
      {certificate.names.join(', ')}</span>]);
  }
  return (
    <div className="block certificate">
      <h3 className="engraved">TLS certificate</h3>
      <table className="table">
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <td className="engraved">{label}</td>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note mono">
        {(certificate.fingerprint || '').replace(/(..)(?=.)/g, '$1:')
          .toUpperCase() || DASH}
      </p>
      <p className="note">
        {watcher.watching
          ? 'Watching the file for a renewal now, and reloading it '
            + 'without a restart when it changes.'
          : 'The file is checked every fifteen minutes over the last '
            + 'two days before expiry, and every minute after it. '
            + 'Renewing early? Reload the service to check at once.'}
        {watcher.reloads
          ? ` Reloaded ${watcher.reloads} time(s) since start-up.`
          : ''}
      </p>
      {watcher.last_error
        ? <p className="alarm">{watcher.last_error}</p>
        : null}
    </div>
  );
}

function FileWarnings({checks}) {
  const broken = checks.filter((check) => !check.ok);
  if (!broken.length) {
    return null;
  }
  return (
    <section className="banner-warn" role="status">
      <h3 className="engraved">
        {broken.length === 1
          ? 'One file the daemon needs is not usable'
          : `${broken.length} files the daemon needs are not usable`}
      </h3>
      <p className="note">
        Checked as the account the daemon is running as, after it dropped
        privileges. Nothing below is failing yet; each one will fail the
        first time the daemon needs it.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>What for</th><th>Path</th><th>Set by</th><th>Trouble</th>
          </tr>
        </thead>
        <tbody>
          {broken.map((check) => (
            <tr key={`${check.purpose}:${check.path}`}>
              <td>{check.purpose}</td>
              <td className="mono">{check.path}</td>
              <td>
                <span className="badge">
                  {check.source === 'configured' ? 'configured' : 'default'}
                </span>
              </td>
              <td>
                {check.problem}
                {check.consequence ? ` — ${check.consequence}` : ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function Settings() {
  const notify = useToast();
  const [payload, setPayload] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const answer = await api.get('/api/settings');
      setPayload(answer);
      const next = {};
      FIELDS.forEach(([name]) => {
        const value = answer.settings[name];
        next[name] = Array.isArray(value) ? value.join(', ')
          : value === undefined ? '' : value;
      });
      next.device_password = '';
      setDraft(next);
    } catch (err) {
      notify(err.message, true);
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  if (!payload) {
    return <p className="empty">Reading settings…</p>;
  }

  const restart = payload.restart_required_fields || [];
  const account = payload.daemon_user || {};

  async function save() {
    setBusy(true);
    const changes = {};
    FIELDS.forEach(([name]) => {
      if (name === 'device_password' && !draft[name]) {
        return;
      }
      changes[name] = draft[name];
    });
    try {
      await api.put('/api/settings', {settings: changes});
      notify('Settings saved.');
      load();
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="split two">
      {account.warning
        ? (
          <p className="banner-alarm" role="alert">
            <strong>Set a daemon user.</strong> {account.warning}
          </p>
        )
        : null}
      <div className="column wide">
        <div className="block settings">
          {FIELDS.map(([name, label, hint]) => (
            <label className="setting" key={name}>
              <span className="engraved">
                {label}
                {restart.includes(name)
                  ? <span className="badge">restart</span>
                  : null}
              </span>
              {controlFor(name, payload.settings[name], draft[name],
                (value) => setDraft({...draft, [name]: value}))}
              {hint ? <span className="note">{hint}</span> : null}
            </label>
          ))}
          <div className="row">
            <button type="button" className="primary" disabled={busy}
              onClick={save}>Save settings</button>
          </div>
        </div>
      </div>

      <div className="column">
        <div className="block">
          <h3 className="engraved">Privileges</h3>
          <table className="table">
            <tbody>
              <tr>
                <td className="engraved">running as</td>
                <td className="mono">
                  {account.name || 'not dropped'}
                  {account.uid === undefined ? '' : ` (uid ${account.uid})`}
                </td>
              </tr>
              <tr>
                <td className="engraved">pam helper</td>
                <td className="mono">
                  {account.privileged_helper
                    ? 'separate privileged process'
                    : 'in this process'}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="note">
            Only the helper keeps the privilege PAM needs. Everything
            else — the web server, the device connections, the JSON
            files — runs as the account above.
          </p>
        </div>

        <div className="block files">
          <h3 className="engraved">Files</h3>
          <table className="table">
            <tbody>
              {Object.entries(payload.paths || {}).map(([name, path]) => (
                <tr key={name}>
                  <td className="engraved">{name}</td>
                  <td className="mono">{path}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            The device password is never sent back to the browser;
            {payload.settings.device_password_set
              ? ' one is stored.' : ' none is stored.'}
          </p>
        </div>

        <Certificate certificate={payload.certificate || {}} />
      </div>

      <FileWarnings checks={payload.file_checks || []} />
    </div>
  );
}
