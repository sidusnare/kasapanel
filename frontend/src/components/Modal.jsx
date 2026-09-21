// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// A plain modal dialog: a plate over a dimmed cabinet.  Escape and a
// click on the backdrop both close it, and focus goes to the first field
// so a keyboard user can start typing at once.  It is drawn into the
// body rather than where it is used, so a dimmed disabled card does not
// dim its dialog too.

import React, {useEffect, useRef} from 'react';
import {createPortal} from 'react-dom';

export default function Modal({title, onClose, children}) {
  const plate = useRef(null);
  // Held in a ref so the effect below runs once: the parent re-renders
  // on every dashboard poll, and re-running it would steal focus back
  // to the first field in the middle of typing.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const first = plate.current
      && plate.current.querySelector('input, select, textarea, button');
    if (first) {
      first.focus();
    }
    function onKey(event) {
      if (event.key === 'Escape') {
        close.current();
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="modal plate" role="dialog" aria-modal="true"
        aria-label={title} ref={plate}>
        <h2 className="engraved">{title}</h2>
        {children}
      </div>
    </div>,
    document.body,
  );
}
