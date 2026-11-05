#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
"""Builds the SPDX 2.3 software bill of materials for this repository.

Run it from the top of the working tree after changing any shipped file:

    python3 tools/make_sbom.py

The document lists every shipped file with its SHA-256 and SHA-1
checksum, the package verification code SPDX asks for, and the three
runtime dependencies with package URLs so a scanner can look them up.
"""

import argparse
import ast
import datetime
import hashlib
import json
import os
import sys
from typing import Any

PACKAGE_NAME = 'kasapanel'
PACKAGE_LICENSE = 'GPL-3.0-or-later'

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_version() -> str:
    """Reads the project version from its single definition.

    The version is defined once, as ``__version__`` in
    ``kasapanel/__init__.py``.  It is parsed rather than imported so
    that the SBOM can be built without the runtime dependencies
    installed, and so that a broken import cannot silently yield a
    stale version.

    Returns:
        The version string, e.g. ``1.8.3``.

    Raises:
        RuntimeError: If no ``__version__`` assignment is found.
    """
    source = os.path.join(REPO_ROOT, PACKAGE_NAME, '__init__.py')
    with open(source, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), filename=source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == '__version__'
                and isinstance(node.value, ast.Constant)
            ):
                return str(node.value.value)
    raise RuntimeError(f'no __version__ found in {source}')


def npm_version(name: str, project: str = 'frontend') -> str:
    """Reads an npm dependency's version from a lock file.

    The lock file is the authoritative record of what `npm ci` will
    install, so reading it keeps the SBOM honest without a network
    call and without a second copy of the version to keep in step.

    There are two npm projects: `frontend` holds the browser
    application, and the top of the tree holds documentation tooling.

    Args:
        name: The package name as npm knows it.
        project: Directory of the npm project, relative to the top of
            the tree.  The empty string means the top itself.

    Returns:
        The locked version string.

    Raises:
        RuntimeError: If the package is not in the lock file.
    """
    lock = os.path.join(REPO_ROOT, project, 'package-lock.json')
    with open(lock, encoding='utf-8') as handle:
        document = json.load(handle)
    entry = document.get('packages', {}).get(f'node_modules/{name}')
    if not entry or 'version' not in entry:
        raise RuntimeError(f'{name} is not in {lock}')
    return str(entry['version'])


PACKAGE_VERSION = read_version()
DOCUMENT_NAMESPACE = (
    f'https://example.invalid/kasapanel/spdx/{PACKAGE_NAME}-{PACKAGE_VERSION}'
)

# Directories and files that are not part of what we ship.
SKIP_DIRS = (
    '.git',
    '__pycache__',
    '.pytest_cache',
    'build',
    'dist',
    '.mypy_cache',
    '.venv',
    'node_modules',
)
SKIP_SUFFIXES = ('.egg-info',)
SKIP_NAMES = ('sbom.spdx.json',)

