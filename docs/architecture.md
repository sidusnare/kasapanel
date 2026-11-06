<!--
SPDX-FileCopyrightText: 2026 Kasa Panel contributors

SPDX-License-Identifier: GPL-3.0-or-later

SPDX-FileContributor: Claude Opus 5 (AI-generated)
AI usage declaration: docs/ai-bom.spdx.json
-->

# Architecture

This is a [C4 model][c4] of Kasa Panel: the same system drawn at four
levels, each one closer in than the last. Read it from the top. If you
only want to know what the daemon talks to, the first diagram is the
whole answer and you can stop there.

Each diagram is a [Mermaid][mermaid] block in this file, which is the
only copy. GitHub renders them where you are reading them, and
[the diagrams workflow](../.github/workflows/diagrams.yml) renders the
same blocks to SVG and PNG on every push, so there is no second copy to
fall out of date. See [Rendering](#rendering) at the foot.

Mermaid's C4 support is marked experimental upstream, and its layout is
a grid with straight lines rather than a router. That is why the
component level is split into several small diagrams instead of one
large one: a component diagram with a dozen boxes in it renders as a
thicket, and is not much use to read either. The `UpdateRelStyle` lines
at the foot of some blocks nudge edge labels off each other by hand. If
you change a label's text, expect to adjust its offset.

[c4]: https://c4model.com/
[mermaid]: https://mermaid.js.org/syntax/c4.html

## Level 1: system context

Kasa Panel is a daemon on one host on your LAN. It has no cloud
account, no telemetry, and no outbound connection to anything but your
own devices.

The host's own account database signs people in, which is why PAM is a
system of its own here: the panel has no user list and deliberately
never grows one.

<!-- diagram: 01-system-context -->

```mermaid
C4Context
    title System context for Kasa Panel

    Person(operator, "Operator", "Has an account on the host. There is no separate panel account.")
    System(panel, "Kasa Panel", "Threaded HTTPS dashboard, control and scheduler daemon")

    System_Ext(devices, "Kasa devices", "Plugs, bulbs and strips. Found by UDP broadcast, driven over TCP.")
    System_Ext(pam, "Linux PAM", "The host's accounts and password policy. Reading it needs root.")
    System_Ext(monitoring, "Monitoring system", "Prometheus, or anything that scrapes the text format")
    System_Ext(renewal, "Certificate renewal", "certbot, a company CA or a script. Cannot notify the daemon.")
    System_Ext(init, "systemd", "Owns the restarts, the shutdown signal and the log")

    Rel(operator, panel, "Signs in and controls", "HTTPS 8443")
    Rel(panel, devices, "Discovers, polls, switches and dims", "python-kasa")
    Rel(panel, pam, "Checks one password at a time", "libpam, root helper")
    Rel(monitoring, panel, "Scrapes cached state", "GET /metrics")
    Rel(renewal, panel, "Replaces the certificate file", "a PEM on disk")
    Rel(init, panel, "Starts, stops, collects the log", "unit file, SIGTERM")

    UpdateRelStyle(operator, panel, $offsetX="-20", $offsetY="-50")
    UpdateRelStyle(panel, devices, $offsetX="-90", $offsetY="10")
    UpdateRelStyle(panel, pam, $offsetX="20", $offsetY="40")
    UpdateRelStyle(monitoring, panel, $offsetX="-40", $offsetY="-70")
    UpdateRelStyle(renewal, panel, $offsetX="50", $offsetY="-20")
    UpdateRelStyle(init, panel, $offsetX="10", $offsetY="70")
```

## Level 2: containers

Four things run, and three of them are files. The split that matters is
the second process. PAM needs root; nothing else does. A thread cannot
hold a different identity from the process around it, because glibc
broadcasts `setuid` to every thread deliberately. So the daemon forks a
helper before any thread exists, keeps root only there, and drops
everything else.

What that buys: anyone who takes the web server gets a socket on which
the only sentence that can be said is "is this password correct",
answered yes or no.

<!-- diagram: 02-containers -->

```mermaid
C4Container
    title Containers inside Kasa Panel

    Person(operator, "Operator", "Signs in with a host account")
    System_Ext(devices, "Kasa devices", "Plugs and bulbs on the LAN")
    System_Ext(pam, "Linux PAM", "Host passwords and policy")

    System_Boundary(panel, "Kasa Panel") {
        Container(spa, "Browser application", "React, bundled by esbuild", "One committed bundle, so installing the daemon needs Python only")
        Container(daemon, "Panel daemon", "Python 3.10 or newer", "TLS, the JSON API, the poller and the minute tick. Unprivileged.")
        Container(helper, "Authentication helper", "Python, keeps root", "Forked before the first thread exists. Holds no other state.")
        ContainerDb(configfile, "Configuration", "a JSON file", "One flat object, hand editable")
        ContainerDb(state, "State directory", "JSON files", "Inventory, schedules, preferences")
        ContainerDb(tls, "TLS material", "PEM files", "Self-signed on a first run")
    }

    Rel(operator, spa, "Uses", "HTTPS 8443")
    Rel(spa, daemon, "Calls the JSON API", "cookie or X-Kasa-Session, CSRF on writes")
    Rel(daemon, helper, "Is this password correct", "socketpair")
    Rel(helper, pam, "Holds the conversation", "libpam as root")
    Rel(daemon, devices, "Polls and commands", "python-kasa")
    Rel(daemon, configfile, "Reads, rewrites on save", "write and rename")
    Rel(daemon, state, "Reads and writes", "write and rename")
    Rel(daemon, tls, "Loads, reloads on change", "ssl.SSLContext")

    UpdateRelStyle(operator, spa, $offsetX="-190", $offsetY="-70")
    UpdateRelStyle(spa, daemon, $offsetX="-30", $offsetY="-130")
    UpdateRelStyle(daemon, helper, $offsetX="90", $offsetY="-20")
    UpdateRelStyle(helper, pam, $offsetX="-180", $offsetY="150")
    UpdateRelStyle(daemon, devices, $offsetX="40", $offsetY="-90")
    UpdateRelStyle(daemon, configfile, $offsetX="50", $offsetY="30")
    UpdateRelStyle(daemon, state, $offsetX="-160", $offsetY="70")
    UpdateRelStyle(daemon, tls, $offsetX="20", $offsetY="100")
```

## Level 3: components

The daemon has twenty-two modules and the browser application has
fourteen. Drawn together they are unreadable, so the level is split
into four views of the daemon and two of the interface. Each view names
the boxes that belong to it and refers to the rest as one box.

### The request path

`httpd` knows about sockets and nothing else; `api` knows about JSON
and nothing about sockets. That line is what lets the handlers be
called directly from tests, with no server running.

`metrics` sits beside `api` rather than inside it because it answers to
a different rule: a scrape reads the snapshot cache the poller filled,
and stops there. A monitoring system polling every fifteen seconds must
not become fifteen-second traffic to every plug in the house.

<!-- diagram: 03-components-request -->

```mermaid
C4Component
    title Components: the request path

    Container(spa, "Browser application", "React", "The dashboard")
    System_Ext(monitoring, "Monitoring system", "Scrapes metrics")
    Container(helper, "Authentication helper", "Python, root", "Checks passwords")

    Container_Boundary(daemon, "Panel daemon") {
        Component(httpd, "httpd", "http.server, ssl", "One thread per connection, TLS only, static files, security headers")
        Component(api, "api", "a regex routing table", "Transport-free JSON handlers. A session on every route, CSRF on writes.")
        Component(metrics, "metrics", "Prometheus text format", "Renders from the snapshot cache, so a scrape never touches a device")
        Component(app, "app", "threading", "Owns every long-lived object, the poller thread and the snapshot cache")
        Component(auth, "auth", "pam, secrets", "Sessions, allow lists, lockout, and a one-second floor on PAM")
        Component(activity, "activity", "deque and logging", "A ring buffer that is also written to the log")
    }

    Rel(spa, httpd, "Requests", "HTTPS")
    Rel(monitoring, httpd, "Scrapes", "GET /metrics")
    Rel(httpd, api, "Request in, Response out")
    Rel(httpd, metrics, "Asks for the exposition")
    Rel(api, auth, "Signs in, checks session and CSRF")
    Rel(api, app, "Asks for everything else")
    Rel(auth, helper, "One password check", "socketpair")
    Rel(metrics, app, "Reads the snapshot cache")
    Rel(app, activity, "Records what happened")

    UpdateRelStyle(spa, httpd, $offsetX="130", $offsetY="-150")
    UpdateRelStyle(monitoring, httpd, $offsetX="50", $offsetY="-60")
    UpdateRelStyle(httpd, api, $offsetX="-10", $offsetY="-60")
    UpdateRelStyle(httpd, metrics, $offsetX="30", $offsetY="40")
    UpdateRelStyle(api, auth, $offsetX="-210", $offsetY="70")
    UpdateRelStyle(api, app, $offsetX="150", $offsetY="-40")
    UpdateRelStyle(auth, helper, $offsetX="-140", $offsetY="60")
    UpdateRelStyle(metrics, app, $offsetX="-30", $offsetY="-70")
    UpdateRelStyle(app, activity, $offsetX="50", $offsetY="-20")
```

### Starting up and staying up

The order in this diagram is the whole point of it. The helper is
forked while the process is still single-threaded and before the
listening socket exists, so it can never hold one. `filecheck` runs
*after* the drop, because "can root write this file" is not the
question anyone cares about.

`certwatch` and `logsink` both exist because a file the daemon opened
at start up may not be the file at that path an hour later. One handles
a renewed certificate, the other a rotated log.

<!-- diagram: 04-components-lifecycle -->

```mermaid
C4Component
    title Components: starting up and staying up

    Container(helper, "Authentication helper", "Python, root", "The forked child")
    Component(httpd_ref, "httpd and app", "the serving layers", "Started once the drop has happened")

    Container_Boundary(daemon, "Panel daemon") {
        Component(cli, "cli", "argparse", "The command line. run is the daemon; the other verbs inspect without starting anything.")
        Component(privsep, "privsep", "fork, capset, setuid", "Forks the helper while still single-threaded, checks the capabilities a privileged start needs, then drops")
        Component(certs, "certs", "cryptography or openssl", "Makes a self-signed pair for a first run")
        Component(certwatch, "certwatch", "cryptography, ssl", "Re-keys the live TLS context when the file on disk changes, without dropping a session")
        Component(filecheck, "filecheck", "a real write, not access", "Proves, after the drop, that the daemon can still write its config, log and pid file")
        Component(logsink, "logsink", "a logging handler", "Reopens the log when rotation or a tidy-up moves it")
    }

    Rel(cli, privsep, "1. Forks the helper, then drops")
    Rel(privsep, helper, "Keeps root here only", "socketpair")
    Rel(cli, certs, "2. Makes a pair if none is configured")
    Rel(cli, filecheck, "3. Checks, after the drop")
    Rel(cli, logsink, "4. Installs")
    Rel(cli, httpd_ref, "5. Serves until SIGTERM")
    Rel(certwatch, httpd_ref, "Reloads the context in place")

    UpdateRelStyle(cli, privsep, $offsetX="-30", $offsetY="-70")
    UpdateRelStyle(privsep, helper, $offsetX="40", $offsetY="-50")
    UpdateRelStyle(cli, certs, $offsetX="60", $offsetY="20")
    UpdateRelStyle(cli, filecheck, $offsetX="-130", $offsetY="50")
    UpdateRelStyle(cli, logsink, $offsetX="40", $offsetY="80")
    UpdateRelStyle(cli, httpd_ref, $offsetX="-230", $offsetY="-40")
    UpdateRelStyle(certwatch, httpd_ref, $offsetX="90", $offsetY="-30")
```

### The schedule language and the device boundary

The language is three modules, not one, so each can be tested on its
own: `cron` parses the time half, `actions` declares the verb half, and
`schedule` is the line that joins them. `actions` is shared with the
JSON API and the dashboard buttons, which is why a button and a
schedule rule cannot mean different things.

`kasabridge` is the only module that imports python-kasa. Everything
above it is testable without a smart plug on the network, which is the
entire reason the boundary exists.

<!-- diagram: 05-components-schedule -->

```mermaid
C4Component
    title Components: the schedule language and the device boundary

    System_Ext(devices, "Kasa devices", "A plug on the LAN")
    Component(store_ref, "devicestore", "the per-device files", "Holds each device's script and its snooze")
    Component(api_ref, "api and app", "the layers above", "Buttons, and the poller")

    Container_Boundary(daemon, "Panel daemon") {
        Component(scheduler, "scheduler", "a minute-tick thread", "Wakes just past each boundary and runs what is due, in a small pool so one dead device cannot hold up the rest")
        Component(schedule, "schedule", "a line parser", "One rule per line: a cron expression, then an action")
        Component(cron, "cron", "Vixie syntax", "Fields, ranges, steps, names, the @daily family. No seconds, by design.")
        Component(actions, "actions", "a declared vocabulary", "The verb half, shared with the API and the interface so they cannot disagree")
        Component(snooze, "snooze", "systemd.time syntax", "One instant per device. Missed rules are skipped, not replayed.")
        Component(kasabridge, "kasabridge", "asyncio in one thread", "The only importer of python-kasa. One loop owns every device object and its sockets.")
    }

    Rel(scheduler, store_ref, "Reads each script")
    Rel(scheduler, snooze, "Skips a snoozed device")
    Rel(scheduler, schedule, "Which rules are due")
    Rel(schedule, cron, "Parses the time half")
    Rel(schedule, actions, "Resolves the verb half")
    Rel(scheduler, kasabridge, "Runs the due actions")
    Rel(api_ref, kasabridge, "Buttons, and the poll")
    Rel(kasabridge, actions, "Looks up the runner")
    Rel(kasabridge, devices, "Commands and reads", "python-kasa")

    UpdateRelStyle(scheduler, store_ref, $offsetX="30", $offsetY="-60")
    UpdateRelStyle(scheduler, snooze, $offsetX="-30", $offsetY="90")
    UpdateRelStyle(scheduler, schedule, $offsetX="60", $offsetY="-70")
    UpdateRelStyle(schedule, cron, $offsetX="-40", $offsetY="20")
    UpdateRelStyle(schedule, actions, $offsetX="130", $offsetY="-10")
    UpdateRelStyle(scheduler, kasabridge, $offsetX="90", $offsetY="50")
    UpdateRelStyle(api_ref, kasabridge, $offsetX="-260", $offsetY="90")
    UpdateRelStyle(kasabridge, actions, $offsetX="150", $offsetY="-60")
    UpdateRelStyle(kasabridge, devices, $offsetX="40", $offsetY="-80")
```

### Persistence

Every piece of state is its own JSON file, and every write goes through
one module. `jsonstore` writes to a temporary file in the same
directory and renames it over the target, so a crash mid-write cannot
leave a truncated file behind — which is also why the daemon account
must be able to write the *directory*, not just the file.

The inventory and the per-device files are split so that a device can
be backed up, copied or hand-edited on its own, and so the inventory
stays short enough to read.

<!-- diagram: 06-components-storage -->

```mermaid
C4Component
    title Components: persistence

    Component(api_ref, "api, app and scheduler", "the layers above", "Everything that reads or changes state")
    ContainerDb(statedir, "State directory", "JSON files", "devices.json, one file per device, one per operator")
    ContainerDb(configfile, "Configuration", "a JSON file", "The daemon's own settings")

    Container_Boundary(daemon, "Panel daemon") {
        Component(devicestore, "devicestore", "dataclasses over JSON", "The inventory, and one file per device holding its script, its snooze and its locks")
        Component(userstore, "userstore", "one file per account", "Card order and theme. Two operators signed in at once want different answers.")
        Component(config, "config", "a dataclass", "The daemon's settings, with the secret fields redacted on the way to the browser")
        Component(jsonstore, "jsonstore", "os.replace", "Writes beside the target and renames over it, so a crash cannot truncate a file")
    }

    Rel(api_ref, devicestore, "Adds, edits, schedules")
    Rel(api_ref, userstore, "Saves preferences")
    Rel(api_ref, config, "Saves settings")
    Rel(devicestore, jsonstore, "Persists")
    Rel(userstore, jsonstore, "Persists")
    Rel(config, jsonstore, "Persists")
    Rel(jsonstore, statedir, "Writes and renames")
    Rel(jsonstore, configfile, "Writes and renames")

    UpdateRelStyle(api_ref, devicestore, $offsetX="-170", $offsetY="30")
    UpdateRelStyle(api_ref, userstore, $offsetX="240", $offsetY="40")
    UpdateRelStyle(api_ref, config, $offsetX="-250", $offsetY="150")
    UpdateRelStyle(devicestore, jsonstore, $offsetX="-60", $offsetY="30")
    UpdateRelStyle(userstore, jsonstore, $offsetX="20", $offsetY="60")
    UpdateRelStyle(config, jsonstore, $offsetX="90", $offsetY="20")
    UpdateRelStyle(jsonstore, statedir, $offsetX="-60", $offsetY="40")
    UpdateRelStyle(jsonstore, configfile, $offsetX="40", $offsetY="70")
```

### The browser application: shell

One module talks to the daemon and the rest do not, which keeps session
handling in a single place. The session travels two ways — an HttpOnly
cookie and an `X-Kasa-Session` header — because a browser refuses to
store a `Secure` cookie from an origin whose certificate it does not
trust, which is every self-signed first run. A custom header cannot be
attached by a cross-site form, so this costs nothing in CSRF terms.

<!-- diagram: 07-components-browser-shell -->

```mermaid
C4Component
    title Components: the browser application, shell

    Person(operator, "Operator", "Clicks and types")
    Container(daemon_ref, "Panel daemon", "Python", "Serves the bundle and the JSON API")
    Component(views_ref, "the five views", "React", "Panel, Devices, Schedules, Activity, Settings")

    Container_Boundary(spa, "Browser application") {
        Component(main, "main.jsx", "the React root", "Mounts the tree and applies the remembered theme before the first paint")
        Component(appjs, "App.jsx", "React", "Holds the session and the dashboard payload, polls, and chooses the view")
        Component(signin, "SignIn.jsx", "React", "The only place a password is typed")
        Component(apijs, "api.js", "fetch", "The one module that talks to the daemon. Carries the session two ways and adds CSRF to writes.")
        Component(toasts, "Toasts.jsx", "React context", "Transient messages, including the ones an API error becomes")
        Component(order, "order.js", "a seating plan", "Where each card sits. A plan, not a copy of the inventory, so adding a device cannot corrupt it.")
        Component(theme, "theme.js", "localStorage", "Light or dark. The daemon is the authority; the local copy only prevents a flash of the wrong one.")
    }

    Rel(operator, appjs, "Clicks and types")
    Rel(main, appjs, "Mounts")
    Rel(main, theme, "Applies before the first paint")
    Rel(main, toasts, "Provides")
    Rel(appjs, signin, "Shows when there is no session")
    Rel(appjs, apijs, "Signs in, then polls the dashboard")
    Rel(appjs, order, "Seats the cards")
    Rel(appjs, views_ref, "Renders the chosen one")
    Rel(apijs, toasts, "Turns an error into a message")
    Rel(apijs, daemon_ref, "Calls", "HTTPS JSON")

    UpdateRelStyle(operator, appjs, $offsetX="-40", $offsetY="-40")
    UpdateRelStyle(main, appjs, $offsetX="-30", $offsetY="10")
    UpdateRelStyle(main, theme, $offsetX="-180", $offsetY="-20")
    UpdateRelStyle(main, toasts, $offsetX="50", $offsetY="40")
    UpdateRelStyle(appjs, signin, $offsetX="-100", $offsetY="-20")
    UpdateRelStyle(appjs, apijs, $offsetX="30", $offsetY="-60")
    UpdateRelStyle(appjs, order, $offsetX="-160", $offsetY="60")
    UpdateRelStyle(appjs, views_ref, $offsetX="180", $offsetY="-60")
    UpdateRelStyle(apijs, toasts, $offsetX="80", $offsetY="-20")
    UpdateRelStyle(apijs, daemon_ref, $offsetX="150", $offsetY="-30")
```

### The browser application: views

Each tab is one module and each one calls `api.js` rather than holding
its own idea of the session. `ConfirmLock` is the odd one: it is not a
tab but a gate, and both the panel and the schedule editor put it in
front of anything that would change a locked device. When the device is
`secure_locked` it asks for the operator's password as well, which goes
back through PAM like any other sign-in.

<!-- diagram: 08-components-browser-views -->

```mermaid
C4Component
    title Components: the browser application, views

    Component(appjs_ref, "App.jsx", "React", "Chooses the view and holds the payload")
    Component(apijs_ref, "api.js", "fetch", "The one module that talks to the daemon")

    Container_Boundary(spa, "Browser application") {
        Component(panel, "views/Panel.jsx", "React", "The cards: a rocker per device, a brightness slider, a snooze menu")
        Component(devicesv, "views/Devices.jsx", "React", "Add by host, scan the LAN, rename, remove")
        Component(schedules, "views/Schedules.jsx", "React", "The script editor, with the daemon's own parse findings shown inline")
        Component(activityv, "views/Activity.jsx", "React", "Recent events and the signed-in sessions")
        Component(settingsv, "views/Settings.jsx", "React", "Daemon settings, marking which ones need a restart")
        Component(confirm, "ConfirmLock.jsx", "React", "The plate a locked device puts in front of anything that would change it, and the password prompt when it is secure-locked")
        Component(format, "format.js", "Intl", "Clocks, watts and dashes. The daemon sends host-local times with no zone.")
    }

    Rel(appjs_ref, panel, "Renders")
    Rel(appjs_ref, devicesv, "Renders")
    Rel(appjs_ref, schedules, "Renders")
    Rel(appjs_ref, activityv, "Renders")
    Rel(appjs_ref, settingsv, "Renders")
    Rel(panel, confirm, "Asks first, when locked")
    Rel(schedules, confirm, "Asks first, when locked")
    Rel(panel, format, "Formats clocks and watts")
    Rel(panel, apijs_ref, "Sends actions and snoozes")
    Rel(devicesv, apijs_ref, "Adds, scans, removes")
    Rel(schedules, apijs_ref, "Checks and saves scripts")
    Rel(activityv, apijs_ref, "Reads records and sessions")
    Rel(settingsv, apijs_ref, "Reads and saves")

    UpdateRelStyle(appjs_ref, panel, $offsetX="-120", $offsetY="-40")
    UpdateRelStyle(appjs_ref, devicesv, $offsetX="30", $offsetY="-60")
    UpdateRelStyle(appjs_ref, schedules, $offsetX="-200", $offsetY="120")
    UpdateRelStyle(appjs_ref, activityv, $offsetX="200", $offsetY="60")
    UpdateRelStyle(appjs_ref, settingsv, $offsetX="230", $offsetY="180")
    UpdateRelStyle(panel, confirm, $offsetX="-40", $offsetY="20")
    UpdateRelStyle(schedules, confirm, $offsetX="40", $offsetY="50")
    UpdateRelStyle(panel, format, $offsetX="60", $offsetY="-20")
    UpdateRelStyle(panel, apijs_ref, $offsetX="-120", $offsetY="-50")
    UpdateRelStyle(devicesv, apijs_ref, $offsetX="-40", $offsetY="-80")
    UpdateRelStyle(schedules, apijs_ref, $offsetX="20", $offsetY="-110")
    UpdateRelStyle(activityv, apijs_ref, $offsetX="90", $offsetY="-60")
    UpdateRelStyle(settingsv, apijs_ref, $offsetX="150", $offsetY="-30")
```

## Dynamic views

Two sequences are worth following, because in both the interesting part
is which process does the work.

### Signing in

The daemon never learns whether the password was right; it learns a yes
or a no from a process it cannot read. Note the fifth step: `auth`
refuses an empty password and applies the rate limit and the lockout
*before* the helper is engaged, so neither an empty field nor a flood
of attempts ever reaches PAM.

<!-- diagram: 09-dynamic-sign-in -->

```mermaid
C4Dynamic
    title Dynamic: signing in
    UpdateLayoutConfig($c4ShapeInRow="2", $c4BoundaryInRow="1")

    Person(operator, "Operator", "Types a host account name and password")
    Container(spa, "Browser application", "React", "The sign-in form")
    System_Ext(pam, "Linux PAM", "The host's password check")

    Container_Boundary(daemon, "Panel daemon, unprivileged") {
        Component(httpd, "httpd", "ssl, one thread per connection")
        Component(api, "api", "JSON handlers")
        Component(auth, "auth", "sessions and policy")
    }

    Container_Boundary(helperproc, "Authentication helper, root") {
        Component(helper, "privsep helper", "a forked process")
    }

    Rel(operator, spa, "Enters a name and password")
    Rel(spa, httpd, "POST /api/session", "HTTPS")
    Rel(httpd, api, "Dispatches to sign_in")
    Rel(api, auth, "authenticate")
    Rel(auth, auth, "Refuses empty, then the lockout and the rate limit")
    Rel(auth, helper, "One check", "socketpair")
    Rel(helper, pam, "Holds the conversation", "libpam as root")
    Rel(pam, helper, "Accepts or refuses")
    Rel(helper, auth, "Yes or no, nothing else")
    Rel(auth, api, "Allow list passes; the account name")
    Rel(api, spa, "Mints a session; the cookie and the token", "HTTPS")

    UpdateRelStyle(operator, spa, $offsetX="-10", $offsetY="40")
    UpdateRelStyle(httpd, api, $offsetX="-20", $offsetY="-50")
    UpdateRelStyle(api, auth, $offsetX="60", $offsetY="-30")
    UpdateRelStyle(auth, helper, $offsetX="-150", $offsetY="-40")
    UpdateRelStyle(helper, pam, $offsetX="-200", $offsetY="-60")
    UpdateRelStyle(pam, helper, $offsetX="150", $offsetY="40")
    UpdateRelStyle(helper, auth, $offsetX="200", $offsetY="80")
    UpdateRelStyle(auth, api, $offsetX="-40", $offsetY="30")
    UpdateRelStyle(api, spa, $offsetX="30", $offsetY="-120")
```

### A schedule minute

The scheduler wakes a moment after the boundary rather than on it,
reads every device's script fresh, and runs what is due in a pool.
Times are the host's local time, exactly like cron: rules in a skipped
hour do not run, and rules in a repeated hour run twice.

<!-- diagram: 10-dynamic-schedule-tick -->

```mermaid
C4Dynamic
    title Dynamic: one scheduler minute
    UpdateLayoutConfig($c4ShapeInRow="2", $c4BoundaryInRow="1")

    System_Ext(devices, "Kasa devices", "A plug on the LAN")
    ContainerDb(state, "State directory", "JSON files", "The per-device files")

    Container_Boundary(daemon, "Panel daemon") {
        Component(scheduler, "scheduler", "a minute-tick thread")
        Component(devicestore, "devicestore", "the inventory")
        Component(snooze, "snooze", "snooze instants")
        Component(schedule, "schedule", "the script language")
        Component(cron, "cron", "the time half")
        Component(kasabridge, "kasabridge", "the asyncio boundary")
        Component(activity, "activity", "the ring buffer and the log")
    }

    Rel(scheduler, scheduler, "Sleeps to just past the boundary")
    Rel(scheduler, devicestore, "Reads each script")
    Rel(devicestore, state, "Reads the per-device files")
    Rel(scheduler, snooze, "Skips a snoozed device")
    Rel(scheduler, schedule, "Parses the script")
    Rel(schedule, cron, "Match this minute?")
    Rel(scheduler, kasabridge, "Submits to a pool")
    Rel(kasabridge, devices, "Switches, dims or refreshes", "python-kasa")
    Rel(devices, kasabridge, "Answers, or times out")
    Rel(kasabridge, scheduler, "Returns, or raises")
    Rel(scheduler, activity, "Records the outcome")

    UpdateRelStyle(scheduler, scheduler, $offsetX="-150", $offsetY="-80")
    UpdateRelStyle(scheduler, devicestore, $offsetX="-40", $offsetY="-70")
    UpdateRelStyle(devicestore, state, $offsetX="60", $offsetY="-40")
    UpdateRelStyle(scheduler, snooze, $offsetX="-150", $offsetY="30")
    UpdateRelStyle(scheduler, schedule, $offsetX="90", $offsetY="-60")
    UpdateRelStyle(schedule, cron, $offsetX="-30", $offsetY="-10")
    UpdateRelStyle(scheduler, kasabridge, $offsetX="-30", $offsetY="130")
    UpdateRelStyle(kasabridge, devices, $offsetX="340", $offsetY="-60")
    UpdateRelStyle(devices, kasabridge, $offsetX="330", $offsetY="30")
    UpdateRelStyle(kasabridge, scheduler, $offsetX="110", $offsetY="90")
    UpdateRelStyle(scheduler, activity, $offsetX="-130", $offsetY="-30")
```

## Deployment

One host, one unit, two processes. Nothing is containerised and nothing
needs to be: the daemon's dependencies are Python and python-kasa.

Running in the foreground under systemd is the arrangement to prefer,
even though `--daemonize` exists. systemd then owns the restarts, the
log and the shutdown signal, and does all three better than a
double-forked process can.

<!-- diagram: 11-deployment -->

```mermaid
C4Deployment
    title Deployment: one host on the LAN

    Deployment_Node(client, "Operator's computer or phone", "any OS") {
        Deployment_Node(browser, "Web browser", "Chromium, Firefox or Safari") {
            Container(spa, "Browser application", "React bundle", "Served by the daemon. Warns once about a self-signed certificate.")
        }
    }

    Deployment_Node(host, "Panel host", "GNU/Linux with systemd") {
        Deployment_Node(unit, "kasapanel.service", "a unit, in the foreground") {
            Container(daemon, "Panel daemon", "Python 3.10 or newer", "Drops to www-data, httpd or nobody. Threads for connections, polling and the minute tick.")
            Container(helper, "Authentication helper", "Python, keeps root", "Reachable only over the inherited socketpair")
        }
        Deployment_Node(etc, "/etc/kasapanel", "root-owned, readable by the daemon") {
            ContainerDb(configfile, "config.json", "a JSON file", "The daemon's settings")
        }
        Deployment_Node(var, "/var/lib/kasapanel", "owned by the daemon account") {
            ContainerDb(state, "devices.json, devices, users", "JSON files", "Inventory, schedules, preferences")
            ContainerDb(tls, "tls", "PEM files", "Certificate and key")
        }
    }

    Deployment_Node(plugs, "Kasa device", "TP-Link firmware") {
        Container(device, "Smart plug, bulb or strip", "KLAP or the older protocol", "Discovery on UDP, commands on TCP")
    }

    Rel(spa, daemon, "HTTPS 8443")
    Rel(daemon, helper, "socketpair")
    Rel(daemon, device, "python-kasa over the LAN")
    Rel(daemon, configfile, "reads, rewrites on save")
    Rel(daemon, state, "atomic writes")
    Rel(daemon, tls, "loads, reloads on change")

    UpdateRelStyle(spa, daemon, $offsetX="-40", $offsetY="-30")
    UpdateRelStyle(daemon, helper, $offsetX="40", $offsetY="-10")
    UpdateRelStyle(daemon, device, $offsetX="-60", $offsetY="40")
    UpdateRelStyle(daemon, configfile, $offsetX="60", $offsetY="20")
    UpdateRelStyle(daemon, state, $offsetX="-120", $offsetY="60")
    UpdateRelStyle(daemon, tls, $offsetX="20", $offsetY="90")
```

## Rendering

The blocks above are the source. To get image files from them:

```sh
npm ci                 # at the top of the tree, not in frontend/
npm run diagrams       # SVG and PNG into build/diagrams/
```

`tools/render_diagrams.py` pulls each block out of this file, names it
from the `<!-- diagram: ... -->` comment above it, and hands it to
[mermaid-cli][mmdc], which drives a headless Chromium. The output is
not committed: GitHub renders the blocks directly, and a committed copy
would be one more thing to forget.

Two things the script does that a bare `mmdc` call does not:

- It sets a web-safe font stack for every C4 shape, because Mermaid's
  C4 theme asks for Open Sans and then embeds twenty `@font-face` rules
  carrying the whole typeface — about 350 kB of base64 in every file,
  and Open Sans's Apache-2.0 licence dragged into a GPL document for
  nothing. With the stack set, nothing refers to the embedded copy, and
  the script drops those rules: an SVG goes from 380 kB to about 40 kB
  and renders identically. If a future Mermaid uses the font for
  something, the check notices and the rules are kept.
- It names the block that failed, and the line it starts on. `mmdc`
  handed a whole Markdown file numbers the output and says only that
  something went wrong, which is no help with eleven diagrams in one
  file.

Chromium is not installed by `npm ci` here, because the project leaves
Puppeteer's download script denied — see `allowScripts` in
`package.json`, the same arrangement esbuild has in
`frontend/package.json`. The script looks for `chromium` and then
`google-chrome` on the path; to name a different one:

```sh
PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium npm run diagrams
```

The workflow does the same on GitHub's runners and uploads the images
as a build artifact. It fails if any block stops parsing, which is the
point: a diagram that no longer renders should not reach `main`
quietly.

[mmdc]: https://github.com/mermaid-js/mermaid-cli
