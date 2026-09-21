# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Kasa Panel: a threaded HTTPS dashboard for python-kasa devices.

The package is organised in layers so that each piece can be tested on
its own:

* :mod:`kasapanel.jsonstore` -- atomic JSON persistence primitives.
* :mod:`kasapanel.config` -- daemon configuration file handling.
* :mod:`kasapanel.devicestore` -- device inventory and per-device files.
* :mod:`kasapanel.cron` -- cron expression parser (minute resolution).
* :mod:`kasapanel.actions` -- the device action vocabulary.
* :mod:`kasapanel.schedule` -- the per-device schedule script language.
* :mod:`kasapanel.kasabridge` -- thread-safe bridge to python-kasa.
* :mod:`kasapanel.scheduler` -- the minute-tick scheduling thread.
* :mod:`kasapanel.auth` -- Linux PAM authentication and sessions.
* :mod:`kasapanel.api` -- JSON API handlers.
* :mod:`kasapanel.httpd` -- the threaded TLS HTTP server.
* :mod:`kasapanel.app` -- wiring, lifecycle and background threads.
* :mod:`kasapanel.cli` -- the command line entry point.
"""

__version__ = '1.7.0'
__license__ = 'GPL-3.0-or-later'

APP_NAME = 'kasapanel'
APP_TITLE = 'Kasa Panel'
