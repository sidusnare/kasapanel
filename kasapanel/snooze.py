# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Snoozing a device's schedule, and the time syntax that says how long.

A snooze is a single instant stored in the per-device file: until then
the scheduler runs none of the device's rules, and after it the schedule
carries on as if nothing had happened.  Rules that would have fired
during the snooze are skipped, not replayed, just as a missed minute is.

How long is written the way systemd writes it (``man systemd.time``),
because anyone who runs this daemon already reads that syntax in unit
files and ``systemd-run --on-active``.  Two forms are accepted:

* a time span -- ``90min``, ``2h 30m``, ``1.5d``, ``1w``, ``+3h`` --
  counted from now;
* a timestamp -- ``tomorrow 07:00``, ``2026-12-25 09:30``, ``Fri 18:00``
  (only when today is Friday, as in systemd), ``@1767225600``, with an
  optional trailing ``UTC``.

A bare span is not a timestamp in systemd, where it needs a leading
``+``, but "snooze for 2h" is what a person means here, so both are
accepted.  Everything else keeps systemd's meaning: a bare ``07:00`` is
today at seven, so once seven has passed it is refused with a hint to
say ``tomorrow`` rather than silently becoming tomorrow.  Resolution is
the minute, like the scheduler; seconds are kept but rarely matter.
"""

import datetime
import re
from typing import Any, Dict, Optional, Tuple

# Seconds per unit, as systemd.time defines them, months and years
# included.  Unit names are case-sensitive: "m" is minutes, "M" months.
UNIT_SECONDS: Dict[str, float] = {}
for _names, _seconds in (
        (('usec', 'us', 'µs', 'μs'), 1e-6),
        (('msec', 'ms'), 1e-3),
        (('seconds', 'second', 'sec', 's'), 1),
        (('minutes', 'minute', 'min', 'm'), 60),
        (('hours', 'hour', 'hr', 'h'), 3600),
        (('days', 'day', 'd'), 86400),
        (('weeks', 'week', 'w'), 7 * 86400),
        (('months', 'month', 'M'), 30.44 * 86400),
        (('years', 'year', 'y'), 365.25 * 86400)):
    for _name in _names:
        UNIT_SECONDS[_name] = _seconds

# Longest snooze accepted.  Anything past this is far more likely to be
# a typo than an intention, and a schedule silently off for a decade
# is not a thing anyone should have to discover.
MAX_SNOOZE = datetime.timedelta(days=366)

# Offered in the interface as worked examples of the syntax.  Dated
# examples are made up fresh by :func:`reference` so they never go stale.
SPAN_EXAMPLES: Tuple[Tuple[str, str], ...] = (
    ('45min', 'forty-five minutes from now'),
    ('2h 30min', 'two and a half hours from now'),
    ('3d', 'three days from now'),
    ('2w', 'two weeks from now'),
    ('tomorrow 07:00', 'seven tomorrow morning'),
    ('23:30', 'half past eleven tonight'),
)

_SPAN_PART = re.compile(r'\s*(\d+(?:\.\d*)?|\.\d+)\s*([A-Za-zµμ]*)')

_WEEKDAYS = {
    name: index
    for index, names in enumerate((
        ('mon', 'monday'), ('tue', 'tuesday'), ('wed', 'wednesday'),
        ('thu', 'thursday'), ('fri', 'friday'), ('sat', 'saturday'),
        ('sun', 'sunday')))
    for name in names
}

_DATE = re.compile(r'^(\d{4})-(\d{1,2})-(\d{1,2})$')
_TIME = re.compile(r'^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$')


class SnoozeError(ValueError):
    """Raised when a snooze time cannot be understood or is unusable."""


def parse_span(text: str) -> datetime.timedelta:
    """Parses a systemd time span such as ``2h 30min``.

    Components are added together, and a number without a unit is
    seconds, as in systemd.

    Args:
        text: The span.

    Returns:
        The duration.

    Raises:
        SnoozeError: If the text is not a time span.
    """
    source = text.strip()
    if not source:
        raise SnoozeError('say how long to snooze for')
    total = 0.0
    position = 0
    while position < len(source):
        match = _SPAN_PART.match(source, position)
        if match is None or match.end() == position:
            raise SnoozeError(f'"{text.strip()}" is not a time span')
        number, unit = match.groups()
        if unit and unit not in UNIT_SECONDS:
            raise SnoozeError(f'"{unit}" is not a unit of time')
        total += float(number) * UNIT_SECONDS[unit or 's']
        position = match.end()
        while position < len(source) and source[position].isspace():
            position += 1
    try:
        return datetime.timedelta(seconds=total)
    except OverflowError as err:
        raise SnoozeError(f'"{text.strip()}" is far too long') from err


def _clock(text: str) -> Optional[datetime.time]:
    """Parses ``HH:MM`` or ``HH:MM:SS``.

    Args:
        text: The candidate time of day.

    Returns:
        The time, or None when the text is not a time of day.

    Raises:
        SnoozeError: If it looks like a time but is out of range.
    """
    match = _TIME.match(text)
    if match is None:
        return None
    hour, minute, second = (int(part or 0) for part in match.groups())
    try:
        return datetime.time(hour, minute, second)
    except ValueError as err:
        raise SnoozeError(f'"{text}" is not a time of day') from err


def _day(text: str, now: datetime.datetime) -> Optional[datetime.date]:
    """Parses a date, or one of the words systemd accepts for one.

    Args:
        text: The candidate date, lower case.
        now: The current local time.

    Returns:
        The date, or None when the text is not a date.

    Raises:
        SnoozeError: If it looks like a date but is not a real one.
    """
    words = {'today': 0, 'tomorrow': 1, 'yesterday': -1}
    if text in words:
        return now.date() + datetime.timedelta(days=words[text])
    match = _DATE.match(text)
    if match is None:
        return None
    try:
        return datetime.date(*(int(part) for part in match.groups()))
    except ValueError as err:
        raise SnoozeError(f'"{text}" is not a date') from err


def _timestamp(text: str, now: datetime.datetime) -> datetime.datetime:
    """Parses a systemd timestamp such as ``Fri 2026-10-02 07:00 UTC``.

    Args:
        text: The timestamp, already stripped.
        now: The current local time.

    Returns:
        The moment, as a naive local time.

    Raises:
        SnoozeError: If the text is not a timestamp.
    """
    tokens = text.split()
    if len(tokens) == 1 and 'T' in tokens[0]:
        # ISO 8601 style, as newer systemd accepts: 2026-10-02T07:00.
        head, tail = tokens[0].split('T', 1)
        if _DATE.match(head):
            tokens = [head, tail]
    utc = bool(tokens) and tokens[-1].upper() in ('UTC', 'Z')
    if utc:
        tokens.pop()
    weekday = None
    if tokens and tokens[0].lower().rstrip(',') in _WEEKDAYS:
        weekday = _WEEKDAYS[tokens.pop(0).lower().rstrip(',')]
    if not tokens or len(tokens) > 2:
        raise SnoozeError(f'"{text}" is not a time span or a timestamp')
    reference = (now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                 if utc else now)
    day = _day(tokens[0].lower(), reference)
    clock = _clock(tokens[-1])
    if len(tokens) == 2 and (day is None or clock is None):
        raise SnoozeError(f'"{text}" is not a time span or a timestamp')
    if day is None and clock is None:
        raise SnoozeError(f'"{text}" is not a time span or a timestamp')
    moment = datetime.datetime.combine(
        day or reference.date(), clock or datetime.time())
    if weekday is not None and moment.weekday() != weekday:
        raise SnoozeError(
            f'{moment:%Y-%m-%d} is a {moment:%A}, not the day named')
    if utc:
        moment = (moment.replace(tzinfo=datetime.timezone.utc)
                  .astimezone().replace(tzinfo=None))
    return moment


def resolve(text: str,
            now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Works out when a snooze written in systemd.time syntax ends.

    Args:
        text: A time span or a timestamp; see the module docstring.
        now: The current local time; defaults to the clock.

    Returns:
        The end of the snooze, as a naive local time.

    Raises:
        SnoozeError: If the text cannot be understood, names a moment
            that has already passed, or is further off than
            :data:`MAX_SNOOZE`.
    """
    now = now or datetime.datetime.now()
    source = ' '.join(str(text or '').split())
    lowered = source.lower()
    if not source:
        raise SnoozeError('say how long to snooze for')
    try:
        if lowered == 'now':
            until = now
        elif source.startswith('@'):
            until = datetime.datetime.fromtimestamp(float(source[1:]))
        elif source.startswith('+'):
            until = now + parse_span(source[1:])
        elif source.startswith('-') or lowered.endswith(' ago'):
            until = now  # In the past by construction.
        elif lowered.endswith(' left'):
            until = now + parse_span(source[:-len(' left')])
        else:
            try:
                until = now + parse_span(source)
            except SnoozeError:
                until = _timestamp(source, now)
    except SnoozeError:
        raise
    except (OverflowError, OSError, ValueError) as err:
        raise SnoozeError(f'"{source}" is out of range') from err
    if until <= now:
        hint = (' Say "tomorrow" and the time, for example "tomorrow 07:00".'
                if _clock(source.split()[-1]) else '')
        raise SnoozeError(f'"{source}" is not in the future.{hint}')
    if until - now > MAX_SNOOZE:
        raise SnoozeError(
            f'"{source}" is more than {MAX_SNOOZE.days} days away; '
            'disable the schedule instead')
    return until


