// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useCallback, useEffect, useState} from 'react';
import {api} from '../api.js';
import ConfirmLock, {lockOutcome} from '../components/ConfirmLock.jsx';
import {useToast} from '../components/Toasts.jsx';
import {shortTime} from '../format.js';

function Findings({summary}) {
  if (!summary) {
    return null;
  }
  const problems = summary.problems || [];
  if (problems.length) {
    return (
      <ul className="findings">
        {problems.map((problem, index) => (
          <li key={index} className="alarm">
            line {problem.line_number}: {problem.message}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p className="note">
      {summary.rule_count} rule(s), no problems found.
    </p>
  );
}

function NextRuns({summary}) {
  const runs = (summary && summary.next_runs) || [];
  if (!runs.length) {
    return <p className="empty">Nothing scheduled to run.</p>;
  }
  return (
    <ol className="ticks">
      {runs.map((run, index) => (
        <li key={index}>
          <span className="tick-when mono">{shortTime(run.when)}</span>
          <span className="tick-command mono">{run.command}</span>
          <span className="tick-line engraved">line {run.line_number}</span>
        </li>
      ))}
    </ol>
  );
}

function Reference({reference}) {
  if (!reference) {
    return null;
  }
  return (
    <div className="block">
      <h3 className="engraved">The language</h3>
      <p className="note">
        One rule per line: a schedule, an action, then any arguments.
        Times use the daemon host clock, to the minute.
      </p>
      <table className="table">
        <tbody>
          {(reference.actions || []).map((action) => (
            <tr key={action.name}>
              <td className="mono">{action.usage}</td>
              <td>{action.summary}</td>
            </tr>
          ))}
          {(reference.keywords || []).map((keyword) => (
            <tr key={keyword.name}>
              <td className="mono">{keyword.name}</td>
              <td className="mono">{keyword.expands_to || 'at start up'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Schedules({devices, reference, onSaved}) {
  const notify = useToast();
  const [selected, setSelected] = useState('');
  const [script, setScript] = useState('');
  const [enabled, setEnabled] = useState(true);
  // What the daemon last told us about the checkbox, so the question a
  // locked device asks can name what is actually changing.
  const [stored, setStored] = useState(true);
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState(null);

  const device = devices.find((item) => item.device_id === selected);

  const load = useCallback(async (deviceId) => {
    if (!deviceId) {
      return;
    }
    try {
      const answer = await api.get(`/api/devices/${deviceId}/schedule`);
      setScript(answer.schedule || '');
      setEnabled(Boolean(answer.schedule_enabled));
      setStored(Boolean(answer.schedule_enabled));
      setSummary(answer.summary || null);
    } catch (err) {
      notify(err.message, true);
    }
  }, [notify]);

  useEffect(() => {
    if (!selected && devices.length) {
      const first = devices[0].device_id;
      setSelected(first);
      load(first);
    }
  }, [devices, selected, load]);

  async function check() {
    setBusy(true);
    try {
      const answer = await api.post('/api/schedule/check', {schedule: script});
      setSummary(answer.summary);
      notify(answer.summary.valid
        ? 'The schedule reads cleanly.'
        : 'The schedule has problems; nothing was saved.',
      !answer.summary.valid);
    } catch (err) {
      notify(err.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function saveRun(confirmation) {
    setBusy(true);
    try {
      const answer = await api.put(`/api/devices/${selected}/schedule`, {
        schedule: script,
        schedule_enabled: enabled,
        ...(confirmation || {}),
      });
      setSummary(answer.summary);
      setStored(Boolean(answer.schedule_enabled));
      notify('Schedule saved.');
      onSaved();
      return {done: true};
    } catch (err) {
      if (err.payload && err.payload.summary) {
        setSummary(err.payload.summary);
      }
      return lockOutcome(err);
    } finally {
      setBusy(false);
    }
  }

  // Saving, enabling and disabling all arrive here, because they all
  // leave through the one request.  A locked device asks first, and the
  // question names whichever of the three this is.
  async function save() {
    const name = (device && device.display_name) || 'this device';
    let question = `Save the schedule for ${name}?`;
    if (enabled !== stored) {
      question = enabled
        ? `Enable the schedule for ${name}?`
        : `Disable the schedule for ${name}?`;
    }
    if (device && device.locked) {
      setAsk({question, run: saveRun});
      return;
    }
    const outcome = await saveRun();
    if (outcome.done) {
      return;
    }
    if (outcome.locked) {
      setAsk({question, run: saveRun});
      return;
    }
    notify(outcome.error, true);
  }

  if (!devices.length) {
    return <p className="empty">Add a device before writing a schedule.</p>;
  }

  // Three columns: the device picker, then the editor with the language
  // beneath it where it can be read while typing, then the preview of
  // what the script will do.
  return (
    <div className="split schedules">
      <div className="column">
        <div className="picker">
          {devices.map((item) => (
            <button
              key={item.device_id}
              type="button"
              className={item.device_id === selected ? 'is-current' : ''}
              onClick={() => { setSelected(item.device_id);
                load(item.device_id); }}
            >
              {item.display_name}
              {item.locked
                ? (
                  <span className="pick-lock" role="img" aria-label="locked">
                    {'\u{1F512}'}
                  </span>
                )
                : null}
            </button>
          ))}
        </div>
      </div>

      <div className="column editor">
        <label className="field">
          <span className="engraved">schedule</span>
          <textarea
            className="mono"
            rows={16}
            spellCheck={false}
            value={script}
            onChange={(event) => setScript(event.target.value)}
          />
        </label>

        <label className="toggle-line">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>Run this schedule</span>
        </label>

        <div className="row">
          <button type="button" className="primary" disabled={busy}
            onClick={save}>Save</button>
          <button type="button" className="quiet" disabled={busy}
            onClick={check}>Check without saving</button>
        </div>

        <Findings summary={summary} />
        <Reference reference={reference} />
        {ask
          ? (
            <ConfirmLock
              title={`${(device && device.display_name) || 'This device'} `
                + 'is locked'}
              question={ask.question}
              onConfirm={
                (password) => ask.run({confirm: true, password})}
              onClose={() => setAsk(null)}
            />
          )
          : null}
      </div>

      <div className="column firings">
        <h3 className="engraved">Next five firings</h3>
        <NextRuns summary={summary} />
      </div>
    </div>
  );
}
