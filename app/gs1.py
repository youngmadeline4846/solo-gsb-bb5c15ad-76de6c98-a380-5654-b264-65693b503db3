"""GS1 element-string parser.

Supports AI 01 (GTIN-14), AI 10 (batch/lot), AI 17 (expiry date) and
AI 21 (serial number). Fixed-length fields (01, 17) are delimited
implicitly by their length; variable-length fields (10, 21) must be
terminated by the FNC1 separator (ASCII 29, GS) unless they are the
last field in the string.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass

GS = "\x1d"  # ASCII 29 <GS>, transmitted as FNC1 by scanners

DIGITS = frozenset("0123456789")

# GS1 AI encodable character set 82 (GS1 General Specifications, fig. 7.11-1)
AI82 = frozenset(
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "!\"%&'()*+,-./:;<=>?_"
)


@dataclass(frozen=True)
class AISpec:
    ai: str
    name: str
    min_length: int
    max_length: int
    fixed_length: bool
    charset: frozenset
    charset_rule: str


AI_SPECS = {
    "01": AISpec("01", "GTIN", 14, 14, True, DIGITS, "digits 0-9"),
    "17": AISpec("17", "EXPIRY", 6, 6, True, DIGITS, "digits 0-9"),
    "10": AISpec("10", "BATCH/LOT", 1, 20, False, AI82, "GS1 AI character set 82"),
    "21": AISpec("21", "SERIAL", 1, 20, False, AI82, "GS1 AI character set 82"),
}

SUPPORTED_AIS = ", ".join(sorted(AI_SPECS))


class GS1ParseError(Exception):
    """Parse/validation failure carrying a byte offset and the expected rule."""

    def __init__(self, code, message, offset, expected, found=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.offset = offset
        self.expected = expected
        self.found = found

    def to_dict(self):
        detail = {
            "error": self.code,
            "message": self.message,
            "offset": self.offset,
            "expected": self.expected,
        }
        if self.found is not None:
            detail["found"] = self.found
        return detail


@dataclass(frozen=True)
class Element:
    ai: str
    name: str
    value: str
    offset: int  # byte offset of the AI within the input


def _byte_offset(data: str, index: int) -> int:
    """Byte offset (UTF-8) of the character at ``index``."""
    return len(data[:index].encode("utf-8"))


def parse(data: str) -> list[Element]:
    """Parse a raw GS1 scan string into an ordered list of elements."""
    if not data:
        raise GS1ParseError(
            "EMPTY_INPUT",
            "Input is empty",
            offset=0,
            expected=f"at least one element string starting with AI {SUPPORTED_AIS}",
        )

    elements = []
    pos = 0
    while pos < len(data):
        ai_offset = _byte_offset(data, pos)
        if data[pos] == GS:
            raise GS1ParseError(
                "UNEXPECTED_SEPARATOR",
                "Unexpected separator (ASCII 29); expected an application identifier",
                offset=ai_offset,
                expected=f"2-digit AI, one of: {SUPPORTED_AIS}",
                found="<GS>",
            )
        ai = data[pos : pos + 2]
        if len(ai) < 2 or not all(c in DIGITS for c in ai):
            raise GS1ParseError(
                "INVALID_AI",
                f"Expected a 2-digit application identifier, found {ai!r}",
                offset=ai_offset,
                expected=f"2-digit AI, one of: {SUPPORTED_AIS}",
                found=ai,
            )
        spec = AI_SPECS.get(ai)
        if spec is None:
            raise GS1ParseError(
                "UNSUPPORTED_AI",
                f"Unsupported application identifier '{ai}'",
                offset=ai_offset,
                expected=f"one of: {SUPPORTED_AIS}",
                found=ai,
            )
        pos += 2
        value_pos = pos

        if spec.fixed_length:
            value, pos = _read_fixed(data, pos, spec)
        else:
            value, pos = _read_variable(data, pos, spec)

        _check_charset(data, value, value_pos, spec)
        if spec.ai == "01":
            _check_gtin(data, value, value_pos)
        elif spec.ai == "17":
            _check_date(data, value, value_pos)

        elements.append(Element(ai=spec.ai, name=spec.name, value=value, offset=ai_offset))
    return elements


def _read_fixed(data, pos, spec):
    field = data[pos : pos + spec.max_length]
    sep = field.find(GS)
    if sep != -1:
        raise GS1ParseError(
            "FIELD_TOO_SHORT",
            f"AI {spec.ai} ({spec.name}) requires exactly {spec.max_length} characters "
            f"but a separator was found after {sep}",
            offset=_byte_offset(data, pos + sep),
            expected=f"{spec.max_length} characters for AI {spec.ai}",
            found="<GS>",
        )
    if len(field) < spec.max_length:
        raise GS1ParseError(
            "FIELD_TOO_SHORT",
            f"AI {spec.ai} ({spec.name}) requires exactly {spec.max_length} characters "
            f"but the input ended after {len(field)}",
            offset=_byte_offset(data, len(data)),
            expected=f"{spec.max_length} characters for AI {spec.ai}",
            found=f"{len(field)} character(s)",
        )
    return field, pos + spec.max_length


def _read_variable(data, pos, spec):
    end = data.find(GS, pos)
    terminated = end != -1
    if not terminated:
        end = len(data)
    field = data[pos:end]
    if len(field) < spec.min_length:
        raise GS1ParseError(
            "FIELD_EMPTY",
            f"AI {spec.ai} ({spec.name}) requires at least {spec.min_length} character",
            offset=_byte_offset(data, pos),
            expected=f"{spec.min_length}-{spec.max_length} characters for AI {spec.ai}",
            found="empty field",
        )
    if len(field) > spec.max_length:
        raise GS1ParseError(
            "FIELD_TOO_LONG",
            f"AI {spec.ai} ({spec.name}) allows at most {spec.max_length} characters "
            f"but got {len(field)} before {'the separator' if terminated else 'end of input'}",
            offset=_byte_offset(data, pos + spec.max_length),
            expected=f"separator (ASCII 29) or end of input within {spec.max_length} characters",
        )
    return field, end + 1 if terminated else end


def _check_charset(data, field, field_pos, spec):
    for i, ch in enumerate(field):
        if ch not in spec.charset:
            raise GS1ParseError(
                "INVALID_CHARACTER",
                f"Character {ch!r} (U+{ord(ch):04X}) is not allowed in AI {spec.ai} ({spec.name})",
                offset=_byte_offset(data, field_pos + i),
                expected=spec.charset_rule,
                found=ch,
            )


def _check_gtin(data, gtin, field_pos):
    total = 0
    for i, ch in enumerate(reversed(gtin[:-1])):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    expected = (10 - total % 10) % 10
    if expected != int(gtin[-1]):
        raise GS1ParseError(
            "INVALID_CHECK_DIGIT",
            f"GTIN check digit mismatch: expected {expected}, found {gtin[-1]}",
            offset=_byte_offset(data, field_pos + len(gtin) - 1),
            expected=f"Mod-10 check digit {expected}",
            found=gtin[-1],
        )


def _check_date(data, yymmdd, field_pos):
    yy = int(yymmdd[0:2])
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    year = 2000 + yy
    if not 1 <= mm <= 12:
        raise GS1ParseError(
            "INVALID_DATE",
            f"Invalid expiry month {mm:02d} in AI 17 date '{yymmdd}'",
            offset=_byte_offset(data, field_pos + 2),
            expected="month 01-12",
            found=yymmdd[2:4],
        )
    last_day = calendar.monthrange(year, mm)[1]
    if dd == 0:
        return  # GS1: day "00" denotes the last day of the month
    if dd > last_day:
        raise GS1ParseError(
            "INVALID_DATE",
            f"Invalid expiry day {dd:02d} for {year}-{mm:02d} (max {last_day})",
            offset=_byte_offset(data, field_pos + 4),
            expected=f"day 01-{last_day} for {year}-{mm:02d} (00 = last day of month)",
            found=yymmdd[4:6],
        )