def active_until(value: Any,
                 now: Optional[datetime.datetime] = None
                 ) -> Optional[datetime.datetime]:
    """Reads a stored snooze and says whether it is still in force.

    Args:
        value: The ``snoozed_until`` field of a per-device document.
        now: The current local time; defaults to the clock.

    Returns:
        When the snooze ends, or None when there is none in force.
    """
    stored = stored_until(value)
    now = now or datetime.datetime.now()
    if stored is None or stored <= now:
        return None
    return stored


def stored_until(value: Any) -> Optional[datetime.datetime]:
    """Decodes a stored snooze, whether or not it has run out.

    A value that cannot be read is treated as no snooze at all: a hand
    edited file must not be able to stop a schedule for good.

    Args:
        value: The ``snoozed_until`` field of a per-device document.

    Returns:
        The stored instant, or None.
    """
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value)).replace(
            tzinfo=None)
    except ValueError:
        return None


def first_minute(until: datetime.datetime) -> datetime.datetime:
    """Returns the first whole minute at which rules run again.

    Args:
        until: The end of a snooze.

    Returns:
        ``until`` itself when it falls on a minute, otherwise the next
        minute after it.
    """
    floor = until.replace(second=0, microsecond=0)
    if floor == until:
        return floor
    return floor + datetime.timedelta(minutes=1)


def reference(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Describes the syntax for the interface.

    Args:
        now: The current local time; defaults to the clock.

    Returns:
        Worked examples and the longest snooze allowed.
    """
    now = now or datetime.datetime.now()
    week = (now + datetime.timedelta(days=7)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    dated = (
        (f'{week:%Y-%m-%d %H:%M}', 'a date and time'),
        (f'{week:%a %Y-%m-%d %H:%M}',
         'with a weekday, which must match the date'),
    )
    return {
        'examples': [{'text': text, 'means': means}
                     for text, means in SPAN_EXAMPLES + dated],
        'max_days': MAX_SNOOZE.days,
    }
