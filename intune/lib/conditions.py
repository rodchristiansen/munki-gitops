"""Translate a Munki condition into an Intune assignment-filter rule.

ONE copy, imported by both the lint stage and the assign stages. In the
production pipeline this logic exists twice with a comment between the copies
saying "keep these in sync", which is a module wearing a disguise. This is the
module.

Munki evaluates conditions on the device against facts it collects locally, so
a Munki condition can reference almost anything. An assignment filter can only
reference the handful of properties the MDM holds. Everything hard about this
file comes from that gap.

Supported:
    hostname       == != CONTAINS BEGINSWITH ENDSWITH
    machine_type   == !=            ("laptop" / "desktop")
    os_vers_major  == != >= <= > <
    AND, OR, NOT, parentheses

Not supported, and deliberately loud about it:
    arch, serial_number, catalogs, machine_model, any custom fact
"""
from __future__ import annotations

import re

# macOS assignment filters expose only these device properties.
FILTER_PROPERTIES = {
    "deviceName", "manufacturer", "model", "osVersion",
    "deviceCategory", "enrollmentProfileName",
}

# Munki facts with no filter equivalent at all. Listed so the error message can
# say WHY rather than just "unsupported".
UNTRANSLATABLE = {
    "arch": "no architecture property exists on an assignment filter",
    "serial_number": "serial number is not a filterable property",
    "catalogs": "a Munki concept the MDM has never heard of",
    "munki_version": "a Munki concept the MDM has never heard of",
    "enrolled_area": "a local fact, not a directory property",
    # machine_model looks supported and is not: the filter's `model` property
    # holds the marketing name ("MacBook Pro"), never the hardware identifier
    # ("MacBookPro16,1"). A condition on the identifier translates cleanly,
    # produces a valid filter, and matches nothing.
    "machine_model": "filter `model` is the marketing name, not the hardware identifier",
}

MACHINE_TYPES = {"laptop": "MacBook", "desktop": "iMac"}


class CondParseError(ValueError):
    """The condition is malformed."""


class UnsupportedCondition(ValueError):
    """The condition is well-formed but has no filter equivalent."""


_TOKEN = re.compile(r"""
    \s*(?:
      (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<op>==|!=|>=|<=|>|<)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<number>\d+)
    | (?P<string>"[^"]*"|'[^']*')
    )
""", re.VERBOSE)

_LOGICAL = {"AND", "OR", "NOT"}
_TEXT_OPS = {"CONTAINS", "BEGINSWITH", "ENDSWITH"}


def _tokenize(text: str) -> list[str]:
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos].isspace():
                pos += 1
                continue
            raise CondParseError(f"unexpected character {text[pos]!r} at {pos}")
        pos = m.end()
        tokens.append(m.group(m.lastgroup))
    return tokens


def _rule_for(subject: str, operator: str, value: str) -> str:
    """Emit one filter predicate."""
    bare = value.strip("\"'")

    if subject in UNTRANSLATABLE:
        raise UnsupportedCondition(f"{subject}: {UNTRANSLATABLE[subject]}")

    if subject == "hostname":
        prop = "deviceName"
        if operator == "==":
            return f'(device.{prop} -eq "{bare}")'
        if operator == "!=":
            return f'(device.{prop} -ne "{bare}")'
        if operator == "CONTAINS":
            return f'(device.{prop} -contains "{bare}")'
        if operator == "BEGINSWITH":
            return f'(device.{prop} -startsWith "{bare}")'
        if operator == "ENDSWITH":
            return f'(device.{prop} -endsWith "{bare}")'
        raise UnsupportedCondition(f"hostname {operator} is not expressible")

    if subject == "machine_type":
        marketing = MACHINE_TYPES.get(bare.lower())
        if not marketing:
            raise UnsupportedCondition(f"machine_type {bare!r} is not laptop or desktop")
        # -notStartsWith is rejected by the platform on device.model, so a
        # negated machine_type cannot be expressed as a single predicate.
        if operator == "==":
            return f'(device.model -startsWith "{marketing}")'
        raise UnsupportedCondition(
            "machine_type != translates to -notStartsWith, which the platform "
            "rejects on device.model"
        )

    if subject == "os_vers_major":
        ops = {"==": "-eq", "!=": "-ne", ">=": "-ge", "<=": "-le", ">": "-gt", "<": "-lt"}
        if operator not in ops:
            raise CondParseError(f"bad operator {operator!r} for os_vers_major")
        return f'(device.osVersion {ops[operator]} "{bare}")'

    raise UnsupportedCondition(f"{subject}: no assignment-filter equivalent")


def translate(condition: str) -> tuple[str, str]:
    """Return (filter display name, filter rule) for a Munki condition.

    Raises CondParseError if malformed, UnsupportedCondition if it cannot be
    expressed. Callers must not treat those the same: one is a bug in the
    manifest, the other is a limit of the platform.
    """
    text = (condition or "").strip()
    if not text:
        raise CondParseError("empty condition")

    tokens = _tokenize(text)
    if not tokens:
        raise CondParseError(f"nothing to parse in {condition!r}")
    if tokens.count("(") != tokens.count(")"):
        raise CondParseError(f"unbalanced parentheses in {condition!r}")

    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        upper = tok.upper()
        if tok in "()":
            out.append(tok)
            i += 1
        elif upper in _LOGICAL:
            out.append({"AND": "and", "OR": "or", "NOT": "not"}[upper])
            i += 1
        else:
            # A comparison is exactly three tokens: subject, operator, value.
            if i + 2 >= len(tokens):
                raise CondParseError(f"incomplete comparison in {condition!r}")
            subject, operator, value = tokens[i], tokens[i + 1], tokens[i + 2]
            if operator.upper() in _TEXT_OPS:
                operator = operator.upper()
            out.append(_rule_for(subject, operator, value))
            i += 3

    rule = " ".join(out)
    display = "Munki: " + re.sub(r"\s+", " ", text)
    return display, rule


def is_supported(condition: str) -> bool:
    try:
        translate(condition)
        return True
    except (CondParseError, UnsupportedCondition):
        return False
