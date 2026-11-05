// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Claude Opus 5 (AI-generated)
// AI usage declaration: docs/ai-bom.spdx.json

// ESLint configuration for the browser sources, the build script and the
// jsdom test suite.  Three groups, because they run in different places:
// src/ is a browser bundle, build.mjs and test/ are Node.
//
// eslint-plugin-react earns its place here: without jsx-uses-vars, core
// no-unused-vars cannot see that a component named only inside JSX is
// used, and reports every import in every view as dead.

import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';

export default [
  {
    // node_modules is restored by npm, and the bundle is generated.
    ignores: ['node_modules/**', 'dist/**'],
  },
  js.configs.recommended,
  {
    files: ['src/**/*.js', 'src/**/*.jsx'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
      parserOptions: {ecmaFeatures: {jsx: true}},
    },
    plugins: {react},
    settings: {react: {version: '18.3'}},
    rules: {
      ...react.configs.flat.recommended.rules,
      // The panel passes plain JSON from the API straight into its
      // components and validates it server side; prop-types would
      // restate the API schema in a second place.
      'react/prop-types': 'off',
    },
  },
  {
    files: ['build.mjs', 'test/**/*.mjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.node,
    },
  },
];
