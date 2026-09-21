# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for the prompt file that regenerates this project.

A prompt that has fallen behind the code is worse than no prompt: it
reads as authoritative while describing something that no longer
exists.  These tests cannot tell whether the prose is any good, but they
can tell when a module has been added and nobody said so.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, 'kasapanel')
PROMPT = 'AI.prompt.md'

# Modules that need no mention: entry points and the package marker.
UNREMARKABLE = {'__init__', '__main__'}


def plain(text: str) -> str:
    """Strips Markdown decoration so the two files compare fairly.

    Args:
        text: File contents.

    Returns:
        The text without backticks, asterisks or underscores.
    """
    return re.sub(r'[`*_]', '', text).lower()


def read() -> str:
    """Reads the prompt file.

    Returns:
        Its contents.
    """
    with open(os.path.join(ROOT, PROMPT), encoding='utf-8') as handle:
        return handle.read()


class PromptFileTest(unittest.TestCase):
    """The prompt exists, asks for itself, and is still current."""

    def test_the_file_is_there(self):
        """It ships with the project."""
        self.assertTrue(os.path.isfile(os.path.join(ROOT, PROMPT)),
                        f'{PROMPT} is missing')

    def test_it_asks_for_itself(self):
        """The requirement that makes the prompt self-reproducing."""
        self.assertGreaterEqual(
            read().count(PROMPT), 2,
            f'{PROMPT} should ask for itself by name')

    def test_every_module_is_accounted_for(self):
        """A module nobody mentioned is a prompt that has gone stale."""
        modules = {
            os.path.splitext(entry)[0]
            for entry in os.listdir(PACKAGE) if entry.endswith('.py')
        } - UNREMARKABLE
        body = read()
        missing = sorted(module for module in modules
                         if f'{module}.py' not in body)
        unmentioned = ', '.join(missing)
        self.assertEqual(missing, [],
                         f'{PROMPT} does not mention: {unmentioned}')

    def test_the_hard_won_decisions_are_recorded(self):
        """The parts a fresh attempt gets wrong are written down."""
        wanted = (
            'not a thread',        # PAM helper is a process
            'ReadWritePaths',      # not StateDirectory
            'permitted',           # capabilities can be raised
            'throwaway context',   # certificate pair proved first
            'setfacl',             # access is asked, not inferred
            'never poll',          # metrics come from cache
            'no @reboot',          # removed on purpose
        )
        body = plain(read())
        for phrase in wanted:
            self.assertIn(plain(phrase), body,
                          f'{PROMPT} should explain "{phrase}"')

    def test_no_stray_plain_text_twin(self):
        """One prompt, so a second copy cannot drift out of step."""
        self.assertFalse(
            os.path.exists(os.path.join(ROOT, 'AI.prompt.txt')),
            'AI.prompt.md is the only prompt file')

    def test_it_carries_the_licence_header(self):
        """It ships with the project, so it is covered like it."""
        body = read()
        self.assertIn('SPDX-License-Identifier: GPL-3.0-or-later', body)
        self.assertIn('SPDX-FileContributor', body)


if __name__ == '__main__':
    unittest.main()
