"""Laundry Hub runtime: tap handling, catalog operations, lights, feedback."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from . import taxonomy
from .access import Caller, Forbidden, can_see_garment
from .const import (
    CLOSET_STATIONS,
    CONF_COLOR,
    CONF_FEEDBACK_TIMEOUT,
    CONF_FLASH_DURATION,
    CONF_FOLLOW_HA_THEME,
    CONF_LIGHT_ENTITY,
    CONF_MISSING_AFTER,
    CONF_NAME,
    CONF_NOTIFY_TARGET,
    CONF_ORDER,
    CONF_STATION_DEVICES,
    DEFAULT_FEEDBACK_TIMEOUT,
    DEFAULT_FLASH_DURATION,
    DEFAULT_MISSING_AFTER,
    DOMAIN,
    EVENT_UNKNOWN,
    FEEDBACK_ERROR,
    FEEDBACK_KNOWN,
    FEEDBACK_UNKNOWN,
    MAX_NAME_LEN,
    MAX_NOTES_LEN,
    MODE_AUTO,
    MODE_DRY,
    MODE_SORT,
    MODES,
    PANEL_URL,
    SIGNAL_TAP,
    SIGNAL_UPDATE,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_NEW,
    STATE_PUT_AWAY,
    STATION_LAUNDRY,
    STATIONS,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
    TAP_EVENT,
    VERSION,
    ZONE_TYPES,
)
from .lights import FlashController
from .models import Garment, InvalidUid, Zone, garment_problems, normalize_uid
from .photos import PhotoError, decode_photo
from .purchases import PurchaseInbox, rank_cards
from .revs import RevTracker
from .statemachine import Decision, InvalidTap, TapInput, decide, is_missing
from .store import Catalog, CatalogError
from .wardrobe import Wardrobe

_LOGGER = logging.getLogger(__name__)

NOTIFY_THROTTLE = timedelta(hours=1)
BASE_VIEW_KEYS = (
    "id", "uids", "name", "owner", "wash_bin", "dry_type", "notes", "photos", "state",
    "location", "last_seen", "last_laundry_tap", "created", "recent_taps", "lifecycle",
)  # fmt: skip
MISSING_REFRESH = timedelta(hours=1)
LOCATIONS = (*CLOSET_STATIONS, STATE_IN_WASH, STATE_DRYING)
LINK_WINDOW = timedelta(minutes=2)  # "Attach tag": the next unknown tap links
# How much a manually set state is trusted as it ages (v0.2.8). Station taps
# are trusted fully; a hand-set state is fresh for FRESH_FOR, then fades to
# STALE_WEIGHT at STALE_AFTER. Future suggestions multiply by this weight.
FRESH_FOR = timedelta(days=2)
STALE_AFTER = timedelta(days=30)
STALE_WEIGHT = 0.25
UNKNOWN_WEIGHT = 0.5  # an untagged garment nobody has updated yet
MAX_WEAR_LOG = 1000  # oldest wears are dropped beyond this
# Availability for outfit ranking (v0.2.8 foundation; no UI until v0.3).
AVAILABILITY = {STATE_PUT_AWAY: 1.0, "new": 0.6, STATE_IN_WASH: 0.0, STATE_DRYING: 0.0}
WORN_AVAILABILITY = 0.3  # worn and not yet back in the laundry or closet
NEUTRAL = 0.5  # what an untrusted state fades towards
RECENTLY_WORN = timedelta(days=2)
RECENTLY_WORN_FACTOR = 0.7


def wear_entries(garment: Garment) -> list[dict[str, Any]]:
    """The garment's wear log entries (anything malformed is skipped)."""
    return [w for w in garment.wear_log if isinstance(w, dict) and w.get("at")]


def last_worn(garment: Garment) -> str | None:
    """ISO time of the latest wear, or None."""
    times = [str(w["at"]) for w in wear_entries(garment)]
    return max(times) if times else None


def availability(garment: Garment, now: datetime) -> float:
    """How likely the garment is ready to wear now (0..1). Internal; for v0.3.

    The laundry state gives a base (put away 1, new 0.6, in the wash or
    drying 0; worn and not back yet 0.3). An untrusted state (an untagged
    garment updated long ago) fades towards a neutral 0.5 instead of being
    believed, and anything worn in the last 2 days is a little less likely.
    """
    base = (
        WORN_AVAILABILITY if garment.out_since else AVAILABILITY.get(garment.state, 0.5)
    )
    trust = state_confidence(garment, now)
    score = trust * base + (1 - trust) * NEUTRAL
    worn = last_worn(garment)
    when = dt_util.parse_datetime(worn) if worn else None
    if when is not None and now - when < RECENTLY_WORN:
        score *= RECENTLY_WORN_FACTOR
    return round(score, 3)


def rank_available(
    garments: Iterable[Garment], now: datetime
) -> list[tuple[Garment, float]]:
    """Active garments, most likely ready to wear first (internal, tested)."""
    scored = [(g, availability(g, now)) for g in garments if g.lifecycle == "active"]
    return sorted(scored, key=lambda item: (-item[1], item[0].name.lower()))


def state_confidence(garment: Garment, now: datetime) -> float:
    """How much to trust the garment's laundry state (0..1).

    Tagged garments are tracked by stations: 1.0. An untagged garment's state
    is only as good as its last manual update, so it fades with age; never
    updated at all, it's a coin toss.
    """
    if garment.tagged:
        return 1.0
    if garment.state_updated is None:
        return UNKNOWN_WEIGHT
    age = now - garment.state_updated
    if age <= FRESH_FOR:
        return 1.0
    if age >= STALE_AFTER:
        return STALE_WEIGHT
    span = (STALE_AFTER - FRESH_FOR).total_seconds()
    fade = (age - FRESH_FOR).total_seconds() / span
    return round(1.0 - fade * (1.0 - STALE_WEIGHT), 3)


@dataclass(slots=True)
class LinkSession:
    """Waiting for an unknown tap to link to a garment (v0.2.8)."""

    garment_id: str
    replace: bool
    expires: datetime
    user_id: str | None


