"""GS1 element-string and GS1 Digital Link URI parser.

Supports AI 01 (GTIN-14), AI 10 (batch/lot), AI 17 (expiry date) and
AI 21 (serial number). Fixed-length fields (01, 17) are delimited
implicitly by their length; variable-length fields (10, 21) must be
terminated by the FNC1 separator (ASCII 29, GS) unless they are the
last field in the string.

Inputs starting with ``http://`` or ``https://`` are parsed as GS1
Digital Link URIs: AI/value pairs are read from the URI path (in order),
followed by AI query parameters (in order). Values are percent-decoded
as UTF-8 before the usual charset, length, check-digit and date
validation; all reported offsets refer to the original input.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass

GS = "\x1d"  # ASCII 29 <GS>, transmitted as FNC1 by scanners

DIGITS = frozenset("0123456789")
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

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
    """Parse a raw GS1 scan string or GS1 Digital Link URI into elements."""
    if data.startswith(("http://", "https://")):
        return parse_digital_link(data)
    return parse_element_string(data)


# ------------------------------------------------------------ element strings

def parse_element_string(data: str) -> list[Element]:
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

        offset_at = _offset_in(data, value_pos)
        _check_charset(value, spec, offset_at)
        if spec.ai == "01":
            _check_gtin(value, offset_at)
        elif spec.ai == "17":
            _check_date(value, offset_at)

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


def _offset_in(data, start):
    """Map a field character index to its byte offset within ``data``."""

    def offset_at(index):
        return _byte_offset(data, start + index)

    return offset_at


# --------------------------------------------------------- shared validation

def _check_charset(field, spec, offset_at):
    for i, ch in enumerate(field):
        if ch not in spec.charset:
            raise GS1ParseError(
                "INVALID_CHARACTER",
                f"Character {ch!r} (U+{ord(ch):04X}) is not allowed in AI {spec.ai} ({spec.name})",
                offset=offset_at(i),
                expected=spec.charset_rule,
                found=ch,
            )


def _check_gtin(gtin, offset_at):
    total = 0
    for i, ch in enumerate(reversed(gtin[:-1])):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    expected = (10 - total % 10) % 10
    if expected != int(gtin[-1]):
        raise GS1ParseError(
            "INVALID_CHECK_DIGIT",
            f"GTIN check digit mismatch: expected {expected}, found {gtin[-1]}",
            offset=offset_at(len(gtin) - 1),
            expected=f"Mod-10 check digit {expected}",
            found=gtin[-1],
        )


def _check_date(yymmdd, offset_at):
    yy = int(yymmdd[0:2])
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    year = 2000 + yy
    if not 1 <= mm <= 12:
        raise GS1ParseError(
            "INVALID_DATE",
            f"Invalid expiry month {mm:02d} in AI 17 date '{yymmdd}'",
            offset=offset_at(2),
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
            offset=offset_at(4),
            expected=f"day 01-{last_day} for {year}-{mm:02d} (00 = last day of month)",
            found=yymmdd[4:6],
        )


# --------------------------------------------------------- digital link URIs

def parse_digital_link(uri: str) -> list[Element]:
    """Parse a GS1 Digital Link URI into an ordered list of elements.

    AI/value pairs are read from the URI path first, then AI query
    parameters in order; each AI may appear only once across the whole
    URI. Offsets always refer to the original URI.
    """
    end = len(uri)
    fragment = uri.find("#")
    if fragment != -1:
        end = fragment  # the fragment is not part of the identifier
    qmark = uri.find("?", 0, end)
    path_end = qmark if qmark != -1 else end

    authority_start = 8 if uri.startswith("https://") else 7  # after "http(s)://"
    slash = uri.find("/", authority_start, path_end)

    elements = []
    seen = {}  # ai -> "path" | "query string"

    if slash != -1 and slash + 1 < path_end:
        segments = _split_segments(uri, slash + 1, path_end)
        if len(segments) % 2 != 0:
            start, stop = segments[-1]
            raise GS1ParseError(
                "INVALID_URI",
                "URI path must consist of AI/value pairs; "
                f"segment {uri[start:stop]!r} has no value",
                offset=_byte_offset(uri, start),
                expected="path segments in AI/value pairs, e.g. /01/09501101530003",
                found=uri[start:stop],
            )
        for i in range(0, len(segments), 2):
            ai_span, value_span = segments[i], segments[i + 1]
            ai, _ = _percent_decode(uri, *ai_span)
            spec = _resolve_ai(uri, ai, ai_span, "path")
            _check_duplicate(uri, spec, ai_span, "path", seen)
            value, index_map = _percent_decode(uri, *value_span)
            _validate_uri_value(uri, spec, value_span, value, index_map)
            elements.append(
                Element(
                    ai=spec.ai,
                    name=spec.name,
                    value=value,
                    offset=_byte_offset(uri, ai_span[0]),
                )
            )

    if qmark != -1:
        for key_span, value_span in _split_query(uri, qmark + 1, end):
            key, _ = _percent_decode(uri, *key_span)
            if not key or not all(c in DIGITS for c in key):
                continue  # not an AI parameter (e.g. linkType); ignored
            spec = _resolve_ai(uri, key, key_span, "query string")
            _check_duplicate(uri, spec, key_span, "query string", seen)
            value, index_map = _percent_decode(uri, *value_span)
            _validate_uri_value(uri, spec, value_span, value, index_map)
            elements.append(
                Element(
                    ai=spec.ai,
                    name=spec.name,
                    value=value,
                    offset=_byte_offset(uri, key_span[0]),
                )
            )

    if not elements:
        raise GS1ParseError(
            "EMPTY_INPUT",
            "URI contains no AI elements",
            offset=0,
            expected=f"path AI/value pairs or query parameters with AI {SUPPORTED_AIS}",
        )
    return elements


def _split_segments(uri, start, end):
    """Split ``uri[start:end]`` on '/', returning (start, end) char spans."""
    spans = []
    i = start
    while i < end:
        j = uri.find("/", i, end)
        if j == -1:
            spans.append((i, end))
            break
        spans.append((i, j))
        i = j + 1
    return spans


def _split_query(uri, start, end):
    """Split the query string into ((key_start, key_end), (value_start, value_end))
    spans, one per non-empty ``key=value`` part."""
    parts = []
    i = start
    while i < end:
        amp = uri.find("&", i, end)
        part_end = amp if amp != -1 else end
        if part_end > i:
            eq = uri.find("=", i, part_end)
            if eq == -1:
                parts.append(((i, part_end), (part_end, part_end)))
            else:
                parts.append(((i, eq), (eq + 1, part_end)))
        if amp == -1:
            break
        i = amp + 1
    return parts


def _percent_decode(uri, start, end):
    """Percent-decode ``uri[start:end]`` as UTF-8.

    Returns ``(text, index_map)`` where ``index_map[i]`` is the character
    index within ``uri`` at which the encoding of ``text[i]`` starts, so
    validation errors can be reported against the original URI.
    """
    data = bytearray()
    byte_positions = []  # character index in `uri` for each byte in `data`
    i = start
    while i < end:
        ch = uri[i]
        if ch == "%":
            hexpart = uri[i + 1 : i + 3] if i + 3 <= end else ""
            if len(hexpart) != 2 or any(c not in HEX_DIGITS for c in hexpart):
                raise GS1ParseError(
                    "INVALID_PERCENT_ENCODING",
                    "Invalid percent-encoding; expected '%' followed by two "
                    "hexadecimal digits",
                    offset=_byte_offset(uri, i),
                    expected="% followed by two hexadecimal digits",
                    found=uri[i : min(i + 3, end)],
                )
            data.append(int(hexpart, 16))
            byte_positions.append(i)
            i += 3
        else:
            encoded = ch.encode("utf-8")
            data.extend(encoded)
            byte_positions.extend([i] * len(encoded))
            i += 1
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        bad = byte_positions[min(exc.start, len(byte_positions) - 1)]
        raise GS1ParseError(
            "INVALID_PERCENT_ENCODING",
            f"Percent-encoded data is not valid UTF-8: {exc.reason}",
            offset=_byte_offset(uri, bad),
            expected="percent-encoded UTF-8",
        ) from exc
    index_map = []
    byte_index = 0
    for ch in text:
        index_map.append(byte_positions[byte_index])
        byte_index += len(ch.encode("utf-8"))
    return text, index_map


def _resolve_ai(uri, ai, span, location):
    """Return the AISpec for a decoded AI token, or raise a parse error."""
    if not ai or not all(c in DIGITS for c in ai):
        raise GS1ParseError(
            "INVALID_AI",
            f"Expected a 2-digit application identifier in the URI {location}, "
            f"found {ai!r}",
            offset=_byte_offset(uri, span[0]),
            expected=f"2-digit AI, one of: {SUPPORTED_AIS}",
            found=ai,
        )
    spec = AI_SPECS.get(ai)
    if spec is None:
        raise GS1ParseError(
            "UNSUPPORTED_AI",
            f"Unsupported application identifier '{ai}' in the URI {location}",
            offset=_byte_offset(uri, span[0]),
            expected=f"one of: {SUPPORTED_AIS}",
            found=ai,
        )
    return spec


def _check_duplicate(uri, spec, span, location, seen):
    if spec.ai in seen:
        if seen[spec.ai] == location:
            message = f"Duplicate application identifier '{spec.ai}' in the URI {location}"
        else:
            message = (
                f"Application identifier '{spec.ai}' appears in both the "
                "URI path and query string"
            )
        raise GS1ParseError(
            "DUPLICATE_AI",
            message,
            offset=_byte_offset(uri, span[0]),
            expected="each AI to appear only once in the URI",
            found=spec.ai,
        )
    seen[spec.ai] = location


def _validate_uri_value(uri, spec, value_span, value, index_map):
    """Length, charset, check-digit and date checks for a decoded URI value."""

    def offset_at(index):
        return _byte_offset(uri, index_map[index])

    _check_length(spec, value, offset_at, _byte_offset(uri, value_span[1]))
    _check_charset(value, spec, offset_at)
    if spec.ai == "01":
        _check_gtin(value, offset_at)
    elif spec.ai == "17":
        _check_date(value, offset_at)


def _check_length(spec, value, offset_at, end_offset):
    """Validate a decoded URI value against the AI length rules."""
    if len(value) < spec.min_length:
        if spec.fixed_length:
            raise GS1ParseError(
                "FIELD_TOO_SHORT",
                f"AI {spec.ai} ({spec.name}) requires exactly {spec.max_length} "
                f"characters but the value has {len(value)}",
                offset=end_offset,
                expected=f"{spec.max_length} characters for AI {spec.ai}",
                found=f"{len(value)} character(s)",
            )
        raise GS1ParseError(
            "FIELD_EMPTY",
            f"AI {spec.ai} ({spec.name}) requires at least {spec.min_length} character",
            offset=end_offset,
            expected=f"{spec.min_length}-{spec.max_length} characters for AI {spec.ai}",
            found="empty field",
        )
    if len(value) > spec.max_length:
        requirement = (
            f"requires exactly {spec.max_length}"
            if spec.fixed_length
            else f"allows at most {spec.max_length}"
        )
        raise GS1ParseError(
            "FIELD_TOO_LONG",
            f"AI {spec.ai} ({spec.name}) {requirement} characters "
            f"but the value has {len(value)}",
            offset=offset_at(spec.max_length),
            expected=(
                f"{spec.max_length} characters for AI {spec.ai}"
                if spec.fixed_length
                else f"at most {spec.max_length} characters for AI {spec.ai}"
            ),
            found=f"{len(value)} character(s)",
        )