# Direct dependencies.  No version is written twice: npm versions come
# from frontend/package-lock.json, and each package URL is composed from
# the ecosystem, the name and the version in purl_for().
DEPENDENCIES: tuple[dict[str, str], ...] = (
    {
        'name': 'python-kasa',
        'ecosystem': 'pypi',
        'version': '0.10.2',
        'license': 'GPL-3.0-or-later',
        'supplier': 'Organization: python-kasa contributors',
        'homepage': 'https://python-kasa.readthedocs.io/',
        'comment': 'Device discovery, transport and control.',
    },
    {
        'name': 'python-pam',
        'ecosystem': 'pypi',
        'version': '2.0.2',
        'license': 'MIT',
        'supplier': 'Organization: python-pam contributors',
        'homepage': 'https://github.com/FirefighterBlu3/python-pam',
        'comment': 'Password checking through Linux PAM.',
    },
    {
        'name': 'six',
        'ecosystem': 'pypi',
        'version': '1.17.0',
        'license': 'MIT',
        'supplier': 'Person: Benjamin Peterson',
        'homepage': 'https://github.com/benjaminp/six',
        'comment': (
            'Required by python-pam at import '
            'time; python-pam 2.0.2 does not declare it.'
        ),
    },
    {
        'name': 'cryptography',
        'ecosystem': 'pypi',
        'version': '46.0.6',
        'license': 'Apache-2.0 OR BSD-3-Clause',
        'supplier': 'Organization: Python Cryptographic Authority',
        'homepage': 'https://cryptography.io/',
        'comment': 'Self-signed TLS material for a first run.',
    },
    {
        'name': 'react',
        'ecosystem': 'npm',
        'version': npm_version('react'),
        'license': 'MIT',
        'supplier': 'Organization: Meta Platforms, Inc.',
        'homepage': 'https://react.dev/',
        'comment': (
            'Bundled verbatim into kasapanel/static/bundle.js by '
            'frontend/build.mjs, so it ships inside this package.'
        ),
        'relationship': 'CONTAINS',
        'purpose': 'LIBRARY',
    },
    {
        'name': 'react-dom',
        'ecosystem': 'npm',
        'version': npm_version('react-dom'),
        'license': 'MIT',
        'supplier': 'Organization: Meta Platforms, Inc.',
        'homepage': 'https://react.dev/',
        'comment': (
            'Bundled verbatim into kasapanel/static/bundle.js by '
            'frontend/build.mjs, so it ships inside this package.'
        ),
        'relationship': 'CONTAINS',
        'purpose': 'LIBRARY',
    },
    {
        'name': 'esbuild',
        'ecosystem': 'npm',
        'version': npm_version('esbuild'),
        'license': 'MIT',
        'supplier': 'Person: Evan Wallace',
        'homepage': 'https://esbuild.github.io/',
        'comment': 'Builds the browser bundle; not shipped.',
        'relationship': 'BUILD_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': 'jsdom',
        'ecosystem': 'npm',
        'version': npm_version('jsdom'),
        'license': 'MIT',
        'supplier': 'Organization: jsdom contributors',
        'homepage': 'https://github.com/jsdom/jsdom',
        'comment': 'Runs the interface tests; not shipped.',
        'relationship': 'TEST_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': 'eslint',
        'ecosystem': 'npm',
        'version': npm_version('eslint'),
        'license': 'MIT',
        'supplier': 'Organization: OpenJS Foundation',
        'homepage': 'https://eslint.org',
        'comment': (
            'Lints the browser sources; not shipped.  Held at the 9 '
            'series because eslint-plugin-react peer-depends on '
            'ESLint 9 or older; nothing to do with the Node version.'
        ),
        'relationship': 'DEV_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': '@eslint/js',
        'ecosystem': 'npm',
        'version': npm_version('@eslint/js'),
        'license': 'MIT',
        'supplier': 'Organization: OpenJS Foundation',
        'homepage': 'https://eslint.org',
        'comment': "ESLint's own recommended rule set; not shipped.",
        'relationship': 'DEV_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': 'eslint-plugin-react',
        'ecosystem': 'npm',
        'version': npm_version('eslint-plugin-react'),
        'license': 'MIT',
        'supplier': 'Person: Yannick Croissant',
        'homepage': 'https://github.com/jsx-eslint/eslint-plugin-react',
        'comment': (
            'Teaches ESLint that a component named only inside JSX is '
            'used; not shipped.'
        ),
        'relationship': 'DEV_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': 'markdownlint-cli2',
        'ecosystem': 'npm',
        'version': npm_version('markdownlint-cli2', ''),
        'license': 'MIT',
        'supplier': 'Person: David Anson',
        'homepage': 'https://github.com/DavidAnson/markdownlint-cli2',
        'comment': (
            'Lints the Markdown documents; not shipped.  Declared in '
            'the root package.json, which exists only for this.'
        ),
        'relationship': 'DEV_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': 'globals',
        'ecosystem': 'npm',
        'version': npm_version('globals'),
        'license': 'MIT',
        'supplier': 'Person: Sindre Sorhus',
        'homepage': 'https://github.com/sindresorhus/globals',
        'comment': 'The browser and Node global name lists; not shipped.',
        'relationship': 'DEV_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
)


def purl_for(dependency: dict[str, str]) -> str:
    """Composes a package URL for a dependency.

    Args:
        dependency: An entry from DEPENDENCIES.

    Returns:
        A purl, with an npm scope percent-encoded as the purl
        specification requires.
    """
    name = dependency['name']
    ecosystem = dependency['ecosystem']
    version = dependency['version']
    if ecosystem == 'npm' and name.startswith('@'):
        name = '%40' + name[1:]
    return f'pkg:{ecosystem}/{name}@{version}'


# bundle.js is generated: it carries this project's code and React's.
GENERATED_FILES = ('kasapanel/static/bundle.js',)

LICENSE_BY_SUFFIX = {
    '.py': PACKAGE_LICENSE,
    '.html': PACKAGE_LICENSE,
    '.css': PACKAGE_LICENSE,
    '.js': PACKAGE_LICENSE,
    '.svg': PACKAGE_LICENSE,
    '.toml': PACKAGE_LICENSE,
    '.txt': PACKAGE_LICENSE,
    '.md': 'GPL-3.0-or-later',
    '.service': PACKAGE_LICENSE,
    '.json': 'CC0-1.0',
    '.jsx': PACKAGE_LICENSE,
    '.mjs': PACKAGE_LICENSE,
}


def shipped_files(root: str) -> list[str]:
    """Lists the files that belong in the bill of materials.

    Args:
        root: Top of the working tree.

    Returns:
        Repository relative paths, sorted.
    """
    found = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            name
            for name in subdirectories
            if name not in SKIP_DIRS and not name.endswith(SKIP_SUFFIXES)
        ]
        for name in names:
            if name in SKIP_NAMES or name.endswith('.pyc'):
                continue
            path = os.path.join(directory, name)
            found.append(os.path.relpath(path, root))
    return sorted(found)


