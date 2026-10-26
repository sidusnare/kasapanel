// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// Light or dark, written on the root element for the stylesheet to key
// off.  The daemon's per-user file is the authority, but it cannot be
// read until the session is, so the last theme this browser saw is kept
// locally as well: without it every load of a dark panel would start
// with a flash of the light one.  The local copy is a hint and nothing
// more, which is why it is allowed to be wrong for the first moment
// after somebody else signs in on the same browser.

const STORE_KEY = 'kasapanel.theme';

export const THEMES = ['light', 'dark'];

export function cleanTheme(value, fallback = 'light') {
  return THEMES.includes(value) ? value : fallback;
}

export function rememberedTheme() {
  try {
    return cleanTheme(window.localStorage.getItem(STORE_KEY));
  } catch (err) {
    return 'light';  // Private mode, or storage disabled.
  }
}

export function rememberTheme(theme) {
  try {
    window.localStorage.setItem(STORE_KEY, cleanTheme(theme));
  } catch (err) {
    // Not fatal; the theme still applies for this page load.
  }
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (root) {
    root.setAttribute('data-theme', cleanTheme(theme));
  }
}
