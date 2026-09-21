# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The per-device schedule script language.

Each device owns a small plain text program.  One rule per line, blank
lines and ``#`` comments ignored:

    # kitchen lamp
    0 6 * * mon-fri    on
    30 22 * * *        off
    */15 9-17 * * *    brightness 40
    @daily             refresh

A rule is a cron expression (see :mod:`kasapanel.cron`) followed by an
action keyword and its arguments (see :mod:`kasapanel.actions`).  The
scheduler evaluates every rule once a minute, so two rules that fire in
the same minute both run, in the order they appear in the file.
"""

import dataclasses
import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kasapanel import actions
from kasapanel import cron

COMMENT_CHAR = '#'

EXAMPLE_SCRIPT = '\n'.join((
    '# One rule per line: <schedule> <action> [arguments]',
    '# Times use the daemon host clock, to the minute.',
    '',
    '# 0 6 * * mon-fri   on',
    '# 30 22 * * *       off',
    '# */30 * * * *      refresh',
    '',
))


class ScheduleError(ValueError):
    """Raised when a schedule script contains at least one bad rule."""


@dataclasses.dataclass
class ScheduleEntry:
    """One parsed rule of a schedule script.

    Attributes:
        line_number: One based line number in the source script.
        text: The rule as written, without comments.
        expression: The parsed cron expression.
        action: The resolved action name.
        arguments: Raw string arguments for the action.
        last_fired: Minute this rule last ran, used to avoid repeats.
    """

    line_number: int
    text: str
    expression: cron.CronExpression
    action: str
    arguments: Tuple[str, ...]
    last_fired: Optional[datetime.datetime] = None

    @property
    def command(self) -> str:
        """Returns the action and its arguments as one string."""
        return ' '.join((self.action,) + self.arguments).strip()

    def to_dict(self) -> Dict[str, Any]:
        """Returns a JSON friendly view of the rule."""
        return {
            'line_number': self.line_number,
            'text': self.text,
            'schedule': self.expression.source,
            'describes': self.expression.describe(),
            'action': self.action,
            'arguments': list(self.arguments),
            'command': self.command,
        }


@dataclasses.dataclass
class ScheduleProblem:
    """A rule that could not be parsed.

    Attributes:
        line_number: One based line number in the source script.
        text: The offending line.
        message: What is wrong with it.
    """

    line_number: int
    text: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        """Returns a JSON friendly view of the problem."""
        return {
            'line_number': self.line_number,
            'text': self.text,
            'message': self.message,
        }


def _strip_comment(line: str) -> str:
    """Removes a trailing comment from a line.

    Args:
        line: One raw line of a schedule script.

    Returns:
        The line without its comment and without surrounding space.
    """
    head, _, _ = line.partition(COMMENT_CHAR)
    return head.strip()


def _parse_line(line_number: int, text: str) -> ScheduleEntry:
    """Parses one rule.

    Args:
        line_number: One based line number, used in error messages.
        text: The rule text, already stripped of comments.

    Returns:
        The parsed rule.

    Raises:
        ScheduleError: If the schedule or the action is invalid.
    """
    tokens = text.split()
    if tokens[0].startswith('@'):
        schedule_text = tokens[0]
        remainder = tokens[1:]
    else:
        if len(tokens) < 6:
            raise ScheduleError(
                'a rule needs 5 schedule fields followed by an action, '
                'for example "30 22 * * * off"')
        schedule_text = ' '.join(tokens[:5])
        remainder = tokens[5:]
    if not remainder:
        raise ScheduleError('the schedule is not followed by an action')
    try:
        expression = cron.CronExpression.parse(schedule_text)
    except cron.CronError as err:
        raise ScheduleError(str(err)) from err
    try:
        spec, _ = actions.parse(remainder[0], remainder[1:])
    except actions.ActionError as err:
        raise ScheduleError(str(err)) from err
    return ScheduleEntry(
        line_number=line_number,
        text=text,
        expression=expression,
        action=spec.name,
        arguments=tuple(remainder[1:]))


def parse_script(script: str) -> Tuple[List[ScheduleEntry],
                                       List[ScheduleProblem]]:
    """Parses a whole schedule script.

    Parsing never raises: valid rules and problems are returned side by
    side so the editor can show every mistake at once.

    Args:
        script: The script text.

    Returns:
        A tuple of the parsed rules and the problems found.
    """
    entries: List[ScheduleEntry] = []
    problems: List[ScheduleProblem] = []
    for index, raw_line in enumerate((script or '').splitlines(), start=1):
        text = _strip_comment(raw_line)
        if not text:
            continue
        try:
            entries.append(_parse_line(index, text))
        except ScheduleError as err:
            problems.append(ScheduleProblem(index, text, str(err)))
    return entries, problems


def parse_strict(script: str) -> List[ScheduleEntry]:
    """Parses a schedule script and rejects it if anything is wrong.

    Args:
        script: The script text.

    Returns:
        The parsed rules.

    Raises:
        ScheduleError: If any rule is invalid.
    """
    entries, problems = parse_script(script)
    if problems:
        first = problems[0]
        raise ScheduleError(f'line {first.line_number}: {first.message}')
    return entries


def next_runs(
    entries: Sequence[ScheduleEntry],
    count: int = 5,
    now: Optional[datetime.datetime] = None,
) -> List[Dict[str, Any]]:
    """Builds a preview of the next few firings of a script.

    Args:
        entries: Parsed rules, typically from :func:`parse_script`.
        count: How many firings to return.
        now: Local time to search from; defaults to the current time.

    Returns:
        A list of dictionaries with the firing time, the rule line and
        the command, sorted by time.
    """
    moment = now or datetime.datetime.now()
    upcoming: List[Tuple[datetime.datetime, ScheduleEntry]] = []
    for entry in entries:
        cursor = moment
        for _ in range(count):
            fires = entry.expression.next_run(cursor)
            if fires is None:
                break
            upcoming.append((fires, entry))
            cursor = fires
    upcoming.sort(key=lambda item: (item[0], item[1].line_number))
    preview = []
    for fires, entry in upcoming[:count]:
        preview.append({
            'when': fires.isoformat(timespec='minutes'),
            'line_number': entry.line_number,
            'command': entry.command,
        })
    return preview


def summarise(script: str,
              now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Validates a script and describes what it will do.

    Args:
        script: The script text.
        now: Local time used for the run preview.

    Returns:
        A JSON friendly summary with rules, problems and a preview of
        the next firings.
    """
    entries, problems = parse_script(script)
    return {
        'valid': not problems,
        'rule_count': len(entries),
        'rules': [entry.to_dict() for entry in entries],
        'problems': [problem.to_dict() for problem in problems],
        'next_runs': next_runs(entries, now=now),
    }
