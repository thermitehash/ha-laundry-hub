"""Pure state machine tests (also the mutation-testing target)."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.laundry_hub.const import (
    EVENT_DRYING,
    EVENT_IN_WASH,
    EVENT_PUT_AWAY,
    FEEDBACK_KNOWN,
    FEEDBACK_MOVED,
    MODE_AUTO,
    MODE_DRY,
    MODE_SORT,
    REPEAT_WINDOW,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_NEW,
    STATE_PUT_AWAY,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from custom_components.laundry_hub.statemachine import (
    Decision,
    InvalidTap,
    TapInput,
    decide,
    is_missing,
    within_repeat_window,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def tap(
    station: str = "laundry",
    mode: str = MODE_AUTO,
    state: str = STATE_PUT_AWAY,
    location: str | None = "closet_1",
    last: datetime | None = None,
) -> Decision:
    return decide(TapInput(station, mode, state, location, last, NOW))


def wash(state: str, *, repeat: bool = False) -> Decision:
    return Decision(
        new_state=STATE_IN_WASH,
        location="laundry",
        zone_type=SUBENTRY_WASH_BIN,
        feedback=FEEDBACK_KNOWN,
        event_type=EVENT_IN_WASH,
        changed=state != STATE_IN_WASH,
        repeat=repeat,
        laundry_tap=True,
    )


def dry(state: str, *, repeat: bool = False) -> Decision:
    return Decision(
        new_state=STATE_DRYING,
        location="laundry",
        zone_type=SUBENTRY_DRY_TYPE,
        feedback=FEEDBACK_KNOWN,
        event_type=EVENT_DRYING,
        changed=state != STATE_DRYING,
        repeat=repeat,
        laundry_tap=True,
    )


@pytest.mark.parametrize("state", [STATE_NEW, STATE_PUT_AWAY])
def test_auto_not_in_wash_goes_to_wash(state: str) -> None:
    location = {"new": None, "put_away": "closet_2"}[state]
    assert tap(state=state, location=location) == wash(state)


@pytest.mark.parametrize(
    "last",
    [
        None,
        NOW - REPEAT_WINDOW,
        NOW - timedelta(days=3),
        NOW + timedelta(hours=2),  # clock moved backwards a lot
    ],
)
def test_auto_drying_stays_drying_regardless_of_time(last: datetime | None) -> None:
    """Owner decision (v0.1.1): auto never rewashes a drying garment."""
    assert tap(state=STATE_DRYING, location="laundry", last=last) == dry(
        STATE_DRYING, repeat=True
    )


def test_sort_rewashes_drying_garment() -> None:
    result = tap(mode=MODE_SORT, state=STATE_DRYING, location="laundry")
    assert result == wash(STATE_DRYING)
    assert result.changed is True


def test_auto_in_wash_after_window_goes_to_dry() -> None:
    last = NOW - REPEAT_WINDOW  # exactly 5 min → step
    assert tap(state=STATE_IN_WASH, location="laundry", last=last) == dry(STATE_IN_WASH)


def test_auto_in_wash_never_tapped_goes_to_dry() -> None:
    assert tap(state=STATE_IN_WASH, location="laundry", last=None) == dry(STATE_IN_WASH)


def test_auto_in_wash_repeat_within_window() -> None:
    last = NOW - REPEAT_WINDOW + timedelta(seconds=1)
    assert tap(state=STATE_IN_WASH, location="laundry", last=last) == wash(
        STATE_IN_WASH, repeat=True
    )


def test_auto_drying_repeat_within_window() -> None:
    last = NOW - timedelta(minutes=1)
    assert tap(state=STATE_DRYING, location="laundry", last=last) == dry(
        STATE_DRYING, repeat=True
    )


def test_auto_put_away_recent_laundry_tap_still_washes() -> None:
    # Put away right after a laundry tap: a new laundry tap starts a wash.
    last = NOW - timedelta(minutes=1)
    assert tap(state=STATE_PUT_AWAY, location="closet_1", last=last) == wash(
        STATE_PUT_AWAY
    )


def test_auto_new_recent_tap_washes() -> None:
    last = NOW - timedelta(minutes=1)
    assert tap(state=STATE_NEW, location=None, last=last) == wash(STATE_NEW)


@pytest.mark.parametrize(
    "state", [STATE_NEW, STATE_PUT_AWAY, STATE_IN_WASH, STATE_DRYING]
)
@pytest.mark.parametrize("recent", [True, False])
def test_sort_forces_wash(state: str, recent: bool) -> None:
    last = NOW - timedelta(minutes=1) if recent else None
    assert tap(mode=MODE_SORT, state=state, last=last) == wash(state)


@pytest.mark.parametrize(
    "state", [STATE_NEW, STATE_PUT_AWAY, STATE_IN_WASH, STATE_DRYING]
)
@pytest.mark.parametrize("recent", [True, False])
def test_dry_forces_dry(state: str, recent: bool) -> None:
    last = NOW - timedelta(minutes=1) if recent else None
    assert tap(mode=MODE_DRY, state=state, last=last) == dry(state)


@pytest.mark.parametrize("closet", ["closet_1", "closet_2"])
@pytest.mark.parametrize("mode", [MODE_AUTO, MODE_SORT, MODE_DRY])
def test_closet_moves(closet: str, mode: str) -> None:
    assert tap(station=closet, mode=mode, state=STATE_IN_WASH, location="laundry") == (
        Decision(
            new_state=STATE_PUT_AWAY,
            location=closet,
            zone_type=None,
            feedback=FEEDBACK_MOVED,
            event_type=EVENT_PUT_AWAY,
            changed=True,
            repeat=False,
            laundry_tap=False,
        )
    )


def test_closet_already_there() -> None:
    assert tap(station="closet_1", state=STATE_PUT_AWAY, location="closet_1") == (
        Decision(
            new_state=STATE_PUT_AWAY,
            location="closet_1",
            zone_type=None,
            feedback=FEEDBACK_KNOWN,
            event_type=EVENT_PUT_AWAY,
            changed=False,
            repeat=True,
            laundry_tap=False,
        )
    )


def test_closet_other_closet_is_move() -> None:
    result = tap(station="closet_2", state=STATE_PUT_AWAY, location="closet_1")
    assert result.feedback == FEEDBACK_MOVED
    assert result.changed is True
    assert result.repeat is False
    assert result.location == "closet_2"


def test_closet_location_matches_but_not_put_away_is_move() -> None:
    # Defensive: a corrupt location must not make a non-put-away tap a no-op.
    result = tap(station="closet_1", state=STATE_DRYING, location="closet_1")
    assert result.feedback == FEEDBACK_MOVED
    assert result.changed is True


def test_unknown_station() -> None:
    with pytest.raises(InvalidTap, match="unknown station"):
        tap(station="garage")


def test_invalid_state() -> None:
    with pytest.raises(InvalidTap, match="invalid garment state"):
        tap(state="missing")


def test_unknown_mode_treated_as_auto() -> None:
    assert tap(mode="bogus", state=STATE_PUT_AWAY) == wash(STATE_PUT_AWAY)


def test_repeat_window_boundaries() -> None:
    assert within_repeat_window(None, NOW) is False
    assert within_repeat_window(NOW, NOW) is True
    assert within_repeat_window(NOW - REPEAT_WINDOW, NOW) is False
    assert within_repeat_window(NOW - REPEAT_WINDOW + timedelta(microseconds=1), NOW)
    # Clock moved backwards a little: still a repeat.
    assert within_repeat_window(NOW + timedelta(seconds=30), NOW) is True
    # Clock moved backwards a lot: not stuck in repeat mode.
    assert within_repeat_window(NOW + timedelta(hours=2), NOW) is False
    assert within_repeat_window(NOW, NOW, timedelta(seconds=1)) is True
    assert (
        within_repeat_window(NOW - timedelta(seconds=2), NOW, timedelta(seconds=1))
        is False
    )


def test_timezone_aware_comparison() -> None:
    # Same instant expressed in another offset is a repeat.
    from datetime import timezone

    other = (NOW - timedelta(minutes=1)).astimezone(timezone(timedelta(hours=-8)))
    assert tap(state=STATE_IN_WASH, location="laundry", last=other).repeat is True


def test_is_missing() -> None:
    created = NOW - timedelta(days=30)
    after = timedelta(days=14)
    assert is_missing(None, created, NOW, after) is True
    assert is_missing(None, NOW - timedelta(days=13), NOW, after) is False
    assert is_missing(NOW - timedelta(days=14), created, NOW, after) is True
    assert (
        is_missing(NOW - timedelta(days=14) + timedelta(seconds=1), created, NOW, after)
        is False
    )
    # last_seen wins over created
    assert is_missing(NOW, created, NOW, after) is False
