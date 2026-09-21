# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Runs the daemon with ``python -m kasapanel``."""

# The module name is fixed by Python itself.
# pylint: disable=invalid-name

import sys

from kasapanel import cli

if __name__ == '__main__':
    sys.exit(cli.main())
