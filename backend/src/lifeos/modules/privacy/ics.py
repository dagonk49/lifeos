"""Minimal iCalendar writer (RFC 5545) for the export: no dependency, only what the export
writes (VEVENT, VTODO, VALARM).

Lines end with CRLF and are folded at 75 octets without splitting a UTF-8 character; TEXT
values are escaped (backslash, semicolon, comma, newline) and stripped of the control
characters the format forbids. Instants are written in UTC (`Z`), all-day dates as
`VALUE=DATE`.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

PRODID = "-//LifeOS//Export//FR"
MAX_OCTETS = 75


def escape_text(value: str) -> str:
    """A TEXT value (RFC 5545 §3.3.11): control characters other than tab and newline are
    removed, CRLF and CR become newlines, then `\\ ; , newline` are escaped."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if c in "\t\n" or (c >= " " and c != "\x7f"))
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """Splits a content line into lines of at most 75 octets (continuations start with a
    space, which counts), never inside a UTF-8 character. Returns them joined by CRLF."""
    parts: list[str] = []
    current = ""
    size = 0
    limit = MAX_OCTETS
    for char in line:
        width = len(char.encode())
        if size + width > limit:
            parts.append(current)
            current, size, limit = "", 0, MAX_OCTETS - 1
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def day(value: date) -> str:
    return value.strftime("%Y%m%d")


class Component:
    """One component: properties in order, then nested components."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.lines: list[str] = []
        self.children: list[Component] = []

    def raw(self, name: str, value: str) -> "Component":
        """A property whose value is already in its iCalendar form."""
        self.lines.append(f"{name}:{value}")
        return self

    def text(self, name: str, value: str | None) -> "Component":
        if value:
            self.raw(name, escape_text(value))
        return self

    def texts(self, name: str, values: Sequence[str]) -> "Component":
        """A multi-valued TEXT property (CATEGORIES)."""
        kept = [escape_text(v) for v in values if v]
        if kept:
            self.raw(name, ",".join(kept))
        return self

    def instant(self, name: str, value: datetime | None) -> "Component":
        if value is not None:
            self.raw(name, utc(value))
        return self

    def on_day(self, name: str, value: date) -> "Component":
        self.lines.append(f"{name};VALUE=DATE:{day(value)}")
        return self

    def add(self, child: "Component") -> "Component":
        self.children.append(child)
        return self

    def content_lines(self) -> Iterable[str]:
        yield f"BEGIN:{self.name}"
        yield from self.lines
        for child in self.children:
            yield from child.content_lines()
        yield f"END:{self.name}"


def calendar(components: Iterable[Component], *, name: str) -> str:
    """A VCALENDAR document holding `components`, folded, CRLF-terminated."""
    root = Component("VCALENDAR")
    root.raw("VERSION", "2.0").raw("PRODID", PRODID).raw("CALSCALE", "GREGORIAN")
    root.raw("METHOD", "PUBLISH").text("X-WR-CALNAME", name)
    for component in components:
        root.add(component)
    return "".join(fold(line) + "\r\n" for line in root.content_lines())
