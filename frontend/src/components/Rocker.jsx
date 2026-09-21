// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React from 'react';

// A two-position rocker switch.  The stylesheet keys off aria-pressed,
// so the accessible state and the visible state cannot drift apart.

export default function Rocker({on, busy, disabled, onToggle, name}) {
  return (
    <button
      type="button"
      className="rocker"
      aria-pressed={Boolean(on)}
      aria-label={`${on ? 'Switch off' : 'Switch on'} ${name}`}
      disabled={Boolean(busy || disabled)}
      onClick={onToggle}
    >
      <span className="rocker-face" />
      <span className="rocker-lamp" />
    </button>
  );
}
