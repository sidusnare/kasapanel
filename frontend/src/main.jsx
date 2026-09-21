// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.jsx';
import {ToastProvider} from './components/Toasts.jsx';

const mount = document.getElementById('root');
createRoot(mount).render(
  <ToastProvider>
    <App />
  </ToastProvider>,
);
