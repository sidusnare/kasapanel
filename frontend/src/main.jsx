// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Claude Opus 5 (AI-generated)
// AI usage declaration: docs/ai-bom.spdx.json

import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.jsx';
import {ToastProvider} from './components/Toasts.jsx';
import {applyTheme, rememberedTheme} from './theme.js';

// Before the first paint, so a dark panel does not start light.  The
// daemon's answer, when it arrives, is what decides from then on.
applyTheme(rememberedTheme());

const mount = document.getElementById('root');
createRoot(mount).render(
  <ToastProvider>
    <App />
  </ToastProvider>,
);
