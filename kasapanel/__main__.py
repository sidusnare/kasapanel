# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Claude Opus 5 (AI-generated)
# AI usage declaration: docs/ai-bom.spdx.json
"""Runs the daemon with ``python -m kasapanel``."""

# The module name is fixed by Python itself.
# pylint: disable=invalid-name

import sys

from kasapanel import cli

if __name__ == '__main__':
    sys.exit(cli.main())
