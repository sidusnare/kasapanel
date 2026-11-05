# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
"""Renders the C4 diagrams in docs/architecture.md to image files.

The Markdown file is the only copy of each diagram.  GitHub renders the
fenced blocks where they stand, so nothing here is committed; this
script exists to produce files for slides and offline documents, and to
fail a build when a block stops parsing.

Each block is named by the ``<!-- diagram: name -->`` comment above it.
mermaid-cli will take a whole Markdown file, but then it numbers the
output and says only that something failed, which is no help with eight
diagrams in one file.  So the blocks are pulled out and handed over one
at a time.

Run it through npm, which puts mermaid-cli on the path:

    npm ci && npm run diagrams
"""

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'docs' / 'architecture.md'
DEFAULT_OUT = ROOT / 'build' / 'diagrams'
MMDC = ROOT / 'node_modules' / '.bin' / 'mmdc'

# A diagram is a named comment followed by a mermaid block.  An unnamed
# block is skipped rather than guessed at, and reported at the end.
BLOCK = re.compile(
    r'<!--\s*diagram:\s*(?P<name>[\w.-]+)\s*-->\s*\n'
    r'```mermaid\n(?P<body>.*?)\n```',
    re.DOTALL,
)

# Mermaid's C4 theme asks for Open Sans and then embeds the whole
# typeface as twenty @font-face rules: about 350 kB of base64 in every
# file, and Open Sans's Apache-2.0 licence pulled into a GPL document
# for nothing.  Naming a stack every machine already has makes the
# embedded copy unreferenced, and strip_fonts then drops it.
STACK = 'verdana, arial, sans-serif'

# Every C4 shape carries its own font setting, and one left out keeps
# Open Sans and so keeps the embedded copy.  The names are Mermaid's
# own; they are built here rather than written out twenty-two times.
C4_SHAPES = (
    'person',
    'external_person',
    'system',
    'external_system',
    'system_db',
    'external_system_db',
    'system_queue',
    'external_system_queue',
    'container',
    'external_container',
    'container_db',
    'external_container_db',
    'container_queue',
    'external_container_queue',
    'component',
    'external_component',
    'component_db',
    'external_component_db',
    'component_queue',
    'external_component_queue',
    'boundary',
    'message',
)

MERMAID_CONFIG = {
    'fontFamily': STACK,
    'c4': {f'{shape}FontFamily': STACK for shape in C4_SHAPES},
}

# Chromium refuses its sandbox as root, which is how CI runs it.  The
# page is a local file this script just wrote, so there is nothing here
# the sandbox would be protecting.
PUPPETEER_CONFIG = {'args': ['--no-sandbox', '--disable-dev-shm-usage']}

FONT_FACE = re.compile(r'@font-face\s*\{[^}]*\}')
CSS_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
EMBEDDED = 'data:font/woff2'


class RenderError(Exception):
    """Raised when a diagram cannot be rendered."""


def read_diagrams(path: pathlib.Path) -> list[tuple[str, str, int]]:
    """Pulls the named mermaid blocks out of a Markdown file.

    Args:
        path: The Markdown file to read.

    Returns:
        One tuple per diagram: its name, its mermaid source, and the
        line in the Markdown file where its block starts.

    Raises:
        RenderError: If the file holds no named block, or names one
            twice, since the name becomes a file name.
    """
    text = path.read_text(encoding='utf-8')
    found = []
    seen = set()
    for match in BLOCK.finditer(text):
        name = match.group('name')
        if name in seen:
            raise RenderError(f'{path}: two diagrams are named {name}')
        seen.add(name)
        line = text.count('\n', 0, match.start()) + 1
        found.append((name, match.group('body'), line))
    if not found:
        raise RenderError(f'{path}: no diagram comment and mermaid block')
    unnamed = text.count('```mermaid') - len(found)
    if unnamed:
        raise RenderError(
            f'{path}: {unnamed} mermaid block(s) have no '
            f'<!-- diagram: name --> comment above them'
        )
    return found


def strip_fonts(svg: str) -> str:
    """Drops the embedded typeface from a rendered SVG.

    Mermaid embeds Open Sans whether or not anything asks for it.  With
    the font stack in MERMAID_CONFIG in force nothing does, so the
    rules and the licence comment that comes with them are dead weight.

    Args:
        svg: The SVG as mermaid-cli wrote it.

    Returns:
        The SVG without the embedded font, or unchanged when something
        still refers to it -- a later Mermaid may go back to using it,
        and a diagram that renders large is better than one whose text
        no longer fits its boxes.
    """
    if EMBEDDED not in svg:
        return svg
    pruned = FONT_FACE.sub('', svg)
    if 'Open Sans' in pruned:
        return svg
    return CSS_COMMENT.sub(
        lambda m: '' if 'font' in m.group(0).lower() else m.group(0), pruned
    )


