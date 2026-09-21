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