def validation_error(
    key: str, message: str, **placeholders: Any
) -> ServiceValidationError:
    """Build a translatable validation error with an English fallback."""
    return ServiceValidationError(
        message,
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


def zone_from_subentry(sub: ConfigSubentry) -> Zone:
    """Build a Zone from a config subentry (tolerates partial data)."""
    data = sub.data
    raw_color = data.get(CONF_COLOR) or (255, 255, 255)
    try:
        color = tuple(max(0, min(255, int(c))) for c in raw_color)[:3]
    except (TypeError, ValueError):
        color = (255, 255, 255)
    if len(color) != 3:
        color = (255, 255, 255)
    try:
        order = int(data.get(CONF_ORDER, 0))
    except (TypeError, ValueError):
        order = 0
    return Zone(
        id=sub.subentry_id,
        type=sub.subentry_type,
        name=str(data.get(CONF_NAME) or sub.title),
        color=(color[0], color[1], color[2]),
        light_entity=data.get(CONF_LIGHT_ENTITY) or None,
        order=order,
    )


@dataclass(frozen=True, slots=True)
class TapResult:
    """Outcome of a processed tap (for tests and the event entity)."""

    uid: str
    station: str
    feedback: str
    event_type: str
    garment_id: str | None = None
    decision: Decision | None = None


class LaundryHub:
    """Runtime object stored in entry.runtime_data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.catalog = Catalog(hass)
        self.flash = FlashController(hass)
        self._tap_lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._notified: dict[str, datetime] = {}
        self._link: LinkSession | None = None
        self._wear_undo: dict[str, datetime | None] = {}
        self.zone_snapshot: dict[str, ConfigSubentry] = dict(entry.subentries)
        self.wardrobe = Wardrobe(self)
        self.purchases = PurchaseInbox(self)
        self.revs = RevTracker(self)

    # --- settings ------------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        """Entry options merged over data (data used by older entries)."""
        return {**self.entry.data, **self.entry.options}

    @property
    def flash_duration(self) -> float:
        """Seconds a zone light stays on."""
        return float(self.options.get(CONF_FLASH_DURATION, DEFAULT_FLASH_DURATION))

    @property
    def feedback_timeout(self) -> float:
        """Seconds to wait for a station feedback action."""
        return float(self.options.get(CONF_FEEDBACK_TIMEOUT, DEFAULT_FEEDBACK_TIMEOUT))

    @property
    def missing_after(self) -> timedelta:
        """Not seen for this long → missing."""
        return timedelta(
            days=float(self.options.get(CONF_MISSING_AFTER, DEFAULT_MISSING_AFTER))
        )

    # --- zones ---------------------------------------------------------

    @property
    def zones(self) -> dict[str, Zone]:
        """All zones keyed by subentry id."""
        return {
            sid: zone_from_subentry(sub)
            for sid, sub in self.entry.subentries.items()
            if sub.subentry_type in ZONE_TYPES
        }

    def zones_of(self, zone_type: str) -> list[Zone]:
        """Zones of one type, sorted by order then name."""
        return sorted(
            (z for z in self.zones.values() if z.type == zone_type),
            key=lambda z: (z.order, z.name.lower()),
        )

    def resolve_zone(self, value: str, zone_type: str | None = None) -> Zone:
        """Resolve a zone by subentry id or (case-insensitive) name."""
        zones = self.zones
        zone = zones.get(value)
        if zone is None:
            matches = [
                z
                for z in zones.values()
                if z.name.casefold() == str(value).casefold()
                and (zone_type is None or z.type == zone_type)
            ]
            zone = matches[0] if len(matches) == 1 else None
        if zone is None or (zone_type is not None and zone.type != zone_type):
            kind = zone_type or "zone"
            raise validation_error(
                "unknown_zone", f"Unknown {kind}: {value}", zone=value, kind=kind
            )
        return zone

    # --- lifecycle -----------------------------------------------------

    async def async_setup(self) -> None:
        """Load the catalog and start listening for taps."""
        await self.catalog.async_load()
        self._repair_dangling_zones()
        self.revs.sync()
        self.entry.async_create_background_task(
            self.hass,
            self.wardrobe.async_ensure_thumbnails(),
            "laundry_hub thumbnails",
        )
        self._unsubs.append(self.hass.bus.async_listen(TAP_EVENT, self._handle_event))
        self._unsubs.append(
            async_track_time_interval(self.hass, self._refresh_missing, MISSING_REFRESH)
        )
        await self.purchases.async_setup()

    async def async_unload(self) -> None:
        """Stop listeners, turn off lights and flush storage."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.purchases.async_unload()
        await self.flash.async_stop()
        await self.catalog.async_flush()

    def _repair_dangling_zones(self) -> None:
        """Fix garments whose zone vanished while HA was not running.

        Normally removal of an in-use zone is blocked/undone; this is a last line
        of defence (e.g. a restored backup) so no garment points at a deleted zone.
        """
        bins = self.zones_of(SUBENTRY_WASH_BIN)
        dries = self.zones_of(SUBENTRY_DRY_TYPE)
        zone_ids = set(self.zones)
        changed = False
        for garment in self.catalog.garments.values():
            if garment.wash_bin not in zone_ids and bins:
                _LOGGER.warning(
                    "Garment %s pointed at missing wash bin; moved to %s",
                    garment.name,
                    bins[0].name,
                )
                garment.wash_bin = bins[0].id
                changed = True
            if garment.dry_type not in zone_ids and dries:
                _LOGGER.warning(
                    "Garment %s pointed at missing dry type; moved to %s",
                    garment.name,
                    dries[0].name,
                )
                garment.dry_type = dries[0].id
                changed = True
        if changed:
            self.catalog.schedule_save()

    @callback
    def _refresh_missing(self, _now: datetime) -> None:
        self.revs.sync()  # "missing" is derived from time: revs follow it
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def notify_changed(self) -> None:
        """Tell entities and panel subscribers the catalog changed.

        Revs are assigned first, so every "changed" message carries the rev
        that includes the change.
        """
        self.revs.sync()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # --- taps ----------------------------------------------------------

    @callback
    def _handle_event(self, event: Event) -> None:
        """Validate a tap event and process it in a background task."""
        data = event.data
        station = data.get("station_id")
        if station not in STATIONS:
            _LOGGER.warning("Ignoring tap from unknown station %r", station)
            return
        try:
            uid = normalize_uid(data.get("uid"))
        except InvalidUid as err:
            _LOGGER.warning("Ignoring tap with bad UID from %s: %s", station, err)
            return
        mode = data.get("mode", MODE_AUTO)
        if mode not in MODES:
            _LOGGER.warning("Unknown tap mode %r from %s; using auto", mode, station)
            mode = MODE_AUTO
        expected_device = (self.options.get(CONF_STATION_DEVICES) or {}).get(station)
        device_id = data.get("device_id")
        if expected_device and device_id and device_id != expected_device:
            _LOGGER.warning(
                "Ignoring tap claiming station %s from another device (%s)",
                station,
                device_id,
            )
            return
        self.entry.async_create_background_task(
            self.hass,
            self.async_handle_tap(
                str(station), uid, str(mode), dt_util.as_utc(event.time_fired)
            ),
            f"laundry_hub tap {uid}",
        )

    async def async_handle_tap(
        self,
        station: str,
        uid: str,
        mode: str = MODE_AUTO,
        tapped_at: datetime | None = None,
    ) -> TapResult:
        """Process one tap. Never raises into the event loop.

        ``tapped_at`` is the event's fire time, so taps queued behind the lock
        are judged by when they happened, not when they were processed.
        """
        try:
            async with self._tap_lock:
                result = await self._process_tap(
                    station, uid, mode, tapped_at or dt_util.utcnow()
                )
        except Exception:
            _LOGGER.exception("Unexpected error handling tap %s at %s", uid, station)
            result = TapResult(uid, station, FEEDBACK_ERROR, EVENT_UNKNOWN)
        self._send_feedback(station, result.feedback)
        return result

    async def _process_tap(
        self,
        station: str,
        uid: str,
        mode: str,
        now: datetime,
        origin: str | None = None,
    ) -> TapResult:
        """Run one tap through the state machine.

        ``origin`` names a phone ("phone:<device>"): it is what recent taps and
        the tap event show, while ``station`` is the station it acts as. A
        phone tap doesn't count towards station health.
        """
        garment = self.catalog.by_uid(uid)
        if garment is None:
            linked = self._link_pending(uid, origin or station, now)
            if linked is not None:
                return linked
            return self._unknown_tap(origin or station, uid, now)

        try:
            decision = decide(
                TapInput(
                    station=station,
                    mode=mode,
                    state=garment.state,
                    location=garment.location,
                    last_laundry_tap=garment.last_laundry_tap,
                    now=now,
                )
            )
        except InvalidTap as err:
            _LOGGER.error("Cannot process tap of %s: %s", garment.name, err)
            return TapResult(uid, station, FEEDBACK_ERROR, EVENT_UNKNOWN, garment.id)

        garment.state = decision.new_state
        garment.location = decision.location
        garment.out_since = None  # back in the laundry cycle: no longer "worn"
        garment.state_source = "tap"
        garment.state_updated = now
        garment.last_seen = now
        garment.last_seen_station = station
        if decision.laundry_tap:
            garment.last_laundry_tap = now
        if origin is None:
            self.catalog.record_station_tap(station, now)
        self.catalog.record_garment_tap(
            garment,
            {
                "time": now.isoformat(),
                "station": origin or station,
                "event": decision.event_type,
                "mode": mode,
                "repeat": decision.repeat,
            },
        )
        self.catalog.schedule_save()

        feedback = decision.feedback
        zone: Zone | None = None
        if decision.zone_type is not None:
            zone_id = (
                garment.wash_bin
                if decision.zone_type == SUBENTRY_WASH_BIN
                else garment.dry_type
            )
            zone = self.zones.get(zone_id)
            if zone is None:
                _LOGGER.error(
                    "Garment %s points at missing zone %s", garment.name, zone_id
                )
                feedback = FEEDBACK_ERROR

        self.notify_changed()
        async_dispatcher_send(
            self.hass,
            SIGNAL_TAP,
            decision.event_type,
            {
                "garment_id": garment.id,
                "name": garment.name,
                "owner": garment.owner,
                "uid": uid,
                "station": origin or station,
                "mode": mode,
                "zone": zone.name if zone else None,
                "repeat": decision.repeat,
                "time": now.isoformat(),
            },
        )
        if zone is not None:
            # Flash in the background so lights never delay the next tap.
            self.entry.async_create_background_task(
                self.hass,
                self.flash.async_flash(zone, self.flash_duration),
                f"laundry_hub flash {zone.name}",
            )
        return TapResult(
            uid, station, feedback, decision.event_type, garment.id, decision
        )

    def _unknown_tap(
        self, station: str, uid: str, now: datetime, tapped_by: str | None = None
    ) -> TapResult:
        self.catalog.record_unknown(uid, station, now, tapped_by)
        if station in STATIONS:  # a phone isn't a station (health)
            self.catalog.record_station_tap(station, now)
        self.notify_changed()
        async_dispatcher_send(
            self.hass,
            SIGNAL_TAP,
            EVENT_UNKNOWN,
            {"uid": uid, "station": station, "time": now.isoformat()},
        )
        last = self._notified.get(uid)
        if last is None or now - last >= NOTIFY_THROTTLE:
            self._notified = {
                k: v for k, v in self._notified.items() if now - v < NOTIFY_THROTTLE
            }
            self._notified[uid] = now
            self.entry.async_create_background_task(
                self.hass,
                self._async_notify_unknown(uid, station),
                f"laundry_hub notify {uid}",
            )
        return TapResult(uid, station, FEEDBACK_UNKNOWN, EVENT_UNKNOWN)

    # --- phone taps (v0.2.3) ----------------------------------------------

    @staticmethod
    def available_actions(garment: Garment) -> list[str]:
        """What a phone tap can do with this garment (more in v0.3)."""
        return [
            *(f"put_away:{closet}" for closet in CLOSET_STATIONS),
            f"in_wash:{garment.wash_bin}",
            f"drying:{garment.dry_type}",
        ]

    async def async_phone_tap(
        self,
        raw_uid: str,
        *,
        device_name: str | None,
        action: str | None,
        caller: Caller,
    ) -> dict[str, Any]:
        """A phone acting as a tap station.

        Without ``action``: an unknown tag is recorded like a station tap
        (event + Add); a known garment comes back with its available actions.
        With ``action``: applied through the same state machine as a station
        tap, recorded as ``phone:<device_name>``.
        """
        uid = self._uid(raw_uid)
        name = " ".join(str(device_name or "").split())[:60] or "phone"
        origin = f"phone:{name}"
        now = dt_util.utcnow()
        garment = self.catalog.by_uid(uid)
        if garment is None:
            if action:
                raise validation_error(
                    "tag_not_enrolled", "That tag isn't on a garment yet"
                )
            async with self._tap_lock:
                linked = self._link_pending(uid, origin, now, caller=caller)
                if linked is not None:
                    tagged = self.catalog.garments[str(linked.garment_id)]
                    return {
                        "known": True,
                        "uid": uid,
                        "linked": True,
                        "garment": self.garment_view(tagged),
                        "available_actions": self.available_actions(tagged),
                        "rev": self.revs.rev,
                    }
                self._unknown_tap(origin, uid, now, caller.user_id)
            # The tapper may enrol it (members: for themselves, within 30 min).
            can_enrol = caller.is_admin or (
                caller.household and caller.owner is not None
            )
            return {
                "known": False,
                "uid": uid,
                "can_enroll": can_enrol,
                "owner": None if caller.is_admin else caller.owner,
                "rev": self.revs.rev,
            }
        if not can_see_garment(caller, garment):
            raise Forbidden("That tag belongs to someone else's garment")
        actions = self.available_actions(garment)
        if action is None:
            return {
                "known": True,
                "uid": uid,
                "garment": self.garment_view(garment),
                "available_actions": actions,
                "rev": self.revs.rev,
            }
        if action not in actions:
            raise validation_error(
                "invalid_action",
                f"{action} isn't possible for this garment",
                action=action,
            )
        kind, _, target = action.partition(":")
        if kind == "put_away":
            station, mode = target, MODE_AUTO
        elif kind == "in_wash":
            station, mode = STATION_LAUNDRY, MODE_SORT
        else:
            station, mode = STATION_LAUNDRY, MODE_DRY
        async with self._tap_lock:
            result = await self._process_tap(station, uid, mode, now, origin=origin)
        return {
            "known": True,
            "uid": uid,
            "applied": action,
            "event_type": result.event_type,
            "repeat": bool(result.decision and result.decision.repeat),
            "garment": self.garment_view(garment),
            "available_actions": self.available_actions(garment),
            "rev": self.revs.rev,
        }

    # --- untagged garments and tag linking (v0.2.8) --------------------

    def set_manual_state(self, garment: Garment, action: str) -> Garment:
        """Change an untagged garment's laundry state by hand.

        ``to_laundry`` → in the wash, ``put_away`` → clean and put away. The
        location stays unknown (None): a station never saw it.
        """
        if garment.tagged:
            raise validation_error(
                "garment_has_tag", "Tap its tag at a station to change its state"
            )
        states = {"to_laundry": STATE_IN_WASH, "put_away": STATE_PUT_AWAY}
        if action not in states:
            raise validation_error(
                "invalid_action", f"{action} isn't a manual action", action=action
            )
        now = dt_util.utcnow()
        garment.state = states[action]
        garment.location = None
        garment.out_since = None
        garment.state_source = "manual"
        garment.state_updated = now
        self.catalog.record_garment_tap(
            garment,
            {
                "time": now.isoformat(),
                "station": "manual",
                "event": garment.state,
                "mode": "manual",
                "repeat": False,
            },
        )
        self.catalog.schedule_save()
        self.notify_changed()
        return garment

    def record_wear(
        self,
        garment: Garment,
        *,
        source: str = "manual",
        station: str | None = None,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        """Wore it: add a wear-log entry and mark the garment worn.

        Entries are ``{id, at, source}`` (+ ``station`` for v0.3's station
        wears). The id lets a wear be undone or edited without a migration.
        Worn lasts until the garment's laundry state next changes.
        """
        when = at or dt_util.utcnow()
        entry: dict[str, Any] = {
            "id": ulid_now(),
            "at": when.isoformat(),
            "source": source,
        }
        if station:
            entry["station"] = station
        self._wear_undo[entry["id"]] = garment.out_since
        garment.wear_log = [*garment.wear_log, entry][-MAX_WEAR_LOG:]
        garment.out_since = when
        self.catalog.schedule_save()
        self.notify_changed()
        return entry

    def undo_wear(self, garment: Garment, wear_id: str) -> Garment:
        """Remove a manual wear (the toast's Undo); "worn" goes back as it was."""
        entry = next((w for w in wear_entries(garment) if w.get("id") == wear_id), None)
        if entry is None or entry.get("source") != "manual":
            raise validation_error("unknown_wear", "That wear isn't in the log")
        garment.wear_log = [w for w in garment.wear_log if w is not entry]
        if wear_id in self._wear_undo:
            garment.out_since = self._wear_undo.pop(wear_id)
        elif garment.out_since and garment.out_since.isoformat() == entry["at"]:
            garment.out_since = None
        self.catalog.schedule_save()
        self.notify_changed()
        return garment

    def start_link(
        self, garment: Garment, *, replace: bool = False, user_id: str | None = None
    ) -> datetime:
        """Link the next unknown tap (any station or phone) to this garment.

        One session at a time; a new one replaces the old. Returns the expiry.
        """
        expires = dt_util.utcnow() + LINK_WINDOW
        self._link = LinkSession(garment.id, replace, expires, user_id)
        self.notify_changed()
        return expires

    def cancel_link(self) -> None:
        """Stop waiting for a tap."""
        self._link = None
        self.notify_changed()

    def link_status(self) -> dict[str, Any] | None:
        """The active link session, for the panel (None when there is none)."""
        session = self._link
        if session is None or dt_util.utcnow() > session.expires:
            return None
        return {
            "garment_id": session.garment_id,
            "replace": session.replace,
            "expires": session.expires.isoformat(),
        }

    def _link_pending(
        self, uid: str, origin: str, now: datetime, caller: Caller | None = None
    ) -> TapResult | None:
        """An unknown tap during a link session links to that garment."""
        session = self._link
        if session is None:
            return None
        if now > session.expires:
            self._link = None
            return None
        if (
            caller is not None
            and not caller.is_admin
            and session.user_id
            not in (
                None,
                caller.user_id,
            )
        ):
            return None  # a member's phone only completes their own session
        garment = self.catalog.garments.get(session.garment_id)
        self._link = None
        if garment is None:
            return None
        self._apply_tag(garment, uid, replace=session.replace)
        _LOGGER.info("Linked a tag tapped at %s to %s", origin, garment.name)
        return TapResult(uid, origin, FEEDBACK_KNOWN, EVENT_UNKNOWN, garment.id)

    def attach_tag(
        self,
        garment: Garment,
        raw_uid: str,
        *,
        replace: bool = False,
        move: bool = False,
    ) -> Garment:
        """Attach a known tag UID to a garment now.

        A tag on another garment is refused (``tag_in_use``) unless ``move``.
        ``replace`` removes the garment's current tags first (a lost tag).
        """
        uid = self._uid(raw_uid)
        other = self.catalog.by_uid(uid)
        if other is not None and other.id != garment.id:
            if not move:
                raise validation_error(
                    "tag_in_use",
                    f"That tag belongs to {other.name}; move it here instead?",
                    name=other.name,
                    garment_id=other.id,
                )
            self.catalog.remove_uid(other.id, uid, allow_last=True)
        self._apply_tag(garment, uid, replace=replace)
        return garment

    def _apply_tag(self, garment: Garment, uid: str, *, replace: bool) -> None:
        if replace:
            for old in list(garment.uids):
                if old != uid:
                    self.catalog.remove_uid(garment.id, old, allow_last=True)
        self.catalog.add_uid(garment.id, uid)
        self.catalog.schedule_save()
        self.notify_changed()

    def remove_tag(self, garment: Garment, raw_uid: str | None = None) -> Garment:
        """Remove one tag, or all (back to untagged; history is kept)."""
        uids = [self._uid(raw_uid)] if raw_uid else list(garment.uids)
        for uid in uids:
            self.catalog.remove_uid(garment.id, uid, allow_last=True)
        if not garment.tagged:
            garment.location = None  # no station can vouch for it any more
        self.catalog.schedule_save()
        self.notify_changed()
        return garment

    def link_candidates(
        self, uid: str | None = None, *, owner: str | None = None
    ) -> list[Garment]:
        """Untagged garments, most likely match first.

        The tag itself says nothing about the garment, so the clues are who
        tapped it (their owner first), then the newest garments, grouped by
        type and colour so similar ones sit together.
        """
        tag = self.catalog.unknown_tags.get(self._uid(uid)) if uid else None
        if owner is None and tag and tag.get("tapped_by"):
            from .access import owner_for  # noqa: PLC0415 - avoid import cycle

            owner = owner_for(self, str(tag["tapped_by"]))
        untagged = [
            g
            for g in self.catalog.garments.values()
            if not g.tagged and g.lifecycle == "active"
        ]

        def key(g: Garment) -> tuple[int, float, str, str, str]:
            desc = g.description or {}
            colors = desc.get("colors") or []
            color = (
                colors[0].get("name") if colors and isinstance(colors[0], dict) else ""
            )
            return (
                0 if owner and g.owner == owner else 1,
                -g.created.timestamp(),
                str(desc.get("type") or ""),
                str(color or ""),
                g.name.lower(),
            )

        return sorted(untagged, key=key)

    # --- feedback & notifications ----------------------------------------

    def feedback_service(self, station: str) -> str | None:
        """Return the ESPHome feedback service name for a station, if known."""
        device_id = (self.options.get(CONF_STATION_DEVICES) or {}).get(station)
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return None
        esp_entry = self.hass.config_entries.async_get_entry(device.config_entry_id)
        if esp_entry is None or esp_entry.domain != "esphome":
            return None
        name = esp_entry.data.get("device_name")
        if not name:
            return None
        return f"{str(name).replace('-', '_')}_feedback"

    def _send_feedback(self, station: str, result: str) -> None:
        service = self.feedback_service(station)
        if service is None:
            _LOGGER.debug("No feedback target for station %s", station)
            return
        self.entry.async_create_background_task(
            self.hass,
            self._async_feedback(service, result),
            f"laundry_hub feedback {station}",
        )

    async def _async_feedback(self, service: str, result: str) -> None:
        if not self.hass.services.has_service("esphome", service):
            _LOGGER.warning(
                "Feedback action esphome.%s not available (station offline?)", service
            )
            return
        try:
            async with asyncio.timeout(self.feedback_timeout):
                await self.hass.services.async_call(
                    "esphome", service, {"result": result}, blocking=True
                )
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.warning("Feedback esphome.%s failed: %s", service, err)

    async def _async_notify_unknown(self, uid: str, station: str) -> None:
        target = self.options.get(CONF_NOTIFY_TARGET)
        if not target:
            return
        url = f"/{PANEL_URL}?page=enroll&uid={uid}"
        link_url = f"/{PANEL_URL}?page=link&uid={uid}"
        untagged = any(not g.tagged for g in self.catalog.garments.values())
        title = "New laundry tag"
        message = f"Unknown tag {uid} tapped at {station.replace('_', ' ')}."
        actions = [{"action": "URI", "title": "Enroll", "uri": url}]
        if untagged:
            message += (
                " Add it as a new garment, or link it to a garment without a tag."
            )
            actions.append(
                {
                    "action": "URI",
                    "title": "Link to an untagged garment…",
                    "uri": link_url,
                }
            )
        try:
            if str(target).startswith("notify.") and self.hass.states.get(target):
                await self.hass.services.async_call(
                    "notify",
                    "send_message",
                    {"entity_id": target, "title": title, "message": message},
                    blocking=True,
                )
                return
            service = str(target).removeprefix("notify.")
            if not self.hass.services.has_service("notify", service):
                _LOGGER.warning("Notify target %s not found", target)
                return
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "title": title,
                    "message": message,
                    "data": {
                        "url": url,
                        "clickAction": url,
                        "actions": actions,
                    },
                },
                blocking=True,
            )
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("Enrollment notification failed: %s", err)

    # --- catalog operations (shared by services and websocket) -----------

    def _validate_name(self, name: object) -> str:
        if not isinstance(name, str) or not name.strip():
            raise validation_error("invalid_name", "Name is required")
        name = name.strip()
        if len(name) > MAX_NAME_LEN:
            raise validation_error(
                "invalid_name", f"Name longer than {MAX_NAME_LEN} characters"
            )
        return name

    def validate_owner(self, owner: object) -> str | None:
        """Return a valid person entity id (or None) or raise."""
        if owner in (None, ""):
            return None
        if not isinstance(owner, str) or not owner.startswith("person."):
            raise validation_error(
                "invalid_owner", f"Owner must be a person entity: {owner}", owner=owner
            )
        if self.hass.states.get(owner) is None:
            raise validation_error(
                "invalid_owner", f"Unknown person: {owner}", owner=owner
            )
        return owner

    def _validate_notes(self, notes: object) -> str:
        if notes is None:
            return ""
        if not isinstance(notes, str) or len(notes) > MAX_NOTES_LEN:
            raise validation_error(
                "invalid_notes", f"Notes must be text up to {MAX_NOTES_LEN} characters"
            )
        return notes

    def _uid(self, raw: object) -> str:
        try:
            return normalize_uid(raw)
        except InvalidUid as err:
            raise validation_error("invalid_uid", str(err), uid=raw) from err

    def resolve_garment(self, ref: str) -> Garment:
        """Find a garment by id or by one of its tag UIDs."""
        garment = self.catalog.garments.get(ref)
        if garment is None:
            try:
                garment = self.catalog.by_uid(normalize_uid(ref))
            except InvalidUid:
                garment = None
        if garment is None:
            raise validation_error(
                "unknown_garment", f"Unknown garment: {ref}", garment=ref
            )
        return garment

    def _catalog_error(self, err: CatalogError) -> ServiceValidationError:
        messages = {
            "duplicate_uid": f"Tag {err} already belongs to another garment",
            "unknown_garment": f"Unknown garment: {err}",
            "last_tag": f"Tag {err} is the garment's only tag",
        }
        key = err.translation_key
        return validation_error(key, messages.get(key, str(err)), uid=err, garment=err)

    def _location_state(self, location: str | None) -> tuple[str, str | None]:
        if location in (None, ""):
            return STATE_NEW, None
        if location in CLOSET_STATIONS:
            return STATE_PUT_AWAY, location
        if location in (STATE_IN_WASH, STATE_DRYING):
            return location, STATION_LAUNDRY
        raise validation_error(
            "invalid_location", f"Invalid location: {location}", location=location
        )

    def enroll(
        self,
        *,
        uid: str | None,
        name: str,
        wash_bin: str | None = None,
        dry_type: str | None = None,
        owner: str | None = None,
        notes: str | None = None,
        location: str | None = None,
    ) -> Garment:
        """Create a garment (``uid`` None or empty: untagged, v0.2.8)."""
        norm_uid = self._uid(uid) if uid else None
        if norm_uid is None and location not in (None, ""):
            raise validation_error(
                "untagged_location",
                "A garment without a tag has no known location; leave it empty",
            )
        clean_name = self._validate_name(name)
        bins = self.zones_of(SUBENTRY_WASH_BIN)
        dries = self.zones_of(SUBENTRY_DRY_TYPE)
        if not bins or not dries:
            raise validation_error(
                "no_zones", "Add at least one wash bin and one dry type first"
            )
        bin_zone = (
            self.resolve_zone(wash_bin, SUBENTRY_WASH_BIN) if wash_bin else bins[0]
        )
        dry_zone = (
            self.resolve_zone(dry_type, SUBENTRY_DRY_TYPE) if dry_type else dries[0]
        )
        state, loc = self._location_state(location)
        try:
            garment = self.catalog.add(
                uid=norm_uid,
                name=clean_name,
                wash_bin=bin_zone.id,
                dry_type=dry_zone.id,
                owner=self.validate_owner(owner),
                notes=self._validate_notes(notes),
                state=state,
                location=loc,
            )
        except CatalogError as err:
            raise self._catalog_error(err) from err
        self.notify_changed()
        return garment

    def update(self, ref: str, changes: dict[str, Any]) -> Garment:
        """Update a garment; all fields validated before anything changes."""
        garment = self.resolve_garment(ref)
        updates: dict[str, Any] = {}
        if "name" in changes:
            updates["name"] = self._validate_name(changes["name"])
        if "owner" in changes:
            updates["owner"] = self.validate_owner(changes["owner"])
        if "notes" in changes:
            updates["notes"] = self._validate_notes(changes["notes"])
        if changes.get("wash_bin"):
            updates["wash_bin"] = self.resolve_zone(
                changes["wash_bin"], SUBENTRY_WASH_BIN
            ).id
        if changes.get("dry_type"):
            updates["dry_type"] = self.resolve_zone(
                changes["dry_type"], SUBENTRY_DRY_TYPE
            ).id
        add_uid = self._uid(changes["add_uid"]) if changes.get("add_uid") else None
        remove_uid = (
            self._uid(changes["remove_uid"]) if changes.get("remove_uid") else None
        )
        if add_uid is not None:
            owner_garment = self.catalog.by_uid(add_uid)
            if owner_garment is not None and owner_garment.id != garment.id:
                raise validation_error(
                    "duplicate_uid",
                    f"Tag {add_uid} already belongs to {owner_garment.name}",
                    uid=add_uid,
                )
        if remove_uid is not None and remove_uid in garment.uids:
            remaining = [u for u in garment.uids if u != remove_uid]
            if add_uid is not None and add_uid not in remaining:
                remaining.append(add_uid)
            if not remaining:
                raise validation_error(
                    "last_tag",
                    f"Tag {remove_uid} is the garment's only tag",
                    uid=remove_uid,
                )
        try:
            if add_uid is not None:
                self.catalog.add_uid(garment.id, add_uid)
            if remove_uid is not None:
                self.catalog.remove_uid(garment.id, remove_uid)
        except CatalogError as err:  # pragma: no cover - prevalidated above
            raise self._catalog_error(err) from err
        for key, value in updates.items():
            setattr(garment, key, value)
        self.catalog.schedule_save()
        self.notify_changed()
        return garment

    def set_location(self, ref: str, location: str) -> Garment:
        """Manually set where a garment is (counts as seen)."""
        garment = self.resolve_garment(ref)
        state, loc = self._location_state(location)
        if state == STATE_NEW:
            raise validation_error(
                "invalid_location", "A location is required", location=location
            )
        garment.state = state
        garment.location = loc
        garment.last_seen = dt_util.utcnow()
        garment.last_seen_station = None
        self.catalog.schedule_save()
        self.notify_changed()
        return garment

    async def async_remove(self, ref: str) -> Garment:
        """Delete a garment (and its photos)."""
        garment = self.resolve_garment(ref)
        self.catalog.remove(garment.id)
        await self.wardrobe.async_delete_all(garment)
        self.notify_changed()
        return garment

    async def async_set_photo(self, ref: str, content_type: str, data: str) -> Garment:
        """Legacy (v0.1) base64 upload: goes through the v0.2 photo pipeline."""
        garment = self.resolve_garment(ref)
        try:
            raw, _ext = decode_photo(content_type, data)
        except PhotoError as err:
            raise validation_error(err.key, str(err)) from err
        await self.wardrobe.async_add_photo(garment, raw)  # replaces when off
        return garment

    async def async_delete_photo(self, ref: str) -> Garment:
        """Legacy (v0.1): remove the cover photo."""
        garment = self.resolve_garment(ref)
        if garment.photos:
            await self.wardrobe.async_delete_photo(garment, garment.photos[0])
        return garment

    def reassign(
        self,
        field: str,
        to: str,
        garment_refs: list[str] | None = None,
        from_zone: str | None = None,
    ) -> int:
        """Bulk move garments to another wash bin / dry type."""
        if field not in (SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE):
            raise validation_error("invalid_field", f"Invalid field: {field}")
        target = self.resolve_zone(to, field)
        if garment_refs:
            garments = [self.resolve_garment(ref) for ref in garment_refs]
        elif from_zone:
            source = self.resolve_zone(from_zone, field)
            garments = [
                g
                for g in self.catalog.garments.values()
                if getattr(g, field) == source.id
            ]
        else:
            raise validation_error(
                "reassign_selection", "Select garments or a source zone"
            )
        for garment in garments:
            setattr(garment, field, target.id)
        self.catalog.schedule_save()
        self.notify_changed()
        for zone_id in self.zones:
            if self.catalog.usage(zone_id) == 0:
                ir.async_delete_issue(self.hass, DOMAIN, f"zone_restored_{zone_id}")
        return len(garments)

    def check_zone_removable(self, zone_id: str) -> Zone:
        """Raise if a zone is still used by garments."""
        zone = self.resolve_zone(zone_id)
        count = self.catalog.usage(zone.id)
        if count:
            raise validation_error(
                "zone_in_use",
                f"{zone.name} is used by {count} garment(s); reassign them first "
                "(Laundry Hub panel → Garments → Bulk reassign)",
                name=zone.name,
                count=count,
            )
        return zone

    def remove_zone(self, zone_id: str) -> None:
        """Remove a wash bin / dry type subentry if unused."""
        zone = self.check_zone_removable(zone_id)
        ir.async_delete_issue(self.hass, DOMAIN, f"zone_restored_{zone.id}")
        self.hass.config_entries.async_remove_subentry(self.entry, zone.id)

    async def async_flash_zone(self, zone_ref: str) -> bool:
        """Flash a zone's light (test button)."""
        zone = self.resolve_zone(zone_ref)
        if zone.light_entity is None:
            raise validation_error(
                "no_light", f"{zone.name} has no light configured", name=zone.name
            )
        return await self.flash.async_flash(zone, self.flash_duration)

    def dismiss_unknown(self, uid: str) -> None:
        """Forget an unknown tag."""
        self.catalog.dismiss_unknown(self._uid(uid))
        self.notify_changed()

    # --- derived views ---------------------------------------------------

    def is_missing(self, garment: Garment, now: datetime | None = None) -> bool:
        """True when a tagged garment has not been seen for missing_after.

        An untagged garment is never "missing": no station can see it.
        """
        if not garment.tagged:
            return False
        return is_missing(
            garment.last_seen,
            garment.created,
            now or dt_util.utcnow(),
            self.missing_after,
        )

    def missing(self) -> list[Garment]:
        """Garments not seen for missing_after."""
        now = dt_util.utcnow()
        return [g for g in self.catalog.garments.values() if self.is_missing(g, now)]

    def count(self, state: str, location: str | None = None) -> int:
        """Count garments in a state (optionally at a location)."""
        return sum(
            1
            for g in self.catalog.garments.values()
            if g.tagged  # station counts ignore untagged garments (v0.2.8)
            and g.state == state
            and (location is None or g.location == location)
        )

    def per_zone(self, state: str, zone_type: str) -> dict[str, int]:
        """Count garments in `state` per zone of `zone_type` (keyed by zone id)."""
        field = (
            SUBENTRY_WASH_BIN if zone_type == SUBENTRY_WASH_BIN else SUBENTRY_DRY_TYPE
        )
        counts = {z.id: 0 for z in self.zones_of(zone_type)}
        for garment in self.catalog.garments.values():
            if garment.tagged and garment.state == state:
                zone_id = getattr(garment, field)
                if zone_id in counts:
                    counts[zone_id] += 1
        return counts

    def problems(self) -> dict[str, list[str]]:
        """Invariant violations by garment id (should always be empty)."""
        zones = self.zones
        zone_ids = set(zones)
        found: dict[str, list[str]] = {}
        for gid, garment in self.catalog.garments.items():
            issues = garment_problems(garment, zone_ids)
            bin_zone = zones.get(garment.wash_bin)
            dry_zone = zones.get(garment.dry_type)
            if bin_zone is not None and bin_zone.type != SUBENTRY_WASH_BIN:
                issues.append("wash_bin is not a wash bin")
            if dry_zone is not None and dry_zone.type != SUBENTRY_DRY_TYPE:
                issues.append("dry_type is not a dry type")
            if issues:
                found[gid] = issues
        return found

    def garment_view(
        self, garment: Garment, now: datetime | None = None, *, synced: bool = False
    ) -> dict[str, Any]:
        """Serializable garment for the panel / responses.

        Wardrobe fields appear only when the wardrobe is on; v0.3 reserved
        fields are never sent. ``rev``/``updated_at`` are current: revs are
        synced first unless the caller already did (the snapshot).
        """
        if not synced:
            self.revs.sync()
        stored = garment.to_dict()
        data = {key: stored[key] for key in BASE_VIEW_KEYS}
        data["photo"] = garment.photo
        data["missing"] = self.is_missing(garment, now)
        # v0.2.8: tags are optional; untagged states are set by hand.
        data["tagged"] = garment.tagged
        data["state_source"] = garment.state_source
        data["state_updated"] = (
            garment.state_updated.isoformat() if garment.state_updated else None
        )
        data["state_confidence"] = state_confidence(garment, now or dt_util.utcnow())
        # v0.2.8: wears ("Wore it"; station wears come in v0.3).
        data["wear_count"] = len(wear_entries(garment))
        data["last_worn"] = last_worn(garment)
        data["worn_since"] = (
            garment.out_since.isoformat() if garment.out_since else None
        )
        data.update(self.wardrobe.view(garment))
        return self.revs.stamp("garment", garment.id, data)

    def zone_view(self, zone: Zone) -> dict[str, Any]:
        """Serializable zone (with rev once the tracker knows it)."""
        view = {
            "id": zone.id,
            "type": zone.type,
            "name": zone.name,
            "color": list(zone.color),
            "light_entity": zone.light_entity,
            "order": zone.order,
            "usage": self.catalog.usage(zone.id),
        }
        meta = self.revs.meta("zone", zone.id)
        if meta is not None:
            view.update(meta)
        return view

    def snapshot(self) -> dict[str, Any]:
        """Everything the panel needs (and the app's full read)."""
        now = dt_util.utcnow()
        self.revs.sync(now)
        return {
            "rev": self.revs.rev,
            "link": self.link_status(),
            "garments": [
                self.garment_view(g, now, synced=True)
                for g in self.catalog.garments.values()
            ],
            "wash_bins": [self.zone_view(z) for z in self.zones_of(SUBENTRY_WASH_BIN)],
            "dry_types": [self.zone_view(z) for z in self.zones_of(SUBENTRY_DRY_TYPE)],
            "stations": list(STATIONS),
            "last_unknown": self.catalog.last_unknown,
            "unknown_tags": sorted(
                (
                    self.revs.stamp("unknown_tag", uid, dict(tag))
                    for uid, tag in self.catalog.unknown_tags.items()
                ),
                key=lambda t: t.get("last_seen", ""),
                reverse=True,
            ),
            "missing_after_days": self.missing_after.days,
            "entry_id": self.entry.entry_id,
            "version": VERSION,
            "station_health": self.station_health(),
            "owners": {
                owner: self.catalog.owner_settings(owner)
                for owner in sorted(
                    {g.owner for g in self.catalog.garments.values() if g.owner}
                    | set(self.catalog.owners)
                )
            },
            "wardrobe": {
                "enabled": self.wardrobe.enabled,
                "ai_entity": self.wardrobe.ai_entity if self.wardrobe.enabled else None,
                "currency": self.hass.config.currency,
                "photo_limit": self.wardrobe.photo_limit(),
            },
            "follow_ha_theme": bool(self.options.get(CONF_FOLLOW_HA_THEME, False)),
            "taxonomy": taxonomy.vocabulary() if self.wardrobe.enabled else None,
            "purchases": (
                {
                    **self.purchases.status(),
                    "cards": [
                        self.purchases.view(c)
                        for c in rank_cards(self.purchases.unmatched(), set(), None)
                    ],
                    "archived": [
                        self.purchases.view(c) for c in self.purchases.archived()
                    ],
                    "uniform_offers": self.purchases.uniform_offers(),
                }
                if self.wardrobe.enabled
                else None
            ),
        }

    def station_health(self) -> list[dict[str, Any]]:
        """Per station: configured device, last tap, online (None = unknown)."""
        devices = self.options.get(CONF_STATION_DEVICES) or {}
        registry = er.async_get(self.hass)
        out = []
        for station in STATIONS:
            device_id = devices.get(station)
            online: bool | None = None
            if device_id:
                states = [
                    self.hass.states.get(e.entity_id)
                    for e in er.async_entries_for_device(registry, device_id)
                ]
                known = [st for st in states if st is not None]
                if known:
                    online = any(st.state != STATE_UNAVAILABLE for st in known)
            out.append(
                {
                    "station": station,
                    "configured": bool(device_id),
                    "online": online,
                    "last_tap": (self.catalog.stations.get(station) or {}).get(
                        "last_tap"
                    ),
                }
            )
        return out

    # --- subentry removal guard -----------------------------------------

    def restore_removed_zones(self) -> list[str]:
        """Re-add in-use zones removed through the generic subentry UI.

        Home Assistant's subentry delete button cannot be vetoed, so an in-use
        zone that disappears is restored with the same id and a repair issue is
        raised explaining how to reassign garments first.
        """
        restored: list[str] = []
        for sid, sub in list(self.zone_snapshot.items()):
            if sid in self.entry.subentries:
                continue
            count = self.catalog.usage(sid)
            if not count:
                continue
            _LOGGER.warning(
                "Restored %s %s: still used by %s garment(s)",
                sub.subentry_type,
                sub.title,
                count,
            )
            self.hass.config_entries.async_add_subentry(self.entry, sub)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"zone_restored_{sid}",
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="zone_restored",
                translation_placeholders={"name": sub.title, "count": str(count)},
            )
            restored.append(sid)
        self.zone_snapshot = dict(self.entry.subentries)
        return restored