def render_one(
    source: pathlib.Path, target: pathlib.Path, configs: dict
) -> None:
    """Renders one mermaid file to one image.

    Args:
        source: The ``.mmd`` file to read.
        target: The image to write.  Its suffix picks the format.
        configs: Paths of the mermaid and puppeteer config files, keyed
            ``mermaid`` and ``puppeteer``.

    Raises:
        RenderError: If mermaid-cli fails, with its own output.
    """
    command = [
        str(MMDC),
        '--input',
        str(source),
        '--output',
        str(target),
        '--configFile',
        str(configs['mermaid']),
        '--puppeteerConfigFile',
        str(configs['puppeteer']),
        '--backgroundColor',
        'white',
    ]
    if target.suffix == '.png':
        # Readable when a reader zooms in, and still small.
        command += ['--scale', '2']
    done = subprocess.run(
        command, capture_output=True, text=True, check=False, cwd=ROOT
    )
    if done.returncode != 0 or not target.exists():
        output = (done.stderr or done.stdout).strip()
        raise RenderError(output or f'mmdc exited {done.returncode}')


def write_configs(work: pathlib.Path) -> dict:
    """Writes the mermaid and puppeteer configs into a work directory.

    They are written rather than committed because neither is a thing
    anybody edits: one exists to keep a typeface out of the output, the
    other to let Chromium start as root.

    Args:
        work: A directory to write them into.

    Returns:
        The mapping render_one expects.
    """
    paths = {
        'mermaid': work / 'mermaid.json',
        'puppeteer': work / 'puppeteer.json',
    }
    paths['mermaid'].write_text(json.dumps(MERMAID_CONFIG), encoding='utf-8')
    paths['puppeteer'].write_text(
        json.dumps(PUPPETEER_CONFIG), encoding='utf-8'
    )
    return paths


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Reads the command line.

    Args:
        argv: Arguments to parse, or None for the real ones.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description='Render the C4 diagrams in docs/architecture.md.'
    )
    parser.add_argument(
        '--source',
        type=pathlib.Path,
        default=SOURCE,
        help='the Markdown file holding the diagrams',
    )
    parser.add_argument(
        '--out',
        type=pathlib.Path,
        default=DEFAULT_OUT,
        help='where to write the images',
    )
    parser.add_argument(
        '--format',
        choices=('svg', 'png', 'both'),
        default='both',
        help='which image formats to write',
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='parse and render into a temporary directory, keeping '
        'nothing; for a pre-commit check or a build gate',
    )
    return parser.parse_args(argv)


def formats_for(choice: str) -> tuple[str, ...]:
    """Maps the --format choice to suffixes.

    Args:
        choice: The value of --format.

    Returns:
        The suffixes to render, without the dot.
    """
    if choice == 'both':
        return ('svg', 'png')
    return (choice,)


def main(argv: list[str] | None = None) -> int:
    """Renders every diagram and reports what happened.

    Args:
        argv: Command line arguments, or None for the real ones.

    Returns:
        The process exit status: 0 when every diagram rendered, 1 when
        any did not, 2 when mermaid-cli is not installed.
    """
    args = parse_arguments(argv)
    if not MMDC.exists():
        print(
            f'mermaid-cli is not installed at {MMDC}.\n'
            'Run npm ci at the top of the tree, not in frontend/.',
            file=sys.stderr,
        )
        return 2
    if not os.environ.get('PUPPETEER_EXECUTABLE_PATH'):
        found = shutil.which('chromium') or shutil.which('google-chrome')
        if found:
            os.environ['PUPPETEER_EXECUTABLE_PATH'] = found

    try:
        diagrams = read_diagrams(args.source)
    except (RenderError, OSError) as err:
        print(err, file=sys.stderr)
        return 1

    failures = []
    with tempfile.TemporaryDirectory(prefix='kasapanel-diagrams-') as tmp:
        work = pathlib.Path(tmp)
        configs = write_configs(work)
        out = work / 'out' if args.check else args.out
        out.mkdir(parents=True, exist_ok=True)
        for name, body, line in diagrams:
            mmd = work / f'{name}.mmd'
            mmd.write_text(body + '\n', encoding='utf-8')
            for suffix in formats_for(args.format):
                target = out / f'{name}.{suffix}'
                try:
                    render_one(mmd, target, configs)
                except RenderError as err:
                    failures.append((name, line, suffix, str(err)))
                    continue
                if suffix == 'svg':
                    text = target.read_text(encoding='utf-8')
                    target.write_text(strip_fonts(text), encoding='utf-8')
                size = target.stat().st_size // 1024
                where = 'checked' if args.check else str(target)
                print(f'{name}.{suffix}  {size} kB  {where}')

    for name, line, suffix, message in failures:
        print(
            f'\n{args.source}:{line}: {name} failed to render as '
            f'{suffix}:\n{message}',
            file=sys.stderr,
        )
    if failures:
        print(
            f'\n{len(failures)} of {len(diagrams)} diagrams failed.',
            file=sys.stderr,
        )
        return 1
    print(f'\n{len(diagrams)} diagrams rendered.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
