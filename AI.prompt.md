<!--
SPDX-FileCopyrightText: 2026 Kasa Panel contributors

SPDX-License-Identifier: GPL-3.0-or-later

SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
see docs/ai-bom.spdx.json for the AI usage declaration.

This file is the prompt that produces this project.  Everything below
the line is meant to be handed to a capable coding model as-is.  The
last requirement in it is the one that regenerates this file, so a
project built from this prompt can produce the prompt that builds it.
-->

# AI.prompt.md

Everything below this line is the prompt.

---

Build **Kasa Panel**, a threaded daemon that serves an HTTPS dashboard and
scheduler for TP-Link Kasa smart devices through
[python-kasa](https://python-kasa.readthedocs.io/).

Work in a directory called `kasapanel`. Write real files to disk, run what
you write, and fix what breaks before reporting back. Do not describe code
you have not executed.

## 1. Core requirements

1. A **threaded daemon**, built on the standard library's HTTP server, not a
   web framework. Multiple users must be able to use it at once.
2. **HTTPS only.** Generate a self-signed certificate on first run when none
   is configured, and require TLS 1.2 or better.
3. A **management tab** that both **scans the network for devices** and
   **adds a device by IP address or hostname**.
4. **Per-device schedules** written in a small language **based on Linux cron
   syntax**, with keywords such as `@daily`, at **minute resolution only**.
5. **Linux PAM** for authentication.
6. Store the **daemon configuration, the device list, and each device's own
   configuration in separate JSON files**.
7. Follow the **Google Python Style Guide**.
8. Score **above 9.5 with pylint**, using Google's own `pylintrc`.
9. Ship the **GPL-3 licence**, **SPDX file headers**, an **SPDX SBOM**, and
   **SPDX AI headers** with an AI bill of materials.

## 2. Layout

```
kasapanel/            the Python package
  jsonstore.py        atomic JSON reads and writes
  config.py           daemon settings and their defaults
  devicestore.py      the inventory and per-device documents
  cron.py             cron expression parsing, minute resolution
  actions.py          the device action vocabulary
  schedule.py         the per-device schedule language
  kasabridge.py       the boundary between threads and python-kasa's asyncio
  scheduler.py        the minute tick
  snooze.py           holding a schedule, and systemd.time parsing
  auth.py             PAM, sign-in policy, sessions
  activity.py         the in-memory activity ring buffer
  privsep.py          the privileged PAM helper and privilege dropping
  logsink.py          the log file handler
  filecheck.py        start-up writability checks
  certwatch.py        TLS certificate reloading
  metrics.py          the Prometheus endpoint
  app.py              the object that owns everything
  api.py              transport-free JSON API
  httpd.py            TLS, threads, static files, security headers
  certs.py            self-signed certificate generation
  cli.py              command line entry point
  static/             index.html, style.css, bundle.js, favicon.svg
frontend/             React sources, esbuild build, jsdom tests
tests/                Python unit tests
tools/make_sbom.py    regenerates the SBOM
packaging/            systemd unit
docs/ai-bom.spdx.json the AI bill of materials
AI.prompt.md          this prompt
```

## 3. Storage

Write JSON atomically: temporary file, `flush`, `fsync`, `os.replace`. Files
are mode `0600` and directories `0700`. A partially written file must never
be able to destroy a schedule.

Three kinds of file: `config.json`, an inventory at `devices.json`, and one
document per device under `devices/<device-id>.json` holding its schedule,
its enabled flag and its last known state.

Give each device a **random UUID** when it is added, and use it as the
file name. Nothing a device or a person supplies — host, MAC, alias,
model — may appear in a file or path name, not even as a hash: a name on
disk built from input is a name somebody else has a say in. Refuse an
identifier that is not a UUID rather than scrubbing it, so no string
arriving over HTTP can name a file; scrubbing turns bad input into some
other path, which is not the same as rejecting it. Keep identity across
a DHCP lease change in the record instead: match on MAC, then host, and
keep the identifier already held. An entry whose identifier is not a
UUID is simply a bad entry — log it and skip it, the same as any other
malformed one.

Running as root, default to `/etc/kasapanel/config.json` and
`/var/lib/kasapanel`; otherwise use the XDG directories, so the daemon can
be tried without touching system paths.

## 4. The schedule language

One rule per line, `<schedule> <action> [arguments]`, with `#` comments and
blank lines ignored. Implement Vixie cron semantics at minute resolution:

- five fields — minute, hour, day of month, month, day of week
- `*`, numbers, lists (`1,15`), ranges (`8-17`), steps (`*/15`, `8-17/2`)
- three-letter month and weekday names; Sunday is both `0` and `7`
- when day-of-month **and** day-of-week are both restricted, the rule fires
  when **either** matches
- keywords `@yearly` `@annually` `@monthly` `@weekly` `@daily` `@midnight`
  `@hourly`

There is deliberately **no `@reboot`**: rules that fire on start-up replay
themselves on every crash-restart, which is a surprising way to switch a
device on.

Actions: `on`, `off`, `toggle`, `brightness 1-100`, `temperature` in kelvin,
`colour <hue> <saturation> <value>`, `led on|off`, `refresh`, plus aliases
(`color`, `dim`, `poll` and so on).

Parsing must **collect every problem with its line number** rather than
stopping at the first, so the editor can show them all at once. Provide a
strict variant that raises, for saving.

The scheduler ticks half a second past each minute boundary, dispatches due
rules through a small thread pool, and deduplicates on
`(device_id, line_number, minute)`. A missed minute is skipped rather than
replayed, as cron does, with a warning when the gap is noticed. A rule that
fails is logged and does not stop the others.

### Snoozing

Any device's schedule can be **snoozed**: held until a moment, then left
to carry on. Store it as one `snoozed_until` timestamp in the per-device
document, so it survives a restart. The scheduler runs none of that
device's rules for a minute earlier than it; rules due in the meantime
are **skipped, not replayed**, for the same reason a missed minute is. When
the scheduler finds a snooze that has run out it removes it and says so
in the activity log, once — but only if the stored value is still the one
that ran out, so a snooze somebody set a moment ago is not cleared by a
thread that read the old one. A stored value that cannot be read counts
as no snooze: a hand edit must not be able to stop a schedule for good.
Snoozing never touches the device itself. While snoozed, the next rule
the dashboard shows is the first one after the snooze ends.

How long is written in **`systemd.time` syntax**, because anyone running
this daemon already reads it in unit files. Accept time spans (`45min`,
`2h 30min`, `1.5d`, `1w`, `+3h`, `3h left`; units case-sensitive, so `m`
is minutes and `M` months; a number with no unit is seconds) and
timestamps (`tomorrow 07:00`, `23:30`, `2026-12-26 09:00`, the `T` form,
a weekday that must match the date, a trailing `UTC`, `@epoch`). Two
deliberate departures: a **bare span is accepted** where systemd wants a
leading `+`, since "snooze for 2h" is what anybody means; and anything
**over 366 days is refused**, since that is a typo or a schedule that
should be disabled. Otherwise keep systemd's meaning — a bare `07:00` is
today, so once it has passed refuse it with a hint to write
`tomorrow 07:00` rather than quietly making it tomorrow. Anything in the
past is refused. Every failure is one exception type with a message fit
to show the operator.

## 5. Authentication

Check passwords with PAM through a configurable service, defaulting to
`login`. Support `allowed_users` and `allowed_groups`; when both are empty,
anyone PAM accepts may sign in. Lock an account out after a configurable
number of failures.

Sessions live in memory. Carry the session **two ways**: an
`HttpOnly; Secure; SameSite=Lax` cookie, and an `X-Kasa-Session` header
whose token is returned by the sign-in response. The header matters because
a browser that will not keep the cookie — behind an untrusted certificate,
or a policy that blocks it — otherwise signs in successfully and is refused
on the very next request. Require an `X-Kasa-CSRF` header on every write.

`python-pam` 2.0.2 imports `six` without declaring it, so a plain
`pip install python-pam` leaves an unimportable module. Guard the import,
report the actual cause rather than "not installed", name the interpreter in
the message, and declare `six` as a dependency.

**Log the reason for every 401** — whether anything arrived, whether the
token was simply unknown, how many sessions exist, when the daemon started,
and whether a proxy header is present — so a user bounced back to the login
page leaves evidence behind.

## 6. Privilege separation

PAM needs root; nothing else does. Start as root and separate the two.

Fork a **privileged helper process** before any thread exists and before
the listening socket is created. The helper keeps root, speaks a
length-prefixed JSON protocol over a socket pair, and does exactly one
thing: check a username and password against PAM. The daemon drops to an
unprivileged account and asks the helper whenever somebody signs in. The
policy — allow lists, lockout counters, sessions — stays on the
unprivileged side; the helper only ever answers yes or no.

It must be a process, not a thread. Linux keeps credentials per task,
but glibc broadcasts `setuid` to every thread on purpose, so a process
cannot be part root; and a root thread sharing an address space with the
web server would protect nothing anyway. Say so in the code, because the
next person will ask.

Add a `daemon_user` setting. Unset, fall back to `www-data`, then
`httpd`, then `nobody`. A configured account that does not exist is
fatal — **exit 1** — rather than a silent fallback somewhere unintended.
No account at all, not even `nobody`, is also **exit 1** with a message
saying what to do. When the effective account is `nobody`, however it
was chosen, put a **red banner across the top of the settings page** and
make **settings the landing page after sign-in**, once, so the operator
can still navigate away.

Order at start-up is the design: resolve the account, fork the helper,
bind the socket, hand the files over with `chown`, drop, and only then
start any thread. Verify the drop took and that root cannot be regained.
Warn when the TLS key will not be readable by the account afterwards,
since the certificate watcher has to read it after the drop.

Remember that an atomic write creates a temporary file **beside** its
target, so the account needs write permission on the directory holding
the configuration, not only on the file itself — otherwise saving
settings from the panel fails with a permission error at the moment
somebody tries it. Hand over only the directories that are genuinely the daemon's — its
state tree, the default configuration directory, the standard log
directory, and any directory it created this start — from an explicit
list rather than a guess at the name. "Any directory called kasapanel"
looks reasonable and quietly takes over a source checkout. Leave
everything else alone and warn at start up naming what will not work.

Check that the process actually holds `CAP_SETUID`, `CAP_SETGID`,
`CAP_CHOWN` and `CAP_FOWNER` **before** handing any file over, and fail
with a message naming `CapabilityBoundingSet` if not. A capability in the bounding set is not one the process holds: if what
is needed is in the permitted set but not the effective set, raise it
with `capset` rather than failing, and list the four as
`AmbientCapabilities=` in the unit so they arrive effective in the first
place. Clear every capability set explicitly after the drop and log what
is left, so ambient capabilities cannot outlive the privileges they came
with. Log the whole
privilege state — capability sets, NoNewPrivs, seccomp mode, whether the
process is in a user namespace — on every privileged start, and when a
drop is refused use it to name which of the four causes it is: a missing
capability, a user namespace from `DynamicUser=`/`PrivateUsers=` where
the target account does not exist, a syscall filter, or a security
module. "Operation not permitted" on its own sends people to check file
permissions, which is never the answer here. A service manager
can start a process as uid 0 and still strip the capabilities that make
root mean anything; every privileged call then fails with `EPERM`, which
reads like a file permission problem and sends people looking in
entirely the wrong place.

In the unit file use `ReadWritePaths=` rather than `StateDirectory=` and
`ConfigurationDirectory=`: those reset the ownership of their
directories to the unit's `User=` on every start, so systemd and the
daemon end up fighting over the same directories at every boot.

Refuse a `daemon_user` that is root, or has uid 0 or gid 0, **before**
handing any file over: accepting it would chown everything to root and
then fail the check that root cannot be regained, having changed
ownership on the way.

Hand the pid file to the account too, and create its directory if it is
missing so that the account owns that as well: removing a file needs
write permission on the directory holding it, not on the file, so owning
the pid file alone does not make it removable. When the directory is one
the account cannot write, **log a warning at start up** naming it, and
tolerate the failed unlink at shutdown rather than dying on the way out.

At start up, and **after the drop**, try every file the daemon writes:
the configuration, the inventory, the per-device schedule files, the log
file and pid file when configured, plus the TLS pair tested for reading.
Check the directory as well as the file, since an atomic write needs to
create a temporary file beside the target. Decide access by asking the
kernel as the daemon account -- fork, become it, call `access` -- rather
than by reading `st_mode`: POSIX ACLs are how certificates are usually
distributed to a service account, and a key that reads `0600 root:root`
with `setfacl -m u:www-data:r` on it is readable. Judging by mode bits
raises a false alarm about a correct deployment, which is worse than not
checking. Record whether each path was
configured or is a default, because the two need different fixes. Log
the findings, put them in the activity log, and show them in a warnings
box across the foot of the settings page. Running the check before the
drop would be worse than not running it: root can write anything, so it
would report health for a daemon that cannot save a setting.

Watch the helper from a thread that blocks on `waitpid`. If it dies,
say how — signal or exit status — and **bring the daemon down with a
non-zero exit**, so the service manager can start a whole one. An
unprivileged process cannot fork a new root helper, and a daemon nobody
can sign in to is not worth keeping alive. A deliberate shutdown must
not be mistaken for this, and must still exit zero.

The systemd unit runs as root so the daemon can drop for itself, sets a
start limit so a repeatedly dying helper does not restart in a loop, and
keeps `@system-service` (which already permits `@setuid`) with
`CAP_SETUID` and `CAP_SETGID` in the bounding set.

## 7. Logging

The activity log is a bounded in-memory ring buffer, and it is also a
**source for the ordinary daemon log**: every record it stores is written
through `logging` at a matching level, so activity appears alongside
everything else and follows it to a file, a terminal or the journal.

When a log file is configured, **check before every single line** that the
path still names the file that is open, and reopen it if it does not,
recreating the directory if necessary. Rotation, deletion and a removed
directory must all be survivable. A log that cannot be written falls back to
stderr and must never take the daemon down.

## 8. TLS certificate reloading

Renewal happens outside the daemon and must not require a restart, because a
restart drops every session.

Watch the configured certificate file and compare it with the certificate
the server is actually serving. Check **every fifteen minutes during the
last 48 hours before the live certificate expires**, and **every minute once
it has expired**. Before that window, stay quiet. Also act immediately on
**SIGHUP**.

When the file differs, load it into the **live `ssl.SSLContext`**: Python
hands the context's certificate to each connection as it is accepted, so
re-keying the context is enough. Connections already open finish under the
old certificate, new ones get the new certificate, the listening socket is
never rebound and no session is lost.

Loading a certificate and key into a live context is **not atomic** —
OpenSSL takes the certificate first, and a key that does not match leaves
the context unable to complete any handshake at all. So prove the pair in a
throwaway context first and only then load it into the live one. Treat a
half-written file as "try again later". Record reloads in the activity log.

## 9. HTTP and the API

Keep the API transport-free: decode a request into a plain object, route it,
return a response object, and let the HTTP layer do sockets. This is what
makes the API testable without a network.

Endpoints for session, dashboard, devices (list, add, patch, delete),
per-device action and refresh, per-device schedule (get, put), per-device
snooze (`POST` with `{for}` in systemd.time syntax, `DELETE` to resume), a
schedule check that validates without saving, a snooze check that resolves
a time without setting it, scan, settings, activity, sessions, a
reference describing the language, and an unauthenticated `/healthz`.

Map domain exceptions to status codes: unknown device 404, bad schedule or
action 400, device unreachable 502, auth 401, storage 500, and 405 when the
path exists under another method.

Serve an unauthenticated Prometheus endpoint at `/metrics` in the
standard text exposition format. **A scrape must never poll a device**:
render from the cached snapshots the poller and scheduler leave behind,
so a monitoring system cannot turn into device traffic and an asleep
device cannot hold a scrape open. Serve stale readings as they are and
publish their age beside them; a device never polled gets its inventory
and schedule facts and no state, rather than zeroes. Publish no user
names, passwords, key material or tokens — session counts, not who holds
them. Give it an off switch in the configuration, since it is
unauthenticated.

Send `Cache-Control: no-store` on API responses and a strict
Content-Security-Policy — `default-src 'none'`, `script-src 'self'`,
`style-src 'self'` — with `X-Content-Type-Options`, `X-Frame-Options: DENY`
and `Referrer-Policy`. Guard static file serving against directory escape.
Never return the stored device password; report only whether one is set.

## 10. The interface

A **React** application, sources under `frontend/src`, bundled with esbuild
into `kasapanel/static/bundle.js`. Commit the bundle so installing needs
Python only. No inline script or style anywhere, because the CSP forbids it.

Five tabs: **Panel** (a card per device with a two-position rocker switch,
sliders where the device supports them, live readings), **Schedules** (a
text editor per device, problems listed with line numbers, the next five
firings previewed), **Devices** (scan, add by address, rename, disable,
remove), **Activity**, and **Settings**.

Give it a considered visual identity rather than a default one. The
reference implementation is an enamelled electrical control cabinet: a
sage-grey ground, hairline rules, engraved uppercase monospace labels, and
one energised green lamp per outlet. The rocker is the single flourish; the
stylesheet keys it off `aria-pressed`, so the accessible state and the
visible state cannot drift apart. Everything else stays quiet. Where a row
shows several fields — the next-firings list, for instance — give them real
columns with spacing, not adjacent spans that run together.

Each card's actions row ends, next to **LED off**, with a **Snooze**
menu: 5, 15 and 30 minutes, 1, 3, 6 and 12 hours, 1 day, 1 week, and
**Custom…**. The presets are sent as systemd.time spans (`5min`, `1w`), so
there is one parser and it is on the server. While a snooze is in force
the button reads **Snoozed**, the card says until when in the attention
colour (a held schedule is not a fault, so not red), and the menu leads
with **Resume schedule now**. **Custom…** opens a modal dialog that takes a
systemd.time value, asks the snooze check what it means as the operator
types and shows either when the schedule resumes or what is wrong,
refuses to submit a bad value, and lists worked examples — dated ones
made fresh by the server so they never go stale — that fill the field
when clicked. Render the dialog into the body through a portal, so a
dimmed disabled card does not dim it, and keep its focus handling out of
the re-render every poll causes, or the field loses focus mid-word.

The foot of the side rail shows the **Kasa Panel and python-kasa
versions** beside the **Sign out** button, taken from the dashboard
payload; python-kasa's comes from its distribution metadata.

Poll the dashboard every five seconds, and not at all while the tab is
hidden.

Settings shows which account the daemon runs as and whether the PAM
helper is a separate process, and carries the `nobody` banner described
above. Under the files card it summarises the TLS certificate — subject,
issuer, whether it is self-signed, validity dates, fingerprint — with a
**live countdown to expiry** that ticks by the second in the last two
days and by the minute otherwise, and turns red once inside 48 hours.

## 11. Testing

Python: unit tests for the cron parser and its edge cases, the schedule
language, the snooze syntax (every menu preset, spans, timestamps, the
past, nonsense, the ceiling) and the scheduler skipping a snoozed device
and tidying up after it, atomic storage, config coercion, the device
store, the API (routing, both session carriers, CSRF, error mapping,
snoozing), PAM diagnosis and
sign-in policy, the log handler under rotation and deletion, and the
certificate watcher — including a **live HTTPS server whose certificate is
swapped underneath it while a connection is open**.

Also cover privilege separation: the account fallback chain and its two
fatal cases, the helper protocol including a request it must refuse and
a PAM failure it must survive, and that the authenticator still applies
its own policy when the check is delegated.

Interface: run the **built bundle** in jsdom against a stand-in daemon that
answers with payloads recorded from the real API and enforces auth the way
the daemon does. One test must mount the app with cookie storage disabled
and assert the panel still works, because that is the failure the header
carrier exists for. Another must assert that a daemon running as
`nobody` lands on the settings page with the banner showing, and that
the operator can still navigate away from it. Others must open the
snooze menu and find every length, set and lift a snooze, drive the
custom dialog through a refused value and an example to a set snooze,
and find both versions beside the sign-out button.

Everything must pass, and pylint must stay above 9.5 across the package, the
tests and the tools.

## 12. Packaging and provenance

`pyproject.toml` with a `kasapanel` console script and the static files as
package data. A hardened systemd unit that runs in the foreground and lets
systemd own restarts.

A CLI with `run` (`--host`, `--port`, `--daemonize`, `--pidfile`), `check`
(validates the configuration and every schedule, reports the PAM
environment and the certificate fingerprint, exits non-zero on problems),
`paths`, and `gen-cert`.

Every source file carries `SPDX-FileCopyrightText`,
`SPDX-License-Identifier: GPL-3.0-or-later`, and an `SPDX-FileContributor`
line recording AI assistance and pointing at `docs/ai-bom.spdx.json`. Ship
the full GPL-3 text.

Generate the SBOM with a script so it stays accurate: SPDX 2.3 JSON, every
shipped file with SHA-256 and SHA-1 checksums, a real package verification
code, and package URLs for dependencies. React and ReactDOM are MIT code
**bundled into** `bundle.js`, so relate them with `CONTAINS` and license
that file `GPL-3.0-or-later AND MIT`; esbuild and jsdom are build- and
test-only.

Write `docs/ai-bom.spdx.json` as an SPDX 3.0 AI bill of materials with an
`ai_AIPackage`: which model, what it was used for, what review it received,
what its limitations are, and that no model or model service is part of the
running daemon.

Write a `README.md` covering installation, first run, the certificate
fingerprint check, PAM and the shadow group, the schedule language, the file
layout, the settings, the API, the frontend build, and troubleshooting for
both PAM failures and sign-in bounces.

## 13. This file

Finally, write **`AI.prompt.md`** at the top level of the project: the
full text of a prompt that would generate the current version of Kasa
Panel, including this requirement — that `AI.prompt.md` contains a
prompt that generates the current version of Kasa Panel, including the
requirement to write `AI.prompt.md`.

Have the test suite check that it exists, that it asks for itself, and
that it has not fallen behind the modules in the package.

Keep it faithful to what was actually built. When the project changes,
change this file with it: a prompt that no longer produces the project is
worse than none, because it looks authoritative while being wrong. Record
the decisions someone would otherwise have to rediscover — why there is no
`@reboot`, why the session travels in a header as well as a cookie, why the
certificate pair is proved in a throwaway context first — since those are
the parts a fresh attempt gets wrong.
