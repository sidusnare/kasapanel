# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
"""Tests that the project version is defined exactly once.

Semantic Versioning only means something if every copy of the version
agrees.  Most places derive it: pyproject.toml through setuptools'
dynamic metadata, the SBOM tool by parsing the package.  The few that
cannot be derived -- a JSON document, a recorded test fixture -- are
checked here instead, so a half-finished bump fails the suite rather
than shipping two different answers to "which version is this?".
"""

import json
import os
import re
import sys
import unittest

import kasapanel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# MAJOR.MINOR.PATCH with the optional pre-release and build metadata
# that SemVer 2.0.0 allows.
SEMVER = re.compile(
    r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
    r'(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)'
    r'(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?'
    r'(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$'
)


def read(*parts: str) -> str:
    """Reads a file from the working tree.

    Args:
        *parts: Path components below the top of the tree.

    Returns:
        The file's contents.
    """
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


def load(*parts: str) -> object:
    """Reads a JSON document from the working tree.

    Args:
        *parts: Path components below the top of the tree.

    Returns:
        The parsed document.
    """
    return json.loads(read(*parts))


class VersionTest(unittest.TestCase):
    """One definition, and everything else agreeing with it."""

    def test_it_is_a_semantic_version(self):
        """The format the project says it follows."""
        self.assertRegex(kasapanel.__version__, SEMVER)

    def test_pyproject_derives_it(self):
        """A literal version in pyproject.toml would be a second copy."""
        body = read('pyproject.toml')
        self.assertIn(
            'dynamic = ["version"]',
            body,
            'pyproject.toml should declare the version dynamic',
        )
        self.assertIn(
            'version = {attr = "kasapanel.__version__"}',
            body,
            'and read it from the package',
        )
        self.assertNotRegex(
            body,
            r'(?m)^version = "\d',
            'pyproject.toml should not carry a literal version',
        )

    def test_the_sbom_tool_derives_it(self):
        """The SBOM package version must match the project version."""
        sys.path.insert(0, os.path.join(ROOT, 'tools'))
        try:
            import make_sbom  # pylint: disable=import-outside-toplevel
        finally:
            sys.path.pop(0)
        self.assertEqual(make_sbom.read_version(), kasapanel.__version__)

    def test_the_sbom_agrees(self):
        """A regenerated SBOM carries the current version."""
        document = load('sbom.spdx.json')
        packages = {
            package['name']: package.get('versionInfo')
            for package in document['packages']
        }
        self.assertEqual(
            packages.get('kasapanel'),
            kasapanel.__version__,
            'regenerate with tools/make_sbom.py',
        )
        self.assertIn(kasapanel.__version__, document['name'])

    def test_the_ai_bom_agrees(self):
        """The AI bill of materials names the same release."""
        document = load('docs', 'ai-bom.spdx.json')
        versions = [
            element.get('software_packageVersion')
            for element in document['@graph']
            if element.get('name') == 'kasapanel'
        ]
        self.assertEqual(
            versions,
            [kasapanel.__version__],
            'docs/ai-bom.spdx.json is out of step',
        )

    def test_the_dashboard_fixture_agrees(self):
        """The recorded payload is what the browser tests assert against."""
        fixture = load('frontend', 'test', 'fixtures', 'dashboard.json')
        self.assertEqual(
            fixture['versions']['kasapanel'],
            kasapanel.__version__,
            'frontend/test/fixtures/dashboard.json is stale',
        )

    def test_the_frontend_package_keeps_no_copy(self):
        """A private package needs no version, so it carries none."""
        package = load('frontend', 'package.json')
        self.assertNotIn(
            'version', package, 'frontend/package.json should not restate it'
        )


if __name__ == '__main__':
    unittest.main()
