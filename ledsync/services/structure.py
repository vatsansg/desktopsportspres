"""The LED structure of an event (BRD Section 11) - derived ONLY from the event configuration.

This application does not decide which tables or LED types exist: the `_GUID.json` exported by
the web application is the source of truth (BRD 11, 38). The stored configuration text is
re-read through the same strict parser used at registration (never a bare `json.loads`), so a
corrupted or hand-edited row cannot introduce structure that the web application never exported.
"""

from dataclasses import dataclass

from . import registration as reg

INNER, OUTER, MAIN = "Inner", "Outer", "MainLED"
LED_TYPES = (INNER, OUTER, MAIN)                     # canonical names, in display order (owner decision 21/09/26)
LED_LABELS = {INNER: "Inner", OUTER: "Outer", MAIN: "Main LED"}

# How the different spellings seen in the BRD, the change log and the real cloud folders map
# onto the canonical names. Matching is case-insensitive (the real cloud folders are lower-case).
_ALIASES = {"inner": INNER, "outer": OUTER, "mainled": MAIN, "main led": MAIN, "main_led": MAIN, "main-led": MAIN}


def canonical_led_type(text: str | None) -> str | None:
    """'inner' / 'Inner' / 'MAINLED' / 'Main LED' -> the canonical name, or None if unknown."""
    return _ALIASES.get((text or "").strip().casefold())


class StructureError(Exception):
    """The stored configuration cannot be read; the message is safe to show."""


@dataclass(frozen=True)
class TableStructure:
    number: int
    inner: bool
    outer: bool
    main: bool

    def enabled(self, led_type: str) -> bool:
        return {INNER: self.inner, OUTER: self.outer, MAIN: self.main}[led_type]

    @property
    def enabled_types(self) -> tuple[str, ...]:
        return tuple(t for t in LED_TYPES if self.enabled(t))


@dataclass(frozen=True)
class EventStructure:
    tables: tuple[TableStructure, ...]

    @property
    def enabled_pairs(self) -> tuple[tuple[int, str], ...]:
        """(table number, LED type) for every enabled LED, tables ascending, LED types in display order."""
        return tuple((t.number, led) for t in self.tables for led in t.enabled_types)


def from_config(config: reg.EventConfig) -> EventStructure:
    tables = sorted(config.tables, key=lambda t: t.number)
    return EventStructure(tuple(TableStructure(t.number, t.inner, t.outer, t.main) for t in tables))


def load_structure(conn, event_id: str) -> EventStructure:
    """The structure of a REGISTERED event, from its stored configuration."""
    row = conn.execute("SELECT configuration_json FROM events WHERE event_id = ? COLLATE NOCASE",
                       (event_id,)).fetchone()
    if row is None:
        raise StructureError("That event is not registered.")
    raw = row["configuration_json"]
    if not raw:
        raise StructureError("This event has no stored configuration. Re-register it to load its LED structure.")
    try:
        config = reg.parse_event_config(raw.encode("utf-8"))
    except reg.RegistrationError:
        raise StructureError("The stored configuration for this event could not be read. "
                             "Re-register the event to reload it.") from None
    return from_config(config)
