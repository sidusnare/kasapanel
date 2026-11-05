# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
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
PREAMBLE = 'preamble.txt'

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


def read(name: str = PROMPT) -> str:
    """Reads a file from the top of the working tree.

    Args:
        name: Its name.

    Returns:
        Its contents.
    """
    with open(os.path.join(ROOT, name), encoding='utf-8') as handle:
        return handle.read()


class PromptFileTest(unittest.TestCase):
    """The prompt exists, asks for itself, and is still current."""

    def test_the_file_is_there(self):
        """It ships with the project."""
        self.assertTrue(
            os.path.isfile(os.path.join(ROOT, PROMPT)), f'{PROMPT} is missing'
        )

    def test_it_asks_for_itself(self):
        """The requirement that makes the prompt self-reproducing."""
        self.assertGreaterEqual(
            read().count(PROMPT), 2, f'{PROMPT} should ask for itself by name'
        )

    def test_every_module_is_accounted_for(self):
        """A module nobody mentioned is a prompt that has gone stale."""
        modules = {
            os.path.splitext(entry)[0]
            for entry in os.listdir(PACKAGE)
            if entry.endswith('.py')
        } - UNREMARKABLE
        body = read()
        missing = sorted(
            module for module in modules if f'{module}.py' not in body
        )
        unmentioned = ', '.join(missing)
        self.assertEqual(
            missing, [], f'{PROMPT} does not mention: {unmentioned}'
        )

    def test_the_hard_won_decisions_are_recorded(self):
        """The parts a fresh attempt gets wrong are written down."""
        wanted = (
            'not a thread',  # PAM helper is a process
            'ReadWritePaths',  # not StateDirectory
            'permitted',  # capabilities can be raised
            'throwaway context',  # certificate pair proved first
            'setfacl',  # access is asked, not inferred
            'never poll',  # metrics come from cache
            'no @reboot',  # removed on purpose
            'not the lock',  # the daemon enforces it, not the dialog
            'take the slot',  # a refusal must not consume the rate
            # limit, or nobody could ever sign in
            'seating plan',  # the card order is not a device list
        )
        body = plain(read())
        for phrase in wanted:
            self.assertIn(
                plain(phrase), body, f'{PROMPT} should explain "{phrase}"'
            )

    def test_no_stray_plain_text_twin(self):
        """One prompt, so a second copy cannot drift out of step."""
        self.assertFalse(
            os.path.exists(os.path.join(ROOT, 'AI.prompt.txt')),
            'AI.prompt.md is the only prompt file',
        )

    def test_it_carries_the_licence_header(self):
        """It ships with the project, so it is covered like it."""
        body = read()
        self.assertIn('SPDX-License-Identifier: GPL-3.0-or-later', body)
        self.assertIn('SPDX-FileContributor', body)


class PreambleTest(unittest.TestCase):
    """The engineering policy, and the prompt's copy of it."""

    def test_the_policy_is_there(self):
        """It ships with the project, since the project is held to it."""
        self.assertTrue(
            os.path.isfile(os.path.join(ROOT, PREAMBLE)),
            f'{PREAMBLE} is missing',
        )

    def test_the_prompt_carries_it_verbatim(self):
        """Two copies of a policy are two policies.

        The prompt has to be self-contained, so the preamble is
        embedded in it rather than referred to.  That only stays
        honest if the embedded copy is the file, word for word.
        """
        policy = read(PREAMBLE).strip('\n')
        self.assertIn(
            policy,
            read(),
            f'{PROMPT} no longer carries {PREAMBLE} verbatim; re-embed it',
        )

    def test_it_asks_for_the_prompt_record(self):
        """The requirement that makes the arrangement self-sustaining."""
        self.assertIn(
            'AI.prompt.txt',
            read(PREAMBLE),
            f'{PREAMBLE} should ask for a prompt record',
        )

    def test_the_departures_are_written_down(self):
        """A requirement met differently is stated, never skipped."""
        body = plain(read())
        for phrase in (
            'reads the preamble differently',
            'not `AI.prompt.txt`',
            'JSON, not YAML',
        ):
            self.assertIn(
                plain(phrase), body, f'{PROMPT} should explain "{phrase}"'
            )


if __name__ == '__main__':
    unittest.main()