def digest(path: str) -> tuple[str, str]:
    """Hashes a file with SHA-256 and SHA-1.

    Args:
        path: File to hash.

    Returns:
        The SHA-256 and SHA-1 digests as lower case hex.
    """
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            sha256.update(block)
            sha1.update(block)
    return sha256.hexdigest(), sha1.hexdigest()


def spdx_id(relative: str) -> str:
    """Builds an SPDX identifier for a file.

    Args:
        relative: Repository relative path.

    Returns:
        An identifier made of the characters SPDX allows.
    """
    safe = ''.join(
        char if char.isalnum() or char in '.-' else '-' for char in relative
    )
    return f'SPDXRef-File-{safe}'


def verification_code(sha1_digests: list[str]) -> str:
    """Computes the SPDX package verification code.

    Args:
        sha1_digests: SHA-1 digests of every file in the package.

    Returns:
        The verification code.
    """
    joined = ''.join(sorted(sha1_digests)).encode('ascii')
    return hashlib.sha1(joined).hexdigest()


def build_document(root: str) -> dict[str, Any]:
    """Builds the whole SPDX document.

    Args:
        root: Top of the working tree.

    Returns:
        The document, ready to serialise as JSON.
    """
    files: list[dict[str, Any]] = []
    sha1_digests: list[str] = []
    contains: list[dict[str, str]] = []
    for relative in shipped_files(root):
        sha256, sha1 = digest(os.path.join(root, relative))
        sha1_digests.append(sha1)
        identifier = spdx_id(relative)
        suffix = os.path.splitext(relative)[1]
        if relative in GENERATED_FILES:
            concluded = 'GPL-3.0-or-later AND MIT'
        else:
            concluded = LICENSE_BY_SUFFIX.get(suffix, 'NOASSERTION')
        files.append(
            {
                'SPDXID': identifier,
                'fileName': f'./{relative}',
                'checksums': [
                    {'algorithm': 'SHA256', 'checksumValue': sha256},
                    {'algorithm': 'SHA1', 'checksumValue': sha1},
                ],
                'licenseConcluded': concluded,
                'licenseInfoInFiles': [concluded],
                'copyrightText': '2026 Kasa Panel contributors',
            }
        )
        contains.append(
            {
                'spdxElementId': f'SPDXRef-Package-{PACKAGE_NAME}',
                'relationshipType': 'CONTAINS',
                'relatedSpdxElement': identifier,
            }
        )

    packages: list[dict[str, Any]] = [
        {
            'SPDXID': f'SPDXRef-Package-{PACKAGE_NAME}',
            'name': PACKAGE_NAME,
            'versionInfo': PACKAGE_VERSION,
            'downloadLocation': 'NOASSERTION',
            'filesAnalyzed': True,
            'packageVerificationCode': {
                'packageVerificationCodeValue': verification_code(sha1_digests),
                'packageVerificationCodeExcludedFiles': list(SKIP_NAMES),
            },
            'licenseConcluded': PACKAGE_LICENSE,
            'licenseDeclared': PACKAGE_LICENSE,
            'copyrightText': '2026 Kasa Panel contributors',
            'supplier': 'Organization: Kasa Panel contributors',
            'primaryPackagePurpose': 'APPLICATION',
            'description': (
                'Threaded HTTPS dashboard, control and scheduler '
                'daemon for python-kasa devices.'
            ),
            'externalRefs': [
                {
                    'referenceCategory': 'PACKAGE-MANAGER',
                    'referenceType': 'purl',
                    'referenceLocator': (
                        f'pkg:pypi/{PACKAGE_NAME}@{PACKAGE_VERSION}'
                    ),
                }
            ],
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            'spdxElementId': 'SPDXRef-DOCUMENT',
            'relationshipType': 'DESCRIBES',
            'relatedSpdxElement': f'SPDXRef-Package-{PACKAGE_NAME}',
        }
    ]
    relationships.extend(contains)
    for dependency in DEPENDENCIES:
        name = dependency['name']
        identifier = f'SPDXRef-Package-{name}'
        packages.append(
            {
                'SPDXID': identifier,
                'name': dependency['name'],
                'versionInfo': dependency['version'],
                'downloadLocation': dependency['homepage'],
                'homepage': dependency['homepage'],
                'filesAnalyzed': False,
                'licenseConcluded': 'NOASSERTION',
                'licenseDeclared': dependency['license'],
                'copyrightText': 'NOASSERTION',
                'supplier': dependency['supplier'],
                'primaryPackagePurpose': dependency.get('purpose', 'LIBRARY'),
                'comment': dependency['comment'],
                'externalRefs': [
                    {
                        'referenceCategory': 'PACKAGE-MANAGER',
                        'referenceType': 'purl',
                        'referenceLocator': purl_for(dependency),
                    }
                ],
            }
        )
        kind = dependency.get('relationship', 'DEPENDS_ON')
        if kind in ('BUILD_DEPENDENCY_OF', 'TEST_DEPENDENCY_OF'):
            relationships.append(
                {
                    'spdxElementId': identifier,
                    'relationshipType': kind,
                    'relatedSpdxElement': f'SPDXRef-Package-{PACKAGE_NAME}',
                }
            )
        else:
            relationships.append(
                {
                    'spdxElementId': f'SPDXRef-Package-{PACKAGE_NAME}',
                    'relationshipType': kind,
                    'relatedSpdxElement': identifier,
                }
            )
    created = datetime.datetime.now(datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )
    return {
        'spdxVersion': 'SPDX-2.3',
        'dataLicense': 'CC0-1.0',
        'SPDXID': 'SPDXRef-DOCUMENT',
        'name': f'{PACKAGE_NAME}-{PACKAGE_VERSION}',
        'documentNamespace': DOCUMENT_NAMESPACE,
        'creationInfo': {
            'created': created,
            'creators': [
                'Organization: Kasa Panel contributors',
                f'Tool: {PACKAGE_NAME}-make-sbom-{PACKAGE_VERSION}',
                # The model that drafted this code base.  The full AI
                # usage declaration, with the SPDX AI profile fields,
                # is docs/ai-bom.spdx.json.
                'Tool: Claude-Opus-5',
            ],
            'licenseListVersion': '3.24',
            'comment': (
                'Generated by tools/make_sbom.py.  The AI usage '
                'declaration for this code base is a separate '
                'SPDX 3.0 document at docs/ai-bom.spdx.json.'
            ),
        },
        'packages': packages,
        'files': files,
        'relationships': relationships,
    }


def main(argv: list[str] | None = None) -> int:
    """Writes the SBOM to disk.

    Args:
        argv: Argument list; defaults to the process arguments.

    Returns:
        A process exit status.
    """
    parser = argparse.ArgumentParser(description='Build the SPDX SBOM.')
    parser.add_argument('--root', default='.', help='top of the working tree')
    parser.add_argument(
        '--output', default='sbom.spdx.json', help='where to write the document'
    )
    options = parser.parse_args(argv)
    document = build_document(os.path.abspath(options.root))
    target = os.path.join(options.root, options.output)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    file_count = len(document['files'])
    package_count = len(document['packages'])
    print(f'wrote {target}: {file_count} files, {package_count} packages')
    return 0


if __name__ == '__main__':
    sys.exit(main())
