<!--
SPDX-FileCopyrightText: 2026 Kasa Panel contributors

SPDX-License-Identifier: GPL-3.0-or-later

SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
see docs/ai-bom.spdx.json for the AI usage declaration.
-->

# Kasa Panel

A threaded daemon that puts an HTTPS dashboard in front of your TP-Link
Kasa devices, and runs a per-device schedule written in a small cron-like
language. Sign-in goes through Linux PAM, so the accounts are the ones
already on the host. Everything it knows lives in plain JSON files you can
read, diff and back up.

Built on [python-kasa](https://python-kasa.readthedocs.io/).

## What it does

- **Panel** — one card per device with a rocker switch, brightness and
  colour-temperature sliders where the device has them, and live readings.
- **Schedules** — a text editor per device. Errors are reported with line
  numbers, and the next five firings are previewed before you save.
- **Devices** — broadcast scan for devices on the LAN, or add one by IP or
  hostname. Rename, disable, remove.
- **Activity** — a bounded log of what the daemon and its users did.
- **Settings** — everything from `config.json`, editable in the browser.

Multiple people can be signed in at once; the server is threaded and the
device layer is shared and lock-guarded.

## Requirements

- Linux, Python 3.10 or newer
- `python-kasa`, `python-pam`, `cryptography`, `six`
- PAM read access to the shadow file (see [Authentication](#authentication))

`six` is on that list because python-pam 2.0.2 imports it without
declaring it as a dependency: `pip install python-pam` on its own leaves
a package that raises `ModuleNotFoundError: No module named 'six'` when
the daemon tries to use it. Installing from `requirements.txt` or from
the wheel pulls it in for you.

## Install

```sh
git clone <your-fork> kasapanel && cd kasapanel
python3 -m venv /opt/kasapanel-venv
/opt/kasapanel-venv/bin/pip install .
```

Or run straight from the working tree:

```sh
pip install -r requirements.txt
python3 -m kasapanel --help
```

## First run

```sh
sudo kasapanel --config /etc/kasapanel/config.json run
```

On first start the daemon writes a default `config.json`, creates its state
directory, and generates a self-signed TLS certificate. It prints the
listening URL; open it and sign in with a local account.

Your browser will warn about the certificate, because nothing signed it.
Check the fingerprint rather than clicking through blind:

```sh
kasapanel gen-cert          # prints the SHA-256 fingerprint
```

To use a real certificate instead, set `tls_certificate` and `tls_key` in
the settings tab or the config file and restart.

Other commands:

| Command | What it does |
| --- | --- |
| `run` | run the daemon (`--host`, `--port`, `--daemonize`, `--pidfile`) |
| `check` | validate the config and every device schedule, then exit non-zero if anything is wrong |
| `paths` | print the resolved file paths as JSON |
| `gen-cert` | create or replace the self-signed pair (`--force`, `--days`) |

Run as root and the defaults are `/etc/kasapanel/config.json` and
`/var/lib/kasapanel`. Run as an ordinary user and it falls back to the XDG
config and state directories, which is the easy way to try it out.

## Privileges

PAM has to read `/etc/shadow`, which needs root. Nothing else here does.
Rather than run the whole daemon as root for the sake of one function,
Kasa Panel separates the two.

Start it as root and it will, in this order: work out which account to
become, fork a small **privileged helper process**, bind the listening
socket, hand its files to the account, and drop privileges permanently.
Only the helper keeps root, and the only thing it can be asked is
whether a password is correct.

```
$ ps -o pid,ppid,user,args -C python3
  799   793 www-data  python3 -m kasapanel ... run     <- HTTPS, devices, files
  801   799 root      python3 -m kasapanel ... run     <- PAM only
```

This is a process, not a thread, and it has to be. Linux keeps
credentials per task, but glibc deliberately broadcasts `setuid` to
every thread in a process, so a process cannot be part root. Even if
that were bypassed, a root thread sharing an address space with the web
server would protect nothing: anyone who can run code in the process can
reach that thread. Isolation needs an address space of its own — the
same reason OpenSSH and Postfix are built this way.

The policy stays on the unprivileged side. The helper answers yes or no;
the allow lists, the lockout counter and the sessions all live in the
daemon.

**If the helper dies, the daemon stops.** It cannot be started again
from an unprivileged process — that privilege is exactly what was given
up — and a daemon nobody can sign in to is not worth keeping alive. So
the daemon logs how the helper ended and exits non-zero, and the service
manager starts a whole one:

```
CRITICAL kasapanel.privsep: authentication helper (pid 646) has gone:
         the helper was killed by SIGKILL, which usually means the
         out-of-memory killer or an administrator
CRITICAL kasapanel.cli: the authentication helper has stopped (...);
         shutting down so the daemon can be restarted whole
```

A deliberate stop is not treated this way: shutting down closes the
helper on purpose and exits zero.

### Choosing the account

Set `daemon_user` to a dedicated account. With nothing set, the daemon
tries `www-data`, then `httpd`, then `nobody`:

| Situation | What happens |
| --- | --- |
| `daemon_user` names an existing account | it is used |
| `daemon_user` names a missing account | **exit 1** — no silent fallback |
| `daemon_user` names root, or any uid 0 or gid 0 account | **exit 1**, before anything is chowned |
| unset, `www-data` or `httpd` exists | that one is used |
| unset, neither exists | `nobody`, with a warning in the panel |
| running as `nobody` | red banner, and sign-in lands on Settings |
| `nobody` does not exist either | **exit 1** with a message |

`nobody` is a shared account that other services also use, so anything
it can touch, they can touch. The panel says so until it is changed:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin kasapanel
sudo chown -R kasapanel: /var/lib/kasapanel /etc/kasapanel
```

then set `daemon_user` to `kasapanel` and restart.

Run the daemon as an ordinary user instead and none of this happens:
there is nothing to drop and no helper is forked, so PAM is called in
process and will only work if that account can read the shadow file.

### Directories, not just files

An atomic write creates a temporary file beside its target and renames
it, so the daemon needs write permission on the **directory** holding
its configuration, not only on the file. The same goes for recreating a
log file after rotation, and for removing a pid file.

At start up the daemon hands over the directories that are genuinely
its own — its state tree, the default configuration directory,
`/var/log/kasapanel`, and any directory it created itself this start.
Everything else is left exactly as found, including a directory that
merely happens to be *called* `kasapanel`: a source checkout at
`/opt/kasapanel` is not the daemon's to take over. When a directory it
needs is not writable it says so rather than changing it:

```
WARNING kasapanel.cli: /tmp/panel is not writable by kasapanel, so settings
        saved from the panel will fail; give that directory to the daemon
        account, or move the file into a directory of its own
```

Keeping the configuration at `/etc/kasapanel/config.json` and the state
at `/var/lib/kasapanel` — the defaults — means none of this needs
thinking about.

### The file check

Every file the daemon writes is tried at start up, and the findings go
to the log, to the activity tab, and to a box across the foot of the
settings page:

```
WARNING kasapanel.filecheck: file check: the log file at
        /var/log/kasapanel/panel.log (configured) is not usable: the
        directory cannot be written, so the temporary file an atomic
        write needs cannot be created there; the log cannot be written
        or recreated after rotation
```

It runs **after privileges are dropped**, which is the only moment the
answer means anything: root can write everything, so a check before the
drop would report a clean bill of health for a daemon that cannot save a
setting. `kasapanel check`, which runs as whoever invoked it, asks the
same questions on behalf of the daemon account instead.

Access is decided by the kernel, not by reading `st_mode`, so **ACLs
count**. A key distributed with

```sh
setfacl -m u:www-data:r /etc/ssl/private/panel.key
```

still reads `0600 root:root`, and is readable all the same. Judging it
by its mode bits would raise a false alarm about a perfectly good
deployment. Each finding says whether the path was **configured** or is a
**default**, because the two call for different fixes — a path somebody
chose usually needs a permission changed, while a default that does not
work usually means the daemon is running somewhere it was not set up
for.

Checked: the configuration file, the device inventory, the per-device
schedule files, the log file and the pid file when configured, and the
TLS certificate and key, which are tested for reading rather than
writing because the certificate watcher has to read them after the drop.

`kasapanel check` reports the same list, but as whoever runs it — so
run it as the daemon account, or read the daemon's own log, if the two
differ.

### The pid file

`--pidfile` is not needed under systemd, which tracks the process
itself. If you use one, put it somewhere the daemon account owns:

```sh
kasapanel --config /etc/kasapanel/config.json run \
    --pidfile /run/kasapanel/kasapanel.pid
```

The daemon creates that directory and hands it to `daemon_user` along
with the file. Owning the file is not enough on its own — removing it
needs write permission on the *directory* — so a pid file dropped
straight into `/run` or `/var/run` is written but cannot be cleaned up
afterwards. The daemon says so at start up rather than leaving you to
find out at shutdown:

```
WARNING kasapanel.cli: /run is not writable by kasapanel, so the pid file
        will be left behind when the daemon stops; put the pid file in a
        directory of its own, such as /run/kasapanel
```

A pid file it cannot remove is untidy, not fatal: the daemon warns again
on the way out and exits normally.

### The TLS key after dropping

The certificate watcher reloads a renewed certificate *after* privileges
have been dropped, so the key has to stay readable by the daemon
account. A key under `/etc/letsencrypt/live` is not, by default.

Any of these work, and the daemon checks by asking the kernel as that
account, so all three are recognised:

```sh
setfacl -m u:www-data:r /etc/ssl/private/panel.key   # an ACL
chgrp ssl-cert /etc/ssl/private/panel.key            # a shared group
install -o www-data -m 0400 ... # a copy, from a deploy hook
```

`kasapanel check` reports what the daemon account can actually reach.

## Authentication

Passwords are checked by PAM through the service named in `pam_service`
(`login` by default). PAM needs to read `/etc/shadow`, so either run the
daemon as root, or run it as a dedicated user that belongs to the `shadow`
group.

By default any account PAM accepts may sign in. Narrow that with
`allowed_users` and `allowed_groups` — if either list is non-empty, an
account must match one of them. Repeated failures lock an account out for
`lockout_seconds`.

Sessions are held in memory (so a restart signs everyone out), the cookie is
`HttpOnly; Secure; SameSite=Strict`, and every write request must carry the
session's CSRF token in the `X-Kasa-CSRF` header.

## The schedule language

One rule per line, per device:

```
<schedule> <action> [arguments]
```

Blank lines and everything after `#` are ignored. Times come from the
daemon host's local clock, to the minute — there is no second field, and
never will be.

```sh
# Weekday mornings
0 6 * * mon-fri      on
30 22 * * *          off

# Every half hour, ask the device how it is doing
*/30 * * * *         refresh

# Warm and dim for the evening
0 21 * * *           brightness 25
0 21 * * *           temperature 2700

@daily               off        # midnight
```

### Schedule fields

`minute hour day-of-month month day-of-week`, Vixie cron semantics:

| Field | Range |
| --- | --- |
| minute | 0–59 |
| hour | 0–23 |
| day of month | 1–31 |
| month | 1–12, or `jan`–`dec` |
| day of week | 0–7 (0 and 7 are both Sunday), or `sun`–`sat` |

Each field takes `*`, a number, a list (`1,15`), a range (`8-17`), or a
step (`*/15`, `8-17/2`). As in cron, when *both* day-of-month and
day-of-week are restricted the rule fires when **either** matches.

Keywords: `@yearly` `@annually` `@monthly` `@weekly` `@daily` `@midnight`
`@hourly` — each a shorthand for an ordinary expression.

There is no `@reboot`. A rule that fires on start-up fires again on every
restart, and with `Restart=on-failure` a crash loop would replay it each
time; switching a device on is not something that should happen as a side
effect of the daemon falling over.

### Actions

| Action | Argument | Notes |
| --- | --- | --- |
| `on`, `off`, `toggle` | — | |
| `brightness` | 1–100 | dimmable bulbs and light strips |
| `temperature` | 1000–12000 | colour temperature in kelvin |
| `colour` | hue sat value | 0–360, 0–100, 1–100 |
| `led` | `on` \| `off` | the little status LED on plugs |
| `refresh` | — | poll the device now |

Aliases: `color`, `color_temp`, `colour_temp`, `dim`, `enable`, `disable`,
`poll`.

A rule that fails — device unplugged, timeout — is logged as an error and
does not stop the other rules. Missing a minute (the host was asleep, the
process was stopped) skips that firing rather than replaying it, which is
how cron behaves; the daemon logs a warning when it notices a gap.

## Files

```
/etc/kasapanel/config.json          daemon settings
/var/lib/kasapanel/devices.json     the inventory
/var/lib/kasapanel/devices/<id>.json  one file per device: schedule, state
/var/lib/kasapanel/tls/panel.crt    self-signed certificate
/var/lib/kasapanel/tls/panel.key    private key, mode 0600
```

Each device is given a random UUID when it is added, and that is the
name of its file. Nothing a device or a person supplies — host, MAC,
alias, model — appears in a file or path name, not even as a hash: a
name on disk built from input is a name somebody else has a say in, and
the way to be sure that cannot happen is not to do it. An identifier
that is not a UUID is refused rather than scrubbed, so no string
arriving over HTTP can name a file at all.

Identity across a DHCP lease change is kept in the record instead: a
device is matched by MAC address, then by host, and keeps the identifier
it was given the first time. Writes are atomic: a
temporary file is written, flushed, and renamed over the target, so an
interrupted write cannot truncate your schedules. Files are mode 0600 and
directories 0700.

### Settings

Editable in the settings tab, or by hand in `config.json`:

| Key | Default | Meaning |
| --- | --- | --- |
| `listen_host` / `listen_port` | `0.0.0.0` / `8443` | where to listen |
| `tls_certificate` / `tls_key` | generated | paths to TLS material |
| `daemon_user` | empty | unprivileged account to run as; root is refused |
| `pam_service` | `login` | PAM service name |
| `allowed_users` / `allowed_groups` | empty | sign-in policy |
| `session_minutes` | `720` | idle timeout |
| `max_login_failures` / `lockout_seconds` | `5` / `300` | lockout policy |
| `poll_seconds` | `30` | how often to poll devices |
| `discovery_seconds` / `discovery_target` | `8` / broadcast | scan behaviour |
| `command_timeout_seconds` | `20` | give up on a device |
| `device_username` / `device_password` | empty | cloud credentials for newer firmware |
| `scheduler_enabled` | on | whether schedules run at all |
| `history_limit` | `500` | activity log size |
| `metrics_enabled` | on | serve the unauthenticated `/metrics` endpoint |
| `log_level` / `log_file` | `INFO` / stderr | logging |

Changes to the listen address, port and TLS paths need a restart; the tab
marks those. The device password is never sent back to the browser — the
API reports only whether one is set.

## Troubleshooting sign-in

**Signing in succeeds and then throws you straight back to the login
page.** The panel is refusing the request that follows sign-in. Since
version 1.0.2 the daemon logs the reason for every refusal, so look
there first:

```sh
journalctl -u kasapanel | grep refused
```

Three shapes, three causes:

- *no session cookie and no session header arrived* — nothing
  identifying reached the daemon. Check for a reverse proxy stripping
  headers, or a browser policy blocking cookies for the site. The panel
  sends the session both ways, so both have to fail for this to happen.
- *the token is not one this process issued* — the browser held a
  session the daemon no longer knows. Compare the `daemon started` time
  in the same line with when you signed in: if it is later, the daemon
  restarted and dropped its sessions, which is worth chasing in the log
  above. Sessions are held in memory by design.
- the request never appears at all — it is not reaching the daemon;
  look at the proxy.

The line also reports `via proxy` when an `X-Forwarded-For` header is
present, and the browser's user agent, which tells you whether the
request came from the page or from something else.

### PAM problems


Start with the daemon's own report, which is printed by `check` and
written to the log at startup:

```sh
kasapanel --config /etc/kasapanel/config.json check
```

It prints the interpreter it is running under, the python-pam module it
found, and the libpam the system resolves — or, when sign-in cannot
work, the reason and the exact command that fixes it.

**"python-pam is installed but cannot be imported: it needs the six
module"** — python-pam 2.0.2 imports `six` at import time but declares
no dependencies, so pip does not bring it along:

```sh
sudo python3 -m pip install six      # the interpreter check printed
```

**"python-pam is not installed for the interpreter running this
daemon"** — the package went to a different Python than the one running
the daemon. This is the usual result of installing into a virtualenv and
then running the system `kasapanel`, or of `pip install --user` as
yourself while the daemon runs as root. Compare the two:

```sh
head -1 $(command -v kasapanel)       # which interpreter it uses
kasapanel ... check | grep interpreter
sudo <that interpreter> -m pip install python-pam six
```

Under systemd the daemon's interpreter is whatever `ExecStart` points
at, which is not affected by your shell's virtualenv.

**"the system PAM library could not be loaded"** — python-pam imported
but `ctypes` could not find `libpam`. Install the system library
(`libpam0g` on Debian and Ubuntu) and make sure `ldconfig` is reachable
on the daemon's `PATH`.

**"the PAM service ... could not be used"** — the service named in
`pam_service` is misconfigured. Note that a *missing* service file is
not usually an error: PAM falls back to `/etc/pam.d/other`, which on
Debian and Ubuntu delegates to `common-auth` and therefore still works.
On a host where `other` denies everything, sign-in fails for everyone
until `pam_service` names a real file in `/etc/pam.d/`. The default,
`login`, exists nearly everywhere.

**Right password, still refused** — PAM cannot read the shadow file.
Run the daemon as root, or add its user to the `shadow` group. Check
with:

```sh
sudo -u kasapanel python3 -c "import pam; print(pam.pam().authenticate('you', 'secret'))"
```

## Logs

Everything goes to one place. The activity you see in the panel is not a
separate stream: each record is also written to the daemon log at its own
level, so `journalctl -u kasapanel` shows schedule firings and button
presses interleaved with everything else.

```
INFO  kasapanel.activity: [ada] Desk lamp: Switched on
ERROR kasapanel.activity: [schedule] Hall plug: refresh failed: timed out
WARN  kasapanel.certwatch: TLS certificate replaced without a restart
```

Set `log_file` to write to a file instead of the terminal or journal. The
handler checks, **before every line**, that the path still names the file
it has open, and reopens it if it does not — so `logrotate` needs no
`copytruncate` and no signal, and a deleted file or directory simply comes
back on the next line. If the file cannot be written at all, lines go to
stderr rather than taking the daemon down.

There is no rotation built in, deliberately: that is logrotate's job, and
the reopening behaviour above is what makes it safe.

## Certificate renewal

The daemon reloads a renewed certificate **without restarting**, so nobody
is signed out and no connection is dropped.

It watches the configured certificate file and compares it with the one it
is actually serving. To stay quiet on a healthy system it only looks when
it matters: **every fifteen minutes during the last 48 hours** before the
live certificate expires, and **every minute once it has expired**. If you
renew earlier than that, hurry it along:

```sh
systemctl reload kasapanel      # or: kill -HUP $(cat /run/kasapanel.pid)
```

When the file has changed, the new certificate is loaded into the running
TLS context. Connections already open finish under the old certificate;
everything accepted afterwards gets the new one. Sessions are untouched,
the listening socket is never rebound, and the process never restarts.

A certificate that does not match its key is refused before it can do any
harm — it is proved in a throwaway context first, because loading a
mismatched pair into a live context leaves it unable to complete any
handshake at all. A half-written file is treated the same way, and retried
on the next pass. Both cases are logged and appear in the activity tab.

## Devices the panel cannot reach

### "Unsupported device ... with encrypt_scheme ... encrypt_type='TPAP'"

The device is fine and the panel is fine; python-kasa does not
implement that protocol. Newer TP-Link firmware, on the KP125M among
others, has begun using an encryption scheme called TPAP. Support for
it is an unmerged pull request upstream
([python-kasa#1592](https://github.com/python-kasa/python-kasa/pull/1592)),
so no released python-kasa can talk to such a device at all — no
setting here will help, because the panel never gets far enough to try.

Check whether a newer python-kasa has shipped it since this was
written:

```sh
pip install --upgrade python-kasa
python -c "import kasa; print(kasa.__version__)"
```

If not, the options are to keep the device on older firmware if it has
not updated yet, control it through Matter or the TP-Link app instead,
or wait for the upstream work to land. The panel will pick it up the
moment python-kasa can.

### "Invalid authentication" or a device that answers but refuses

Newer devices want the credentials of the TP-Link account they are
bound to. Put the e-mail address and password into `device_username`
and `device_password` on the settings page. The password is stored in
`config.json` and is never sent back to the browser.

## Metrics

There is a Prometheus endpoint at `/metrics`. It needs no sign-in:

```yaml
scrape_configs:
  - job_name: kasapanel
    scheme: https
    tls_config:
      insecure_skip_verify: false
    static_configs:
      - targets: ['panel.example.com:8443']
```

**Scraping never polls a device.** Everything comes from the snapshot
cache the poller and the scheduler fill in, so a fifteen-second scrape
interval does not become fifteen-second traffic to every plug in the
house, and a device that is asleep cannot hold a scrape open. Readings
are therefore as old as the last scheduled poll, which
`poll_seconds` controls. Rather than hide that, each device publishes
`kasapanel_device_state_age_seconds` beside its readings — alert on it
if staleness matters to you. A device that has never been polled appears
with its inventory and schedule facts and no state at all, rather than
zeroes pretending to be readings.

What it publishes: daemon uptime and version, scheduler state and rules
run, session **count**, activity counts by level, PAM and helper health,
certificate expiry, and per device its name, address, model, whether it
is reachable and on, brightness, colour temperature, power, energy,
signal, schedule rule counts and next firing.

What it does not publish: no user names, no passwords, no key material,
no session or CSRF tokens. It does list device names and addresses,
which is what the numbers would be meaningless without — so if the
endpoint is reachable from somewhere you would rather it were not,
firewall it or set `metrics_enabled` to false.

### "cannot become www-data: Operation not permitted"

The daemon is running as uid 0 but does not hold the capabilities that
make root mean anything, so every privileged call fails with `EPERM` —
which reads like a file permission problem and is not one. The usual
signature is a chown failing for the same reason just before:

```
WARNING kasapanel.privsep: cannot give /var/lib/kasapanel to uid 33:
        [Errno 1] Operation not permitted (CAP_CHOWN is not held ...)
kasapanel: cannot become www-data: [Errno 1] Operation not permitted.
        this process is uid 0 but does not hold CAP_SETUID ...
```

Since 1.4.3 the daemon logs its own privilege state on every privileged
start, and the failure message says which of the four possible causes it
is:

```
INFO  kasapanel.privsep: privileged start: uid=0 CapEff=00000000a80425fb
      CapBnd=00000000a80425fb NoNewPrivs=1 Seccomp=2 userns=no
```

Read `CapEff` against `CAP_SETUID` (bit 7), `CAP_SETGID` (6),
`CAP_CHOWN` (0) and `CAP_FOWNER` (3), or just let the daemon say it —
`kasapanel check`, run as root, prints each one as yes or NO.

It is nearly always `CapabilityBoundingSet` in the unit file:

```sh
systemctl show kasapanel -p CapabilityBoundingSet
```

It must include `CAP_SETUID CAP_SETGID CAP_CHOWN CAP_FOWNER`.

**A capability in the bounding set is not the same as one the process
holds.** The bounding set is a ceiling; what the process can actually
use is its *effective* set, and on some systems the two differ:

```
CapEff=000000002000044d CapBnd=00000000200004cd
                  ^^ CAP_SETUID missing here    ^^ but allowed here
```

The daemon deals with this itself where it can — if the capability is
in the permitted set it raises it into the effective set and carries on,
which is what the permitted set is for. When it is not permitted
either, add this to the unit, which puts the four in the effective set
whatever the exec path did with them:

```
AmbientCapabilities=CAP_CHOWN CAP_FOWNER CAP_SETGID CAP_SETUID
```

It is in the shipped unit from 1.4.4. Ambient capabilities do not
survive the drop — changing away from uid 0 clears them, and the daemon
clears every set explicitly afterwards and logs what is left, which is
nothing:

```
INFO kasapanel.privsep: running as www-data (uid 33, gid 33),
     capabilities cleared
```

If you are running a unit from an older release, replace it with the one
in `packaging/` — and check for drop-ins, which override it:

```sh
systemctl cat kasapanel        # shows the unit and every drop-in
```

Since 1.4.2 the daemon checks this before it changes the ownership of
anything, so a unit missing them fails immediately and leaves the files
alone.

If the capabilities *are* held and the drop is still refused, the
message says so and names the remaining suspects: `DynamicUser=` or
`PrivateUsers=`, which put the daemon in a user namespace where the
target account does not exist and cannot be used with a daemon that
drops privileges itself; a `SystemCallFilter=` that does not allow
`@setuid`; or a security module such as AppArmor or SELinux, which
leaves a denial in the audit log at that moment.

### The state directory keeps reverting to root

If `/var/lib/kasapanel` is `www-data` before a start and `root` after
it, the daemon did not do that — systemd did. `StateDirectory=`,
`ConfigurationDirectory=` and `LogsDirectory=` reset the ownership and
mode of the directories they manage on **every start**, to the unit's
`User=`, which for this daemon is root. The daemon then chowns them
back to its own account, so the two fight over the same directories at
every boot, and the daemon loses whenever it lacks `CAP_CHOWN`.

The unit in `packaging/` uses `ReadWritePaths=` instead, which makes
the same directories writable through `ProtectSystem=strict` without
touching their ownership. Create them once:

```sh
sudo install -d -o root -g root -m 0755 /etc/kasapanel /var/lib/kasapanel
```

and the daemon takes it from there. An older unit with
`StateDirectory=kasapanel` will keep undoing itself.

## Running as a service

```sh
sudo cp packaging/kasapanel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kasapanel
journalctl -u kasapanel -f
```

The unit runs the daemon in the foreground and lets systemd own restarts
and logging. Points worth knowing:

- **`User=root` is deliberate.** The daemon drops privileges itself, to
  `daemon_user`, after forking the PAM helper. Setting `User=` here
  would leave PAM unable to read the shadow file.
- `Restart=on-failure` covers the helper-death exit described above.
  `StartLimitBurst=5` in five minutes stops it restarting in a loop if
  something is killing the helper repeatedly — after that the unit stays
  down for somebody to look at.
- `ExecReload` sends SIGHUP, which rechecks the certificate file. Point
  a certbot deploy hook at `systemctl reload kasapanel`.
- `SystemCallFilter=@system-service` already includes `@setuid`, so the
  privilege drop is allowed; `CAP_SETUID` and `CAP_SETGID` are in the
  bounding set for the same reason. Nothing is left ambient, so the
  daemon holds no capability once it has dropped.
- `NoNewPrivileges=yes` is safe because the helper is root and reads the
  shadow file directly. It would have to come off only if you ran the
  daemon unprivileged with no helper, where PAM needs to execute
  `unix_chkpwd`.
- `LogsDirectory=kasapanel` exists so `log_file` can point at
  `/var/log/kasapanel/`, which `ProtectSystem=strict` would otherwise
  make read-only.
- The UDP broadcast that device discovery needs is allowed on private
  ranges only.

## HTTP API

Everything the browser does is a JSON request under `/api/`, so scripting
it is easy. Sign in for a cookie and a CSRF token, then send the token on
writes.

```
POST   /api/session                  sign in
DELETE /api/session                  sign out
GET    /api/dashboard                devices, state, next runs, summary
GET    /api/devices                  inventory
POST   /api/devices                  add {host, alias}
GET    /api/devices/<id>             one device
PATCH  /api/devices/<id>             rename, enable, annotate
DELETE /api/devices/<id>             remove
POST   /api/devices/<id>/action      {action, arguments}
POST   /api/devices/<id>/refresh     poll now
GET    /api/devices/<id>/schedule    the script and its summary
PUT    /api/devices/<id>/schedule    save a script (400 if invalid)
POST   /api/schedule/check           validate without saving
POST   /api/scan                     broadcast discovery
GET    /api/settings                 redacted settings
PUT    /api/settings                 change settings
GET    /api/activity                 recent log records
GET    /api/sessions                 who is signed in
GET    /api/reference                the action and keyword vocabulary
GET    /healthz                      unauthenticated liveness probe
GET    /metrics                      unauthenticated Prometheus metrics
```

## Design

Threads, not a framework. python-kasa is asyncio, so one thread owns a
single event loop and every device object; the HTTP threads hand it
coroutines and wait with a timeout. A minute-tick thread half a second past
each boundary runs due schedule rules through a small thread pool, and a
poller thread keeps the dashboard fresh.

```
cli / __main__            entry points
httpd                     TLS, threads, static files, security headers
api                       transport-free JSON routing
app                       the one object that owns everything
auth  activity  scheduler  sessions, PAM, log, minute ticks
kasabridge                the async boundary
schedule  cron  actions   the schedule language
devicestore  config       JSON persistence
jsonstore                 atomic writes
```

The interface is a React application. The sources live in `frontend/src`
and are bundled by esbuild into `kasapanel/static/bundle.js`, which is
committed — installing the daemon needs Python only, never Node. There is
no inline script or style anywhere, because the Content-Security-Policy
the server sends does not allow any.

## Development

```sh
pip install -r requirements-dev.txt
pylint kasapanel tests tools        # 10.00/10 against Google's pylintrc
python3 -m unittest discover -s tests -t . -v
python3 tools/make_sbom.py          # regenerate sbom.spdx.json
```

Changing the interface needs Node:

```sh
cd frontend
npm install
npm run build      # rewrites ../kasapanel/static/bundle.js
npm run watch      # rebuild on save
npm test           # runs the built bundle in jsdom against recorded
                   # API payloads, including a browser that refuses cookies
```

Commit the rebuilt `bundle.js` along with the source change.

`AI.prompt.md` holds a prompt that regenerates this project. It is meant
to stay accurate: if you change what the daemon does, change it to
match, or it will confidently describe something that no longer exists.
`tests/test_prompt.py` guards the obvious failure — it fails when a
module is added that the prompt does not mention.

The code follows the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html);
`.pylintrc` is Google's own, with `missing-function-docstring` put back.

## Licensing and provenance

GPL-3.0-or-later. The full text is in [LICENSE](LICENSE), and every source
file carries an SPDX identifier.

- `sbom.spdx.json` — SPDX 2.3 software bill of materials, generated by
  `tools/make_sbom.py`, with a checksum for every shipped file and package
  URLs for the runtime dependencies.
- `docs/ai-bom.spdx.json` — SPDX 3.0 AI bill of materials. This code base
  was drafted with AI assistance, and that document says which model, what
  it was used for, what review it received and what its limitations are.
  The `SPDX-FileContributor` line in each file points at it.

No model, weights or model provider is part of the running daemon. Kasa
Panel talks to your devices on your LAN and to nothing else.
