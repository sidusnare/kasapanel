#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Builds the SPDX 2.3 software bill of materials for this repository.

Run it from the top of the working tree after changing any shipped file:

    python3 tools/make_sbom.py

The document lists every shipped file with its SHA-256 and SHA-1
checksum, the package verification code SPDX asks for, and the three
runtime dependencies with package URLs so a scanner can look them up.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Tuple

PACKAGE_NAME = 'kasapanel'
PACKAGE_VERSION = '1.6.2'
PACKAGE_LICENSE = 'GPL-3.0-or-later'
DOCUMENT_NAMESPACE = (
    'https://example.invalid/kasapanel/spdx/kasapanel-1.6.2')

# Directories and files that are not part of what we ship.
SKIP_DIRS = ('.git', '__pycache__', '.pytest_cache', 'build', 'dist',
             '.mypy_cache', '.venv', 'node_modules')
SKIP_SUFFIXES = ('.egg-info',)
SKIP_NAMES = ('sbom.spdx.json',)

DEPENDENCIES: Tuple[Dict[str, str], ...] = (
    {
        'name': 'python-kasa',
        'version': '0.10.2',
        'license': 'GPL-3.0-or-later',
        'supplier': 'Organization: python-kasa contributors',
        'purl': 'pkg:pypi/python-kasa@0.10.2',
        'homepage': 'https://python-kasa.readthedocs.io/',
        'comment': 'Device discovery, transport and control.',
    },
    {
        'name': 'python-pam',
        'version': '2.0.2',
        'license': 'MIT',
        'supplier': 'Organization: python-pam contributors',
        'purl': 'pkg:pypi/python-pam@2.0.2',
        'homepage': 'https://github.com/FirefighterBlu3/python-pam',
        'comment': 'Password checking through Linux PAM.',
    },
    {
        'name': 'six',
        'version': '1.17.0',
        'license': 'MIT',
        'supplier': 'Person: Benjamin Peterson',
        'purl': 'pkg:pypi/six@1.17.0',
        'homepage': 'https://github.com/benjaminp/six',
        'comment': ('Required by python-pam at import '
                    'time; python-pam 2.0.2 does not declare it.'),
    },
    {
        'name': 'cryptography',
        'version': '46.0.6',
        'license': 'Apache-2.0 OR BSD-3-Clause',
        'supplier': 'Organization: Python Cryptographic Authority',
        'purl': 'pkg:pypi/cryptography@46.0.6',
        'homepage': 'https://cryptography.io/',
        'comment': 'Self-signed TLS material for a first run.',
    },
    {
        'name': 'react',
        'version': '18.3.1',
        'license': 'MIT',
        'supplier': 'Organization: Meta Platforms, Inc.',
        'purl': 'pkg:npm/react@18.3.1',
        'homepage': 'https://react.dev/',
        'comment': ('Bundled verbatim into kasapanel/static/bundle.js by '
                    'frontend/build.mjs, so it ships inside this package.'),
        'relationship': 'CONTAINS',
        'purpose': 'LIBRARY',
    },
    {
        'name': 'react-dom',
        'version': '18.3.1',
        'license': 'MIT',
        'supplier': 'Organization: Meta Platforms, Inc.',
        'purl': 'pkg:npm/react-dom@18.3.1',
        'homepage': 'https://react.dev/',
        'comment': ('Bundled verbatim into kasapanel/static/bundle.js by '
                    'frontend/build.mjs, so it ships inside this package.'),
        'relationship': 'CONTAINS',
        'purpose': 'LIBRARY',
    },
    {
        'name': 'esbuild',
        'version': '0.28.1',
        'license': 'MIT',
        'supplier': 'Person: Evan Wallace',
        'purl': 'pkg:npm/esbuild@0.28.1',
        'homepage': 'https://esbuild.github.io/',
        'comment': 'Builds the browser bundle; not shipped.',
        'relationship': 'BUILD_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
    {
        'name': 'jsdom',
        'version': '30.0.1',
        'license': 'MIT',
        'supplier': 'Organization: jsdom contributors',
        'purl': 'pkg:npm/jsdom@30.0.1',
        'homepage': 'https://github.com/jsdom/jsdom',
        'comment': 'Runs the interface tests; not shipped.',
        'relationship': 'TEST_DEPENDENCY_OF',
        'purpose': 'APPLICATION',
    },
)

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


def shipped_files(root: str) -> List[str]:
    """Lists the files that belong in the bill of materials.

    Args:
        root: Top of the working tree.

    Returns:
        Repository relative paths, sorted.
    """
    found = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            name for name in subdirectories
            if name not in SKIP_DIRS
            and not name.endswith(SKIP_SUFFIXES)]
        for name in names:
            if name in SKIP_NAMES or name.endswith('.pyc'):
                continue
            path = os.path.join(directory, name)
            found.append(os.path.relpath(path, root))
    return sorted(found)


