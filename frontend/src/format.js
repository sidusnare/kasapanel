// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// Small display helpers.  The daemon sends ISO timestamps without a zone
// because everything it does is in the host's local time.

export const DASH = '\u2014';

export function shortTime(text) {
  if (!text) {
    return DASH;
  }
  return String(text).replace('T', ' ').slice(0, 16);
}

export function clockTime(text) {
  if (!text) {
    return DASH;
  }
  return String(text).slice(11, 16);
}

// The daemon sends its clock as a naive local time with the zone beside
// it, so the panel can show the host's own reading and, when the browser
// keeps a different clock, the same instant as the viewer sees it.

export function offsetMinutes(text) {
  const match = /^([+-])(\d{2}):?(\d{2})$/.exec(String(text || '').trim());
  if (!match) {
    return null;
  }
  const size = Number(match[2]) * 60 + Number(match[3]);
  return match[1] === '-' ? -size : size;
}

export function hostInstant(summary) {
  const stamp = String((summary && summary.server_time) || '');
  const offset = offsetMinutes(summary && summary.server_utc_offset);
  if (!stamp || offset === null) {
    return null;
  }
  const date = new Date(`${stamp}${summary.server_utc_offset}`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function hostClock(summary, instant) {
  const zone = (summary && summary.server_timezone) || '';
  const date = instant || hostInstant(summary);
  if (!date) {
    // An older daemon, or one that sent nothing: show what arrived.
    const stamp = String((summary && summary.server_time) || '');
    return stamp ? stamp.slice(11, 19) : DASH;
  }
  const minutes = offsetMinutes(summary.server_utc_offset);
  const shifted = new Date(date.getTime() + minutes * 60000);
  const clock = shifted.toISOString().slice(11, 19);
  return zone ? `${clock} ${zone}` : clock;
}

// Empty when this browser keeps the same clock as the daemon, since
// repeating the same reading twice tells nobody anything.
export function browserClock(summary, instant) {
  const date = instant || hostInstant(summary);
  const minutes = offsetMinutes(summary && summary.server_utc_offset);
  if (!date || minutes === null || -date.getTimezoneOffset() === minutes) {
    return '';
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
      timeZoneName: 'short',
    }).format(date);
  } catch (err) {
    return '';  // No Intl data: the host reading still stands on its own.
  }
}

export function watts(value) {
  if (value === null || value === undefined || value === '') {
    return DASH;
  }
  return `${Number(value).toFixed(1)} W`;
}

export function stateWord(device) {
  const state = device.state || {};
  if (!device.enabled) {
    return {word: 'disabled', tone: 'off', card: 'is-disabled'};
  }
  if (state.reachable === false) {
    return {word: 'no answer', tone: 'fault', card: 'is-fault'};
  }
  if (state.reachable === undefined) {
    return {word: 'unknown', tone: 'off', card: 'is-off'};
  }
  return state.is_on
    ? {word: 'energised', tone: 'live', card: 'is-live'}
    : {word: 'off', tone: 'off', card: 'is-off'};
}

export function deviceSubtitle(device) {
  const bits = [device.host];
  if (device.model) {
    bits.push(device.model);
  }
  if (device.device_type) {
    bits.push(device.device_type);
  }
  return bits.filter(Boolean).join(' \u00b7 ');
}
