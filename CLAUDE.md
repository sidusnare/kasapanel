<!--
SPDX-FileCopyrightText: 2026 Kasa Panel contributors

SPDX-License-Identifier: GPL-3.0-or-later

SPDX-FileContributor: Claude Opus 5 (AI-generated)
AI usage declaration: docs/ai-bom.spdx.json
-->

# Working on Kasa Panel

## The policy comes first

[`preamble.txt`](preamble.txt) is the engineering policy for this
project and applies to all code, configuration, data and documentation.
Read it before changing anything. It is also carried verbatim in
[`AI.prompt.md`](AI.prompt.md), which is the prompt that would
regenerate the project; `tests/test_prompt.py` fails if the two copies
drift apart, so edit `preamble.txt` and re-embed rather than editing
one of them.

Two requirements are deliberately met in a different form than their
letter: the prompt record is `AI.prompt.md` rather than
`AI.prompt.txt`, and the configuration is JSON rather than YAML. Both
are explained under "Where this project reads the preamble differently"
in `AI.prompt.md`. **Do not "fix" either one.**

## Before you install anything

The preamble's TOOLING section applies: if a host tool is missing, stop
and ask, naming the package and the install command. Do not reimplement
it, substitute another tool, or install a host tool through `pip` or
`npm`. Dependencies declared in `pyproject.toml` or
`frontend/package.json` are not host tools and this does not apply to
them.

## The version lives in one place

`__version__` in `kasapanel/__init__.py` is the single definition.
`pyproject.toml` reads it through setuptools' dynamic metadata and
`tools/make_sbom.py` parses it. Two copies cannot be derived and so are
checked instead, by `tests/test_version.py`:
`docs/ai-bom.spdx.json` and `frontend/test/fixtures/dashboard.json`.

Releasing means: bump `__version__`, move the `Unreleased` entries in
[`CHANGELOG.md`](CHANGELOG.md) under a new dated heading, update those
two files, regenerate the SBOM, then tag `vMAJOR.MINOR.PATCH`.

Every change goes under `Unreleased` in `CHANGELOG.md` as it is made,
grouped as Keep a Changelog wants, and written for the people who run
the daemon rather than copied from the commit message.

## Checks

Run all of these and report what they actually printed.

```sh
python3 -m unittest discover -s tests -t . -q
python3 -m pylint --rcfile=.pylintrc --score=y kasapanel tools tests
ruff check . && ruff format --check .
reuse lint
yamllint -c .yamllint.yaml .
npm ci && npm run lint:md          # documentation tooling, at the root
npm run diagrams:check             # the C4 blocks still parse
cd frontend && npm ci && npx eslint . && npm run build && npm test
```

Expected: the test suite passes except `test_a_read_only_directory_is_not`,
which fails when the tree sits on a filesystem where the owner can write
a read-only directory anyway; pylint scores 10.00; ruff, `reuse lint`,
yamllint and ESLint are silent; the browser suite is 50/50.

Rebuild `kasapanel/static/bundle.js` with `npm run build` whenever
`frontend/src` changes, and commit it: it is a shipped file.

### The architecture diagrams

[`docs/architecture.md`](docs/architecture.md) is a C4 model, and the
Mermaid blocks in it are the only copy. Nothing is committed to
`build/diagrams/`; GitHub renders the blocks directly. Change the
block, not an image.

Mermaid's C4 renderer is a grid with straight lines, not a router, so
the `UpdateRelStyle` offsets at the foot of most blocks are hand-tuned
to keep edge labels off the boxes. Change a label's text and its offset
probably needs moving too; render and look before pushing. Keep each
diagram small -- a component diagram with a dozen boxes renders as a
thicket, which is why the component level is six diagrams and not one.

Mermaid keeps one arrow per pair of shapes: two `Rel` lines between
the same two names render as a single arrow with the later label,
while the step numbers count past the one that vanished. A self-loop
counts, so a component cannot be shown doing two separate things to
itself. `tests/test_diagrams.py` fails on that now.

`tests/test_diagrams.py` catches what needs no browser: an unnamed
block, a duplicate name, a duplicated pair, a missing C4 level. `npm run diagrams:check`
is the one that actually renders.

### Two npm projects

There are two, and they are not interchangeable. The root
`package.json` holds documentation tooling only -- markdownlint and
mermaid-cli -- and `frontend/package.json` holds the browser
application. `npm ci` in one
does nothing for the other, so the checks above run it twice.

ESLint is held at the 9 series because `eslint-plugin-react`
peer-depends on ESLint 9 or older. That is a plugin constraint and has
nothing to do with the Node version; do not "upgrade" past it and lose
the JSX rules, which is what stops `no-unused-vars` firing on every
component imported for use in JSX.
