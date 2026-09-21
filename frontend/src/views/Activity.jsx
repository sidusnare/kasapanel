// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useCallback, useEffect, useState} from 'react';
import {api} from '../api.js';
import {useToast} from '../components/Toasts.jsx';
import {DASH, shortTime} from '../format.js';

export default function Activity() {
  const notify = useToast();
  const [records, setRecords] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [limit, setLimit] = useState(100);

  const load = useCallback(async () => {
    try {
      const [log, who] = await Promise.all([
        api.get(`/api/activity?limit=${limit}`),
        api.get('/api/sessions'),
      ]);
      setRecords(log.activity || []);
      setSessions(who.sessions || []);
    } catch (err) {
      notify(err.message, true);
    }
  }, [limit, notify]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="split two">
      <div className="column wide">
        <div className="block">
          <div className="row">
            <h3 className="engraved">Activity</h3>
            <select value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}>
              <option value={50}>last 50</option>
              <option value={100}>last 100</option>
              <option value={500}>last 500</option>
            </select>
            <button type="button" className="quiet" onClick={load}>
              Refresh
            </button>
          </div>
          {records.length === 0
            ? <p className="empty">Nothing logged yet.</p>
            : (
              <table className="table">
                <thead>
                  <tr><th>When</th><th>Who</th><th>What</th></tr>
                </thead>
                <tbody>
                  {records.map((record) => (
                    <tr key={record.id}>
                      <td className="mono">{shortTime(record.at)}</td>
                      <td>{record.source || DASH}</td>
                      <td className={record.level === 'info'
                        ? '' : 'alarm'}>{record.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      </div>

      <div className="column">
        <div className="block">
          <h3 className="engraved">Signed in now</h3>
          <table className="table">
            <tbody>
              {sessions.map((session, index) => (
                <tr key={index}>
                  <td>{session.username}</td>
                  <td className="mono">{session.remote}</td>
                  <td className="mono">{shortTime(session.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
