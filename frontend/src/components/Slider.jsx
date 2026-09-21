// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useEffect, useState} from 'react';

// A slider that only tells the daemon once the user lets go, so dragging
// does not fire a command for every pixel.

export default function Slider({label, min, max, step, value, unit,
  disabled, onCommit}) {
  const [local, setLocal] = useState(value);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) {
      setLocal(value);
    }
  }, [value, dragging]);

  return (
    <label className="row">
      <span className="engraved">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step || 1}
        value={local === null || local === undefined ? min : local}
        disabled={disabled}
        onChange={(event) => {
          setDragging(true);
          setLocal(Number(event.target.value));
        }}
        onMouseUp={() => { setDragging(false); onCommit(local); }}
        onTouchEnd={() => { setDragging(false); onCommit(local); }}
        onKeyUp={() => { setDragging(false); onCommit(local); }}
      />
      <span className="mono">{local}{unit}</span>
    </label>
  );
}
