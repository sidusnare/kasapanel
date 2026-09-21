// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// Loads the built bundle into a jsdom document, the way a browser does.

import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {JSDOM, VirtualConsole} from 'jsdom';
import {createServer} from './server.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.join(here, '..', '..', 'kasapanel', 'static');

export async function mount({acceptCookie = true, lastResort = false} = {}) {
  const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
  const bundle = fs.readFileSync(path.join(staticDir, 'bundle.js'), 'utf8');
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (err) => errors.push(String(err.message)));
  virtualConsole.on('error', (...args) => errors.push(args.join(' ')));

  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'https://panel.test/',
    pretendToBeVisual: true,
    virtualConsole,
  });
  const {window} = dom;
  const server = createServer({acceptCookie, lastResort});
  window.fetch = (url, options = {}) => server.handle(url, options);
  window.addEventListener('error', (event) => errors.push(event.message));

  const script = window.document.createElement('script');
  script.textContent = bundle;
  window.document.body.appendChild(script);
  await settle(window);

  return {
    window,
    document: window.document,
    server,
    errors,
    settle: () => settle(window),
    close: () => dom.window.close(),
  };
}

export function settle(window, ticks = 6) {
  return new Promise((resolve) => {
    let left = ticks;
    const step = () => {
      left -= 1;
      if (left <= 0) {
        resolve();
      } else {
        window.setTimeout(step, 0);
      }
    };
    window.setTimeout(step, 0);
  });
}

export function text(node) {
  return (node ? node.textContent : '').replace(/\s+/g, ' ').trim();
}

export function findByText(document, selector, needle) {
  return [...document.querySelectorAll(selector)]
    .find((node) => text(node).toLowerCase().includes(needle.toLowerCase()));
}
