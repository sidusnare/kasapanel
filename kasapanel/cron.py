# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""A cron expression parser with minute resolution.

The syntax follows Vixie cron closely:

    minute  hour  day-of-month  month  day-of-week

Every field accepts ``*``, single values, comma separated lists,
``first-last`` ranges and ``*/step`` or ``first-last/step`` increments.
Month and day-of-week fields also accept three letter English names.
Sunday is both ``0`` and ``7``.

The following keywords replace the five fields entirely:

    @yearly, @annually, @monthly, @weekly, @daily, @midnight, @hourly,

Seconds are deliberately unsupported: the scheduler ticks once per
minute, which is the finest resolution this language expresses.
"""

import dataclasses
import datetime
from typing import Dict, FrozenSet, Optional, Tuple

MACROS: Dict[str, str] = {
    '@yearly': '0 0 1 1 *',
    '@annually': '0 0 1 1 *',
    '@monthly': '0 0 1 * *',
    '@weekly': '0 0 * * 0',
    '@daily': '0 0 * * *',
    '@midnight': '0 0 * * *',
    '@hourly': '0 * * * *',
}


MONTH_NAMES: Dict[str, int] = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

WEEKDAY_NAMES: Dict[str, int] = {
    'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6,
}

_FIELD_NAMES = ('minute', 'hour', 'day of month', 'month', 'day of week')
_FIELD_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

# How far ahead next_run() is willing to search before giving up.
_SEARCH_HORIZON_DAYS = 400


class CronError(ValueError):
    """Raised when a cron expression cannot be parsed."""


def _parse_value(token: str, index: int) -> int:
    """Converts one field token into an integer.

    Args:
        token: A number or a three letter month or weekday name.
        index: Position of the field, used to pick the name table.

    Returns:
        The numeric value of the token.

    Raises:
        CronError: If the token is neither a number nor a known name.
    """
    lowered = token.strip().lower()
    if index == 3 and lowered[:3] in MONTH_NAMES:
        return MONTH_NAMES[lowered[:3]]
    if index == 4 and lowered[:3] in WEEKDAY_NAMES:
        return WEEKDAY_NAMES[lowered[:3]]
    try:
        return int(lowered, 10)
    except ValueError as err:
        raise CronError(
            f'{_FIELD_NAMES[index]}: "{token}" is not a number or name'
        ) from err


def _parse_field(field: str, index: int) -> Tuple[FrozenSet[int], bool]:
    """Expands one cron field into the set of values it matches.

    Args:
        field: The raw field text, for example ``*/15`` or ``mon-fri``.
        index: Position of the field within the expression.

    Returns:
        A tuple of the matching values and a flag that is True when the
        field is restricted, meaning it is not a bare ``*``.

    Raises:
        CronError: If the field is malformed or out of range.
    """
    low, high = _FIELD_BOUNDS[index]
    field = field.strip()
    if not field:
        raise CronError(f'{_FIELD_NAMES[index]}: empty field')
    values = set()
    restricted = False
    for part in field.split(','):
        part = part.strip()
        if not part:
            raise CronError(f'{_FIELD_NAMES[index]}: empty list element')
        step = 1
        if '/' in part:
            part, _, step_text = part.partition('/')
            try:
                step = int(step_text, 10)
            except ValueError as err:
                raise CronError(
                    f'{_FIELD_NAMES[index]}: step "{step_text}" is not a '
                    'number') from err
            if step < 1:
                raise CronError(
                    f'{_FIELD_NAMES[index]}: step must be 1 or more')
        part = part.strip()
        if part in ('*', ''):
            start, stop = low, high
            if step != 1:
                restricted = True
        elif '-' in part[1:]:
            head, _, tail = part.partition('-')
            start = _parse_value(head, index)
            stop = _parse_value(tail, index)
            restricted = True
        else:
            start = stop = _parse_value(part, index)
            restricted = True
        for bound in (start, stop):
            if bound < low or bound > high:
                raise CronError(
                    f'{_FIELD_NAMES[index]}: {bound} is outside '
                    f'{low}-{high}')
        if stop < start:
            raise CronError(
                f'{_FIELD_NAMES[index]}: range {start}-{stop} runs backwards')
        values.update(range(start, stop + 1, step))
    if index == 4 and 7 in values:
        values.discard(7)
        values.add(0)
    return frozenset(values), restricted


@dataclasses.dataclass(frozen=True)
class CronExpression:
    """A parsed cron expression.

    Attributes:
        source: The expression exactly as written by the user.
        minutes: Matching minutes of the hour.
        hours: Matching hours of the day.
        days: Matching days of the month.
        months: Matching months of the year.
        weekdays: Matching weekdays, Sunday being zero.
        day_restricted: True when the day-of-month field is not ``*``.
        weekday_restricted: True when the day-of-week field is not ``*``.
    """

    source: str
    minutes: FrozenSet[int] = frozenset()
    hours: FrozenSet[int] = frozenset()
    days: FrozenSet[int] = frozenset()
    months: FrozenSet[int] = frozenset()
    weekdays: FrozenSet[int] = frozenset()
    day_restricted: bool = False
    weekday_restricted: bool = False

    @classmethod
    def parse(cls, text: str) -> 'CronExpression':
        """Parses a cron expression or keyword.

        Args:
            text: The expression, for example ``*/5 9-17 * * mon-fri``.

        Returns:
            The parsed expression.

        Raises:
            CronError: If the expression is malformed.
        """
        source = text.strip()
        if not source:
            raise CronError('empty schedule expression')
        lowered = source.lower()
        if lowered.startswith('@'):
            if lowered not in MACROS:
                known = ', '.join(sorted(MACROS))
                raise CronError(
                    f'unknown keyword "{source}"; known keywords: {known}')
            source_fields = MACROS[lowered]
        else:
            source_fields = source
        fields = source_fields.split()
        if len(fields) != 5:
            raise CronError(
                f'expected 5 fields, found {len(fields)}: '
                'minute hour day-of-month month day-of-week')
        parsed = [_parse_field(field, index)
                  for index, field in enumerate(fields)]
        return cls(
            source=source,
            minutes=parsed[0][0],
            hours=parsed[1][0],
            days=parsed[2][0],
            months=parsed[3][0],
            weekdays=parsed[4][0],
            day_restricted=parsed[2][1],
            weekday_restricted=parsed[4][1])

    def _day_matches(self, moment: datetime.datetime) -> bool:
        """Applies the day-of-month and day-of-week matching rule.

        Args:
            moment: The instant under test.

        Returns:
            True when the date part of ``moment`` is selected.
        """
        if moment.month not in self.months:
            return False
        day_hit = moment.day in self.days
        weekday_hit = (moment.weekday() + 1) % 7 in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_hit or weekday_hit
        return day_hit and weekday_hit

    def matches(self, moment: datetime.datetime) -> bool:
        """Reports whether the expression fires at a given minute.

        Args:
            moment: Local time to test; seconds are ignored.

        Returns:
            True when the expression fires during that minute.
        """
        return (moment.minute in self.minutes
                and moment.hour in self.hours
                and self._day_matches(moment))

    def next_run(
        self,
        after: datetime.datetime,
        horizon_days: int = _SEARCH_HORIZON_DAYS,
    ) -> Optional[datetime.datetime]:
        """Finds the next firing time strictly after a given instant.

        Args:
            after: Local time to search from.
            horizon_days: How many days ahead to search before giving up.

        Returns:
            The next matching minute, or None for expressions such as
            ``0 0 30 2 *`` that never fire.
        """
        moment = after.replace(second=0, microsecond=0)
        moment += datetime.timedelta(minutes=1)
        limit = after + datetime.timedelta(days=horizon_days)
        while moment <= limit:
            if not self._day_matches(moment):
                moment = moment.replace(hour=0, minute=0)
                moment += datetime.timedelta(days=1)
                continue
            if moment.hour not in self.hours:
                moment = moment.replace(minute=0)
                moment += datetime.timedelta(hours=1)
                continue
            if moment.minute not in self.minutes:
                moment += datetime.timedelta(minutes=1)
                continue
            return moment
        return None

    def describe(self) -> str:
        """Returns a short human readable summary of the expression."""
        lowered = self.source.lower()
        if lowered in MACROS:
            return f'{lowered} ({MACROS[lowered]})'
        return self.source
