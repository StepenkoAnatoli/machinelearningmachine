"""
Configuration read from the environment, in one place.

Two prefixes are honoured for every setting: the long project name
(``MACHINELEARNINGMACHINE_*``) is canonical, and the short project alias
(``MODULE_MESH_*``) keeps working because it is what the older docs, launch
scripts and ``sessions.py`` already used. The long name wins when both are set,
so a migration is additive rather than a break.

This module exists so the core package (``netguard``, ``sessions``) does not
have to import from ``machinelearningmachine.server`` just to read a variable.
"""

from __future__ import annotations

import math
import os
from typing import Iterable, List, Optional, Tuple

ENV_PREFIX = "MACHINELEARNINGMACHINE_"
LEGACY_ENV_PREFIX = "MODULE_MESH_"

#: Values that turn a flag on. Anything else (including "0", "false", "no") is off,
#: so a stray ``FEATURE=0`` never enables a feature by accident.
TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSY = frozenset({"0", "false", "no", "off", ""})


def names_for(suffix: str) -> Tuple[str, ...]:
    """The environment variables that configure ``suffix``, canonical first."""
    return (ENV_PREFIX + suffix, LEGACY_ENV_PREFIX + suffix)


def get(suffix: str, default: str = "") -> str:
    """First non-empty value among :func:`names_for`, else ``default``."""
    for name in names_for(suffix):
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def flag(suffix: str, default: bool = False) -> bool:
    """A boolean setting. Unset means ``default``; junk means ``default`` too."""
    raw = None
    for name in names_for(suffix):
        value = os.environ.get(name)
        if value is not None and value.strip():
            raw = value.strip().lower()
            break
    if raw is None:
        return default
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return default


class EnvValueError(ValueError):
    """A numeric setting was given something that is not a usable number."""


def checked_number(
    value: object,
    *,
    label: str,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """
    Validate one numeric setting from any source (a flag, an environment variable).

    Unlike a boolean flag, an unparseable number is refused rather than quietly
    replaced: ``SESSION_TTL=5m`` silently falling back to 6 hours is a server that
    is not configured the way its operator believes it is.
    """
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise EnvValueError(f"{label} must be a number, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise EnvValueError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None and parsed < minimum:
        raise EnvValueError(f"{label} must be at least {minimum:g}")
    if maximum is not None and parsed > maximum:
        raise EnvValueError(f"{label} must be at most {maximum:g}")
    return parsed


def number(
    suffix: str,
    *,
    default: float,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """A numeric setting from :func:`names_for`, or ``default`` when unset."""
    raw = get(suffix)
    if not raw:
        return float(default)
    return checked_number(raw, label=describe(suffix), minimum=minimum, maximum=maximum)


def resolve_number(
    flag_value: Optional[object],
    suffix: str,
    *,
    label: str,
    default: float,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """The command-line flag wins, then the environment, then the default."""
    if flag_value is not None:
        return checked_number(flag_value, label=label, minimum=minimum, maximum=maximum)
    return number(suffix, default=default, minimum=minimum, maximum=maximum)


def string_list(suffix: str) -> List[str]:
    """A comma- or whitespace-separated list setting, de-duplicated in order."""
    raw = get(suffix)
    if not raw:
        return []
    parts: Iterable[str] = raw.replace(",", " ").split()
    seen: List[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def describe(suffix: str) -> str:
    """Human-readable hint naming both spellings (used in error messages)."""
    canonical, legacy = names_for(suffix)
    return f"{canonical} (or {legacy})"
