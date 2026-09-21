# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The device action vocabulary.

An action is the verb half of a schedule line and is also what the
dashboard buttons send.  Actions are declared once here so that the
schedule parser, the JSON API and the web interface all agree on the
same names, argument counts and value ranges.

python-kasa moved device features into modules in release 0.7, so every
runner first looks for the module and falls back to the older direct
methods.  That keeps the daemon working across both API generations.
"""

import dataclasses
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_LOG = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the installed python-kasa version.
    from kasa import Module as _KasaModule
except ImportError:  # pragma: no cover
    _KasaModule = None


class ActionError(ValueError):
    """Raised when an action name or its arguments are invalid."""


def device_module(device: Any, name: str) -> Optional[Any]:
    """Returns a python-kasa module of a device, if it exposes one.

    Args:
        device: A python-kasa device object.
        name: Attribute name on ``kasa.Module``, such as ``Light``.

    Returns:
        The module instance, or None when it is not available.
    """
    if _KasaModule is None:
        return None
    key = getattr(_KasaModule, name, None)
    modules = getattr(device, 'modules', None)
    if key is None or modules is None:
        return None
    try:
        return modules.get(key)
    except (KeyError, TypeError):  # pragma: no cover - defensive.
        return None


def _int_arg(values: Sequence[str], index: int, name: str,
             low: int, high: int) -> int:
    """Parses and range checks one integer argument.

    Args:
        values: The raw argument list.
        index: Which argument to read.
        name: Argument name, used in error messages.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        The parsed integer.

    Raises:
        ActionError: If the argument is missing, not a number, or out of
            range.
    """
    try:
        number = int(values[index], 10)
    except (IndexError, ValueError) as err:
        raise ActionError(f'{name} must be a whole number') from err
    if number < low or number > high:
        raise ActionError(f'{name} must be between {low} and {high}')
    return number


def _parse_none(values: Sequence[str]) -> Tuple[Any, ...]:
    """Validates that an action takes no arguments.

    Args:
        values: The raw argument list.

    Returns:
        An empty tuple.

    Raises:
        ActionError: If any argument was supplied.
    """
    if values:
        raise ActionError('this action takes no arguments')
    return ()


def _parse_brightness(values: Sequence[str]) -> Tuple[Any, ...]:
    """Parses the argument of the ``brightness`` action.

    Args:
        values: The raw argument list.

    Returns:
        A one element tuple holding the brightness percentage.

    Raises:
        ActionError: If the percentage is missing or out of range.
    """
    return (_int_arg(values, 0, 'brightness', 1, 100),)


def _parse_temperature(values: Sequence[str]) -> Tuple[Any, ...]:
    """Parses the argument of the ``temperature`` action.

    Args:
        values: The raw argument list.

    Returns:
        A one element tuple holding the colour temperature in kelvin.

    Raises:
        ActionError: If the temperature is missing or out of range.
    """
    return (_int_arg(values, 0, 'colour temperature', 1000, 12000),)


def _parse_colour(values: Sequence[str]) -> Tuple[Any, ...]:
    """Parses the three arguments of the ``colour`` action.

    Args:
        values: The raw argument list.

    Returns:
        A tuple of hue, saturation and value.

    Raises:
        ActionError: If an argument is missing or out of range.
    """
    if len(values) != 3:
        raise ActionError('colour takes hue, saturation and value')
    return (
        _int_arg(values, 0, 'hue', 0, 360),
        _int_arg(values, 1, 'saturation', 0, 100),
        _int_arg(values, 2, 'value', 1, 100),
    )


def _parse_switch(values: Sequence[str]) -> Tuple[Any, ...]:
    """Parses an ``on`` or ``off`` argument.

    Args:
        values: The raw argument list.

    Returns:
        A one element tuple holding a boolean.

    Raises:
        ActionError: If the argument is missing or not on/off.
    """
    if len(values) != 1 or values[0].lower() not in ('on', 'off'):
        raise ActionError('expected "on" or "off"')
    return (values[0].lower() == 'on',)


async def _run_on(device: Any, values: Tuple[Any, ...]) -> str:
    """Turns the device on.

    Args:
        device: A python-kasa device object.
        values: Unused.

    Returns:
        A short report of what happened.
    """
    del values
    await device.turn_on()
    return 'turned on'


async def _run_off(device: Any, values: Tuple[Any, ...]) -> str:
    """Turns the device off.

    Args:
        device: A python-kasa device object.
        values: Unused.

    Returns:
        A short report of what happened.
    """
    del values
    await device.turn_off()
    return 'turned off'


async def _run_toggle(device: Any, values: Tuple[Any, ...]) -> str:
    """Inverts the current on/off state of the device.

    Args:
        device: A python-kasa device object.
        values: Unused.

    Returns:
        A short report of what happened.
    """
    del values
    if device.is_on:
        await device.turn_off()
        return 'toggled off'
    await device.turn_on()
    return 'toggled on'


async def _run_brightness(device: Any, values: Tuple[Any, ...]) -> str:
    """Sets the brightness of a dimmable device.

    Args:
        device: A python-kasa device object.
        values: A tuple holding the brightness percentage.

    Returns:
        A short report of what happened.

    Raises:
        ActionError: If the device cannot be dimmed.
    """
    light = device_module(device, 'Light')
    if light is not None and hasattr(light, 'set_brightness'):
        await light.set_brightness(values[0])
    elif hasattr(device, 'set_brightness'):
        await device.set_brightness(values[0])
    else:
        raise ActionError('device does not support brightness')
    return f'brightness set to {values[0]}%'


async def _run_temperature(device: Any, values: Tuple[Any, ...]) -> str:
    """Sets the colour temperature of a tunable white device.

    Args:
        device: A python-kasa device object.
        values: A tuple holding the temperature in kelvin.

    Returns:
        A short report of what happened.

    Raises:
        ActionError: If the device has no tunable white channel.
    """
    light = device_module(device, 'Light')
    if light is not None and hasattr(light, 'set_color_temp'):
        await light.set_color_temp(values[0])
    elif hasattr(device, 'set_color_temp'):
        await device.set_color_temp(values[0])
    else:
        raise ActionError('device does not support colour temperature')
    return f'colour temperature set to {values[0]}K'


async def _run_colour(device: Any, values: Tuple[Any, ...]) -> str:
    """Sets hue, saturation and value on a colour device.

    Args:
        device: A python-kasa device object.
        values: A tuple of hue, saturation and value.

    Returns:
        A short report of what happened.

    Raises:
        ActionError: If the device has no colour channel.
    """
    light = device_module(device, 'Light')
    if light is not None and hasattr(light, 'set_hsv'):
        await light.set_hsv(values[0], values[1], values[2])
    elif hasattr(device, 'set_hsv'):
        await device.set_hsv(values[0], values[1], values[2])
    else:
        raise ActionError('device does not support colour')
    return f'colour set to {values[0]}/{values[1]}/{values[2]}'


async def _run_led(device: Any, values: Tuple[Any, ...]) -> str:
    """Turns the status LED of the device on or off.

    Args:
        device: A python-kasa device object.
        values: A tuple holding the requested LED state.

    Returns:
        A short report of what happened.

    Raises:
        ActionError: If the device has no controllable LED.
    """
    led = device_module(device, 'Led')
    if led is not None and hasattr(led, 'set_led'):
        await led.set_led(values[0])
    elif hasattr(device, 'set_led'):
        await device.set_led(values[0])
    else:
        raise ActionError('device does not have a controllable LED')
    return 'status LED ' + ('on' if values[0] else 'off')


async def _run_refresh(device: Any, values: Tuple[Any, ...]) -> str:
    """Polls the device without changing anything.

    Args:
        device: A python-kasa device object.
        values: Unused.

    Returns:
        A short report of what happened.
    """
    del values
    await device.update()
    return 'state refreshed'


@dataclasses.dataclass(frozen=True)
class ActionSpec:
    """One entry of the action vocabulary.

    Attributes:
        name: The keyword used in schedules and in the API.
        usage: A one line usage string shown in the help panel.
        summary: What the action does, in plain words.
        parse: Callable that validates and converts raw arguments.
        run: Coroutine function that performs the action.
    """

    name: str
    usage: str
    summary: str
    parse: Callable[[Sequence[str]], Tuple[Any, ...]]
    run: Callable[[Any, Tuple[Any, ...]], Any]


ACTIONS: Dict[str, ActionSpec] = {
    spec.name: spec for spec in (
        ActionSpec('on', 'on', 'Switch the device on.',
                   _parse_none, _run_on),
        ActionSpec('off', 'off', 'Switch the device off.',
                   _parse_none, _run_off),
        ActionSpec('toggle', 'toggle', 'Invert the current state.',
                   _parse_none, _run_toggle),
        ActionSpec('brightness', 'brightness <1-100>',
                   'Set brightness, in percent.',
                   _parse_brightness, _run_brightness),
        ActionSpec('temperature', 'temperature <2500-6500>',
                   'Set white colour temperature, in kelvin.',
                   _parse_temperature, _run_temperature),
        ActionSpec('colour', 'colour <hue> <saturation> <value>',
                   'Set hue 0-360, saturation 0-100, value 1-100.',
                   _parse_colour, _run_colour),
        ActionSpec('led', 'led <on|off>',
                   'Switch the small status LED on the device.',
                   _parse_switch, _run_led),
        ActionSpec('refresh', 'refresh',
                   'Poll the device and record its state.',
                   _parse_none, _run_refresh),
    )
}

ALIASES: Dict[str, str] = {
    'color': 'colour',
    'color_temp': 'temperature',
    'colour_temp': 'temperature',
    'dim': 'brightness',
    'enable': 'on',
    'disable': 'off',
    'poll': 'refresh',
}


def resolve(name: str) -> ActionSpec:
    """Looks up an action by name or alias.

    Args:
        name: The action keyword, case insensitive.

    Returns:
        The matching action specification.

    Raises:
        ActionError: If no such action exists.
    """
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    spec = ACTIONS.get(key)
    if spec is None:
        known = ', '.join(sorted(ACTIONS))
        raise ActionError(f'unknown action "{name}"; known actions: {known}')
    return spec


def parse(name: str, values: Sequence[str]) -> Tuple[ActionSpec,
                                                     Tuple[Any, ...]]:
    """Resolves an action and validates its arguments.

    Args:
        name: The action keyword.
        values: Raw string arguments.

    Returns:
        A tuple of the action specification and its parsed arguments.

    Raises:
        ActionError: If the action or its arguments are invalid.
    """
    spec = resolve(name)
    try:
        return spec, spec.parse(values)
    except ActionError as err:
        raise ActionError(f'{spec.usage}: {err}') from err


async def execute(device: Any, name: str, values: Sequence[str]) -> str:
    """Runs an action against a connected device.

    Args:
        device: A python-kasa device object that is already connected.
        name: The action keyword.
        values: Raw string arguments.

    Returns:
        A short report suitable for the activity log.

    Raises:
        ActionError: If the action or its arguments are invalid.
    """
    spec, parsed = parse(name, values)
    _LOG.debug('running action %s%s on %s', spec.name, parsed, device)
    result = await spec.run(device, parsed)
    await device.update()
    return result


def catalogue() -> List[Dict[str, str]]:
    """Returns the action vocabulary for display in the help panel.

    Returns:
        A list of dictionaries with name, usage and summary keys.
    """
    return [
        {'name': spec.name, 'usage': spec.usage, 'summary': spec.summary}
        for spec in ACTIONS.values()
    ]
