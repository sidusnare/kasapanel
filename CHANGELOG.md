<!--
SPDX-FileCopyrightText: 2026 Kasa Panel contributors

SPDX-License-Identifier: GPL-3.0-or-later

SPDX-FileContributor: Claude Opus 5 (AI-generated)
AI usage declaration: docs/ai-bom.spdx.json
-->

# Changelog

All notable changes to Kasa Panel are recorded here, written for the
people who run it rather than copied from commit messages.

The format follows [Keep a Changelog 1.1.0][kac], and the project uses
[Semantic Versioning 2.0.0][semver] against the public API documented
under "Public API" in the README: the command line, the configuration
file, the on-disk document formats, the HTTP API, the environment
variables it reads and the exit codes it returns.

Releases before 1.8.0 were made before this file existed. Their history
is in the git log and is not reconstructed here, because entries
invented after the fact would read as though somebody had checked them.

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

### Added

- `docs/architecture.md`, a C4 model of the daemon: the system in
  context, its containers, its components over four views, two
  sequences worth following, and the deployment. The diagrams are
  Mermaid blocks in the document itself, so GitHub renders them where
  they are read and there is no second copy to fall out of date.
- A diagrams workflow that renders those blocks to SVG and PNG on every
  push and uploads them, so there are image files for slides and
  offline copies. It fails when a block stops parsing, which is the
  failure that would otherwise land unnoticed.
- `npm run diagrams` and `npm run diagrams:check` for the same job
  locally, through `tools/render_diagrams.py`.

## [1.8.3] - 2026-11-05

A housekeeping release: no behaviour changes, so nothing an operator
does differs. It adopts the project's written engineering policy across
the tree and fixes two faults that the policy's linters found.

### Added

- `CHANGELOG.md`, this file.
- A **Public API** section in the README, listing the command line
  options, the environment variables, the exit codes and the on-disk
  document formats. Semantic Versioning is defined against a public
  API, so the API has to be written down for the version numbers to
  mean anything.
- `preamble.txt`, the engineering policy the project is held to, now
  carried verbatim inside `AI.prompt.md` and pointed at by `CLAUDE.md`.
- Linter and formatter configuration, all committed: `ruff` settings in
  `pyproject.toml`, `frontend/eslint.config.mjs`, `.markdownlint.yaml`,
  `.markdownlint-cli2.yaml` and `.yamllint.yaml`.
- A small `package.json` at the top of the tree, holding documentation
  tooling only: markdownlint, which lints every Markdown file in the
  repository. It is a separate npm project from `frontend/`, which is
  the browser application, so `npm ci` runs in both.
- `LICENSES/` with the full text of every licence the tree refers to,
  and `REUSE.toml` covering the files that cannot carry a comment.
- `tests/test_version.py`, which fails if any copy of the version drifts
  from the single definition.
- An `engines` field in both `package.json` files declaring the
  supported Node versions, so `npm` refuses an unsupported one instead
  of installing a test harness that cannot start.

### Changed

- The version is defined once, as `__version__` in
  `kasapanel/__init__.py`. `pyproject.toml` reads it through
  setuptools' dynamic metadata and the SBOM tool parses it, so a
  release now means editing one line rather than six.
- Every `SPDX-FileContributor` header names the model and its version,
  in the form the policy requires.
- The Python sources are formatted with `ruff format` at 80 columns and
  single quotes, and their type annotations use the built-in generics
  and `X | None` form that the supported Python versions provide.
- The generated `bundle.js` carries a complete REUSE header rather than
  a bare licence identifier.
- `jsdom` is on the 31 series, and the browser tests need Node 22.22,
  24.15 or 26 or newer, whichever line you are on. They are developed
  and tested on 26.
- `esbuild`'s install script is declined explicitly, in `allowScripts`.
  The bundle builds without it, so there is no reason to run it.

### Removed

- `frontend/node_modules` is no longer committed. It is restored with
  `npm ci`, and the dependency licences are recorded in the SBOM, where
  a scanner can read them. The committed copy had also drifted out of
  step with `package-lock.json`, which is what hid the `jsdom` fault
  described under Fixed.
- The `version` field in `frontend/package.json`, which duplicated the
  project version for a package that is never published.

### Fixed

- The browser test suite no longer fails for half the year. Its fake
  daemon read the host's UTC offset at the moment the test ran, while
  the recorded dashboard it serves is timestamped in July; on a host
  that keeps summer time the two disagreed, and the panel was told the
  browser sat in a different time zone than the daemon. The offset is
  now read at the instant the fixture describes. Verified in UTC and in
  summer-time zones in both hemispheres.
- A storage test asserted that saving a bad schedule raised
  `Exception`, which it would also have done had the call failed for an
  unrelated reason. It now asserts `ScheduleError`.
- Long lines and unlabelled code fences throughout the documentation.

## [1.8.2] - 2026-10-26

### Fixed

- The certificate card in Settings stacks its fields instead of letting
  a long path run past the edge of the card.

## [1.8.1] - 2026-10-26

### Fixed

- Long file paths in the Files card wrap instead of overflowing.
- The sign-out row stays visible at the foot of the rail when the
  device list is long enough to scroll.

## [1.8.0] - 2026-10-26

### Added

- Panel cards can be reordered by dragging them, and each operator's
  order is kept in a file of their own.
- A dark theme, chosen per operator and remembered with their card
  order.

<!--
The comparison links below follow the repository host's usual form.
The Source URL in pyproject.toml is still a placeholder, so they will
resolve once the project has a real home.
-->

[Unreleased]: https://example.invalid/kasapanel/compare/v1.8.3...HEAD
[1.8.3]: https://example.invalid/kasapanel/compare/v1.8.2...v1.8.3
[1.8.2]: https://example.invalid/kasapanel/compare/v1.8.1...v1.8.2
[1.8.1]: https://example.invalid/kasapanel/compare/v1.8.0...v1.8.1
[1.8.0]: https://example.invalid/kasapanel/releases/tag/v1.8.0
