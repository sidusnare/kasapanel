// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {createContext, useCallback, useContext, useMemo,
  useRef, useState} from 'react';

const ToastContext = createContext(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({children}) {
  const [items, setItems] = useState([]);
  const next = useRef(1);

  const notify = useCallback((message, bad = false) => {
    const id = next.current;
    next.current += 1;
    setItems((current) => [...current, {id, message, bad}]);
    window.setTimeout(() => {
      setItems((current) => current.filter((item) => item.id !== id));
    }, bad ? 8000 : 4000);
  }, []);

  const value = useMemo(() => notify, [notify]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={item.bad ? 'toast bad' : 'toast'}>
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