def digest(path: str) -> Tuple[str, str]:
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
    safe = ''.join(char if char.isalnum() or char in '.-' else '-'
                   for char in relative)
    return f'SPDXRef-File-{safe}'


def verification_code(sha1_digests: List[str]) -> str:
    """Computes the SPDX package verification code.

    Args:
        sha1_digests: SHA-1 digests of every file in the package.

    Returns:
        The verification code.
    """
    joined = ''.join(sorted(sha1_digests)).encode('ascii')
    return hashlib.sha1(joined).hexdigest()


def build_document(root: str) -> Dict[str, Any]:
    """Builds the whole SPDX document.

    Args:
        root: Top of the working tree.

    Returns:
        The document, ready to serialise as JSON.
    """
    files: List[Dict[str, Any]] = []
    sha1_digests: List[str] = []
    contains: List[Dict[str, str]] = []
    for relative in shipped_files(root):
        sha256, sha1 = digest(os.path.join(root, relative))
        sha1_digests.append(sha1)
        identifier = spdx_id(relative)
        suffix = os.path.splitext(relative)[1]
        if relative in GENERATED_FILES:
            concluded = 'GPL-3.0-or-later AND MIT'
        else:
            concluded = LICENSE_BY_SUFFIX.get(suffix, 'NOASSERTION')
        files.append({
            'SPDXID': identifier,
            'fileName': f'./{relative}',
            'checksums': [
                {'algorithm': 'SHA256', 'checksumValue': sha256},
                {'algorithm': 'SHA1', 'checksumValue': sha1},
            ],
            'licenseConcluded': concluded,
            'licenseInfoInFiles': [concluded],
            'copyrightText': '2026 Kasa Panel contributors',
        })
        contains.append({
            'spdxElementId': f'SPDXRef-Package-{PACKAGE_NAME}',
            'relationshipType': 'CONTAINS',
            'relatedSpdxElement': identifier,
        })

    packages: List[Dict[str, Any]] = [{
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
        'description': ('Threaded HTTPS dashboard, control and scheduler '
                        'daemon for python-kasa devices.'),
        'externalRefs': [{
            'referenceCategory': 'PACKAGE-MANAGER',
            'referenceType': 'purl',
            'referenceLocator':
                f'pkg:pypi/{PACKAGE_NAME}@{PACKAGE_VERSION}',
        }],
    }]
    relationships: List[Dict[str, str]] = [{
        'spdxElementId': 'SPDXRef-DOCUMENT',
        'relationshipType': 'DESCRIBES',
        'relatedSpdxElement': f'SPDXRef-Package-{PACKAGE_NAME}',
    }]
    relationships.extend(contains)
    for dependency in DEPENDENCIES:
        name = dependency['name']
        identifier = f'SPDXRef-Package-{name}'
        packages.append({
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
            'externalRefs': [{
                'referenceCategory': 'PACKAGE-MANAGER',
                'referenceType': 'purl',
                'referenceLocator': dependency['purl'],
            }],
        })
        kind = dependency.get('relationship', 'DEPENDS_ON')
        if kind in ('BUILD_DEPENDENCY_OF', 'TEST_DEPENDENCY_OF'):
            relationships.append({
                'spdxElementId': identifier,
                'relationshipType': kind,
                'relatedSpdxElement': f'SPDXRef-Package-{PACKAGE_NAME}',
            })
        else:
            relationships.append({
                'spdxElementId': f'SPDXRef-Package-{PACKAGE_NAME}',
                'relationshipType': kind,
                'relatedSpdxElement': identifier,
            })
    created = datetime.datetime.now(datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
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
            ],
            'licenseListVersion': '3.24',
            'comment': ('Generated by tools/make_sbom.py.  The AI usage '
                        'declaration for this code base is a separate '
                        'SPDX 3.0 document at docs/ai-bom.spdx.json.'),
        },
        'packages': packages,
        'files': files,
        'relationships': relationships,
    }


def main(argv: List[str] = None) -> int:
    """Writes the SBOM to disk.

    Args:
        argv: Argument list; defaults to the process arguments.

    Returns:
        A process exit status.
    """
    parser = argparse.ArgumentParser(description='Build the SPDX SBOM.')
    parser.add_argument('--root', default='.', help='top of the working tree')
    parser.add_argument('--output', default='sbom.spdx.json',
                        help='where to write the document')
    options = parser.parse_args(argv)
    document = build_document(os.path.abspath(options.root))
    target = os.path.join(options.root, options.output)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    file_count = len(document['files'])
    package_count = len(document['packages'])
    print(f'wrote {target}: {file_count} files, '
          f'{package_count} packages')
    return 0


if __name__ == '__main__':
    sys.exit(main())
