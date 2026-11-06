# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
"""The architecture document and its diagrams hold together.

Rendering the diagrams needs Node, mermaid-cli and a headless browser,
which is the workflow's job and not a unit test's.  What is worth
checking here is everything that can go wrong without any of that: a
block that no renderer could name, a name used twice, a diagram the
prose never mentions, or a C4 level quietly dropped.

Catching those here rather than in CI saves a contributor a Chromium
download to be told they forgot a comment.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCUMENT = os.path.join(ROOT, 'docs', 'architecture.md')

# The levels a C4 model is made of.  The fourth, code, is deliberately
# absent: at that level the source is the diagram.
# The two shape names of a relationship, however it is spelled:
# Rel, BiRel_Up, Rel_Back and the rest all start with the same pair.
REL = re.compile(r'^\s*(?:Bi)?Rel\w*\(\s*(\w+)\s*,\s*(\w+)\s*,', re.M)

REQUIRED_DIAGRAM_TYPES = (
    'C4Context',
    'C4Container',
    'C4Component',
    'C4Dynamic',
    'C4Deployment',
)


def read_document():
    """Returns the architecture document as text."""
    with open(DOCUMENT, encoding='utf-8') as handle:
        return handle.read()


def renderer():
    """Imports the render script without installing it.

    Returns:
        The ``render_diagrams`` module, so the test uses the same block
        grammar the renderer does rather than a second copy of it.
    """
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    try:
        import render_diagrams  # pylint: disable=import-outside-toplevel
    finally:
        sys.path.pop(0)
    return render_diagrams


class DiagramTest(unittest.TestCase):
    """The diagrams are named, unique and complete."""

    def setUp(self):
        """Reads the document and the renderer once per test."""
        self.text = read_document()
        self.render = renderer()
        self.diagrams = self.render.read_diagrams(self.render.SOURCE)

    def test_the_document_is_where_everything_expects_it(self):
        """The path the workflow and the README name must exist."""
        self.assertTrue(os.path.isfile(DOCUMENT))

    def test_every_block_is_named(self):
        """read_diagrams raises when a block has no comment above it.

        It is the only thing standing between a forgotten comment and a
        diagram that silently never renders.
        """
        self.assertEqual(
            self.text.count('```mermaid'),
            len(self.diagrams),
            'every mermaid block needs a <!-- diagram: name --> above it',
        )

    def test_the_names_are_unique(self):
        """Two diagrams with one name would overwrite each other."""
        names = [name for name, _, _ in self.diagrams]
        self.assertCountEqual(names, set(names))

    def test_the_names_are_safe_as_file_names(self):
        """A name becomes a file name, so it stays plain."""
        for name, _, _ in self.diagrams:
            self.assertRegex(name, r'^[\w.-]+$')
            self.assertNotIn('..', name)

    def test_every_level_of_the_model_is_drawn(self):
        """A C4 model missing a level is not a C4 model."""
        drawn = {body.split('\n', 1)[0].strip() for _, body, _ in self.diagrams}
        for kind in REQUIRED_DIAGRAM_TYPES:
            self.assertIn(kind, drawn, f'no {kind} diagram in the document')

    def test_every_diagram_carries_a_title(self):
        """The title is what names the image for a reader."""
        for name, body, line in self.diagrams:
            self.assertRegex(
                body,
                r'(?m)^\s*title\s+\S',
                f'{name} at line {line} has no title',
            )

    def test_no_diagram_draws_the_same_pair_twice(self):
        """Mermaid keeps one relationship per pair and drops the rest.

        Two `Rel` lines between the same two shapes render as a single
        arrow carrying the later label, and the step numbers carry on
        counting past the one that vanished.  Nothing fails: the
        picture is simply missing a step and misnumbers the rest, which
        is how a self-loop on `auth` silently swallowed the
        empty-password and lockout check the prose points at.
        """
        for name, body, line in self.diagrams:
            pairs = REL.findall(body)
            seen = set()
            for pair in pairs:
                self.assertNotIn(
                    pair,
                    seen,
                    f'{name} at line {line} draws {pair[0]} -> {pair[1]} '
                    'twice; mermaid will render only the last of them',
                )
                seen.add(pair)

    def test_every_step_of_a_dynamic_diagram_is_drawn(self):
        """The numbering is only honest if every step has an arrow.

        A dynamic diagram numbers its steps in source order, so a
        dropped relationship leaves a gap the reader cannot see.
        """
        for name, body, line in self.diagrams:
            if not body.lstrip().startswith('C4Dynamic'):
                continue
            pairs = REL.findall(body)
            self.assertEqual(
                len(pairs),
                len(set(pairs)),
                f'{name} at line {line} would lose a numbered step',
            )

    def test_the_prose_introduces_each_diagram(self):
        """A diagram with no words around it explains nothing.

        Every block must sit under a heading, so that the document
        reads as prose with pictures rather than a pile of pictures.
        """
        for name, _, line in self.diagrams:
            before = self.text[: self.text.index(f'<!-- diagram: {name} -->')]
            self.assertRegex(
                before,
                r'(?s).*\n#{2,3} ',
                f'{name} at line {line} is not under a heading',
            )

    def test_the_rendering_section_is_there(self):
        """Readers need to know how the images are made."""
        self.assertIn('## Rendering', self.text)
        self.assertIn('tools/render_diagrams.py', self.text)

    def test_the_workflow_renders_this_document(self):
        """The workflow and the document must name each other."""
        path = os.path.join(ROOT, '.github', 'workflows', 'diagrams.yml')
        self.assertTrue(os.path.isfile(path), 'the diagrams workflow is gone')
        with open(path, encoding='utf-8') as handle:
            workflow = handle.read()
        self.assertIn('docs/architecture.md', workflow)
        self.assertIn('npm run diagrams', workflow)

    def test_the_readme_points_at_it(self):
        """The architecture document is not much use unfound."""
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as handle:
            self.assertIn('docs/architecture.md', handle.read())


class FontStrippingTest(unittest.TestCase):
    """The SVG post-processing is careful about when it applies."""

    def setUp(self):
        """Imports the renderer."""
        self.render = renderer()

    def test_an_svg_without_an_embedded_font_is_untouched(self):
        """Nothing to strip means nothing changes."""
        svg = '<svg><style>.a{fill:red;}</style></svg>'
        self.assertEqual(self.render.strip_fonts(svg), svg)

    def test_an_unreferenced_font_is_dropped(self):
        """The whole point: 350 kB of base64 nothing asks for."""
        svg = (
            '<svg><style>@font-face { font-family: "Open Sans"; '
            'src: url("data:font/woff2;base64,AAAA"); }'
            '.a{font-family:verdana;}</style></svg>'
        )
        stripped = self.render.strip_fonts(svg)
        self.assertNotIn('data:font/woff2', stripped)
        self.assertIn('verdana', stripped)

    def test_a_font_still_in_use_is_kept(self):
        """A later Mermaid may go back to using it.

        Dropping it then would leave the text laid out for metrics the
        viewer does not have, which is worse than a large file.
        """
        svg = (
            '<svg><style>@font-face { font-family: "Open Sans"; '
            'src: url("data:font/woff2;base64,AAAA"); }'
            '.a{font-family:"Open Sans";}</style></svg>'
        )
        self.assertEqual(self.render.strip_fonts(svg), svg)


if __name__ == '__main__':
    unittest.main()
