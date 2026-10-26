// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React from 'react';

// A two-position rocker switch.  The stylesheet keys off aria-pressed,
// so the accessible state and the visible state cannot drift apart.
//
// It takes a draggable flag because the card header it sits in is
// draggable: a hand on the rocker is there to throw it, not to move the
// card, and a drag that starts here is a drag nobody meant.

export default function Rocker({on, busy, disabled, onToggle, name,
  draggable}) {
  return (
    <button
      type="button"
      className="rocker"
      draggable={draggable}
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
