"""Pure garment state machine (no Home Assistant imports).

Kept free of I/O so it can be property-tested and mutation-tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .const import (
    CLOSET_STATIONS,
    EVENT_DRYING,
    EVENT_IN_WASH,
    EVENT_PUT_AWAY,
    FEEDBACK_KNOWN,
    FEEDBACK_MOVED,
    MODE_DRY,
    MODE_SORT,
    REPEAT_WINDOW,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_PUT_AWAY,
    STATION_LAUNDRY,
    STORED_STATES,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)


class InvalidTap(ValueError):
    """Raised for a tap that cannot be processed (unknown station, bad state)."""


@dataclass(frozen=True, slots=True)
class TapInput:
    """Everything the state machine needs to decide a tap."""

    station: str
    mode: str
    state: str
    location: str | None
    last_laundry_tap: datetime | None
    now: datetime


@dataclass(frozen=True, slots=True)
class Decision:
    """Outcome of a tap."""

    new_state: str
    location: str | None
    zone_type: str | None  # SUBENTRY_WASH_BIN / SUBENTRY_DRY_TYPE / None
    feedback: str
    event_type: str
    changed: bool
    repeat: bool
    laundry_tap: bool  # True when the tap counts as a laundry-station tap


def within_repeat_window(
    last: datetime | None, now: datetime, window: timedelta = REPEAT_WINDOW
) -> bool:
    """Return True if `last` is within the repeat window of `now`.

    A clock that moved backwards (last > now) is treated symmetrically so a small
    NTP correction does not turn a repeat tap into a state change, and a large
    backwards jump does not freeze the garment in repeat mode forever.
    """
    if last is None:
        return False
    return abs(now - last) < window


def _laundry_step(state: str, zone_type: str, *, repeat: bool) -> Decision:
    new_state = STATE_IN_WASH if zone_type == SUBENTRY_WASH_BIN else STATE_DRYING
    return Decision(
        new_state=new_state,
        location=STATION_LAUNDRY,
        zone_type=zone_type,
        feedback=FEEDBACK_KNOWN,
        event_type=EVENT_IN_WASH if new_state == STATE_IN_WASH else EVENT_DRYING,
        changed=new_state != state,
        repeat=repeat,
        laundry_tap=True,
    )


def decide(tap: TapInput) -> Decision:
    """Decide what a tap of a known garment does."""
    if tap.state not in STORED_STATES:
        raise InvalidTap(f"invalid garment state {tap.state!r}")

    if tap.station in CLOSET_STATIONS:
        already_here = tap.state == STATE_PUT_AWAY and tap.location == tap.station
        return Decision(
            new_state=STATE_PUT_AWAY,
            location=tap.station,
            zone_type=None,
            feedback=FEEDBACK_KNOWN if already_here else FEEDBACK_MOVED,
            event_type=EVENT_PUT_AWAY,
            changed=not already_here,
            repeat=already_here,
            laundry_tap=False,
        )

    if tap.station != STATION_LAUNDRY:
        raise InvalidTap(f"unknown station {tap.station!r}")

    if tap.mode == MODE_SORT:
        return _laundry_step(tap.state, SUBENTRY_WASH_BIN, repeat=False)
    if tap.mode == MODE_DRY:
        return _laundry_step(tap.state, SUBENTRY_DRY_TYPE, repeat=False)

    # Auto mode (unknown modes are treated as auto by the caller).
    if tap.state == STATE_DRYING:
        # Stays drying whatever the time: rewash needs mode sort.
        return _laundry_step(tap.state, SUBENTRY_DRY_TYPE, repeat=True)
    if tap.state == STATE_IN_WASH:
        if within_repeat_window(tap.last_laundry_tap, tap.now):
            return _laundry_step(tap.state, SUBENTRY_WASH_BIN, repeat=True)
        return _laundry_step(tap.state, SUBENTRY_DRY_TYPE, repeat=False)
    return _laundry_step(tap.state, SUBENTRY_WASH_BIN, repeat=False)


def is_missing(
    last_seen: datetime | None,
    created: datetime,
    now: datetime,
    missing_after: timedelta,
) -> bool:
    """Return True if a garment has not been seen for `missing_after`."""
    reference = last_seen if last_seen is not None else created
    return now - reference >= missing_after
