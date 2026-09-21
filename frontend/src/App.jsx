// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useCallback, useEffect, useRef, useState} from 'react';
import {ApiError, api, clearCredentials, setCredentials} from './api.js';
import SignIn from './SignIn.jsx';
import {useToast} from './components/Toasts.jsx';
import {DASH, clockTime, watts} from './format.js';
import Activity from './views/Activity.jsx';
import Devices from './views/Devices.jsx';
import Panel from './views/Panel.jsx';
import Schedules from './views/Schedules.jsx';
import Settings from './views/Settings.jsx';

const REFRESH_MS = 5000;

const TABS = [
  ['panel', 'Panel'],
  ['schedules', 'Schedules'],
  ['devices', 'Devices'],
  ['activity', 'Activity'],
  ['settings', 'Settings'],
];

function Readouts({dashboard}) {
  const summary = (dashboard && dashboard.summary) || {};
  const scheduler = (dashboard && dashboard.scheduler) || {};
  return (
    <header className="readouts">
      <div className="readout">
        <span className="engraved">Devices</span>
        <b>{summary.total === undefined ? DASH : summary.total}</b>
      </div>
      <div className="readout">
        <span className="engraved">Answering</span>
        <b>{summary.online === undefined ? DASH : summary.online}</b>
      </div>
      <div className="readout">
        <span className="engraved">Energised</span>
        <b>{summary.on === undefined ? DASH : summary.on}</b>
      </div>
      <div className="readout">
        <span className="engraved">Draw</span>
        <b>{summary.power_watts ? watts(summary.power_watts) : DASH}</b>
      </div>
      <div className="readout right">
        <span className="engraved">Scheduler</span>
        <b>{scheduler.running ? 'running' : 'stopped'}</b>
      </div>
      <div className="readout">
        <span className="engraved">Host clock</span>
        <b className="mono">{clockTime(summary.server_time)}</b>
      </div>
    </header>
  );
}

export default function App() {
  const notify = useToast();
  const [ready, setReady] = useState(false);
  const [username, setUsername] = useState('');
  const [signInMessage, setSignInMessage] = useState('');
  const [pamError, setPamError] = useState('');
  const [view, setView] = useState('panel');
  const [dashboard, setDashboard] = useState(null);
  const [reference, setReference] = useState(null);
  const timer = useRef(null);
  const steered = useRef(false);

  const signOutLocally = useCallback((message) => {
    clearCredentials();
    steered.current = false;
    setUsername('');
    setDashboard(null);
    setSignInMessage(message || '');
  }, []);

  const loadDashboard = useCallback(async () => {
    try {
      const answer = await api.get('/api/dashboard');
      setDashboard(answer);
      // A daemon running as the last-resort account needs attention
      // before anything else, so open on the settings tab once.
      if (!steered.current) {
        steered.current = true;
        if (answer.daemon_user && answer.daemon_user.last_resort) {
          setView('settings');
        }
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        signOutLocally('That session is no longer valid. Sign in again.');
        return;
      }
      // A blip while polling is not worth shouting about; the next tick
      // will either recover or the message will repeat.
      if (err.status === 0) {
        setDashboard((current) => current);
      }
    }
  }, [signOutLocally]);

  // Boot: ask the daemon whether this browser is already signed in.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const answer = await api.get('/api/session');
        if (cancelled) {
          return;
        }
        if (answer.signed_in) {
          setCredentials(undefined, answer.csrf_token);
          setUsername(answer.username);
        } else {
          setPamError(answer.pam_error || '');
          clearCredentials();
        }
      } catch (err) {
        setSignInMessage(err.message);
      } finally {
        if (!cancelled) {
          setReady(true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Poll the dashboard while signed in and the tab is visible.
  useEffect(() => {
    if (!username) {
      return undefined;
    }
    loadDashboard();
    api.get('/api/reference').then(setReference).catch(() => {});
    timer.current = window.setInterval(() => {
      if (!document.hidden) {
        loadDashboard();
      }
    }, REFRESH_MS);
    return () => window.clearInterval(timer.current);
  }, [username, loadDashboard]);

  async function signIn(name, password) {
    const answer = await api.post('/api/session',
      {username: name, password});
    setCredentials(answer.session_token, answer.csrf_token);
    setSignInMessage('');
    setUsername(answer.username);
  }

  async function signOut() {
    try {
      await api.remove('/api/session');
    } catch (err) {
      // Already gone as far as the daemon is concerned.
    }
    signOutLocally('Signed out.');
  }

  function replaceDevice(device) {
    if (!device) {
      return;
    }
    setDashboard((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        devices: current.devices.map(
          (item) => (item.device_id === device.device_id ? device : item)),
      };
    });
  }

  if (!ready) {
    return <main className="boot">Starting…</main>;
  }

  if (!username) {
    return (
      <SignIn
        message={signInMessage}
        pamError={pamError}
        onSubmit={signIn}
      />
    );
  }

  const devices = (dashboard && dashboard.devices) || [];

  return (
    <div className="shell">
      <aside className="rail">
        <p className="brand">Kasa<b>Panel</b></p>
        <nav id="nav">
          {TABS.map(([name, label]) => (
            <button
              key={name}
              type="button"
              className={name === view ? 'tab is-current' : 'tab'}
              onClick={() => setView(name)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          <p className="engraved">Signed in</p>
          <p className="mono">{username}</p>
          <button type="button" className="quiet" onClick={signOut}>
            Sign out
          </button>
        </div>
      </aside>

      <main>
        <Readouts dashboard={dashboard} />
        <section className="view is-current">
          {view === 'panel'
            ? <Panel dashboard={dashboard} onChanged={replaceDevice} />
            : null}
          {view === 'schedules'
            ? (
              <Schedules
                devices={devices}
                reference={reference}
                onSaved={loadDashboard}
              />
            )
            : null}
          {view === 'devices'
            ? <Devices devices={devices} onChanged={loadDashboard} />
            : null}
          {view === 'activity' ? <Activity /> : null}
          {view === 'settings' ? <Settings /> : null}
        </section>
      </main>
    </div>
  );
}
