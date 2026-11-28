"""Who may do what (v0.2.3): one permission helper for every command and view.

Roles:

* **admin**: an HA administrator. Full access, exactly as before v0.2.3.
* **member**: any other HA user, only when the "App access" option is
  ``household``. A member is mapped to one Laundry Hub owner (a ``person``
  entity): by the option's people mapping, else by the person entity linked
  to their HA user. A member may read their own owner's garments (and the
  zones), phone-tap, and edit their own garments' name, notes, photos, size
  and wear_for. From v0.2.6 a member also sees the purchase cards assigned to
  their owner, and may match them to their own garments, dismiss them, or
  mark them Not clothing. Nothing else.

Every websocket command declares a ``Policy``; ``check()`` enforces it. HTTP
photo views use ``can_see_garment`` / ``can_edit_garment``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import CONF_APP_ACCESS, CONF_USER_OWNERS

if TYPE_CHECKING:
    from homeassistant.auth.models import User

    from .hub import LaundryHub
    from .models import Garment

ACCESS_ADMINS = "admins"
ACCESS_HOUSEHOLD = "household"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

# Fields a member may change on their own garments.
MEMBER_UPDATE_FIELDS = frozenset({"name", "notes"})
MEMBER_WARDROBE_FIELDS = frozenset({"size", "wear_for"})
# Fields a member may send when enrolling a garment for themselves (v0.2.4).
MEMBER_CREATE_FIELDS = frozenset(
    {"uid", "name", "wash_bin", "dry_type", "notes", "owner"}
)
# A member may enrol only the unknown tag they tapped with their phone, recently.
ENROL_WINDOW = timedelta(minutes=30)
_ENVELOPE = frozenset({"id", "type", "if_rev", "if_revs"})


class Policy(StrEnum):
    """What a command needs."""

    ADMIN = "admin"  # admins only
    READ = "read"  # anyone allowed in; results filtered to the caller's owner
    OWN_GARMENT = "own_garment"  # admins, or a member acting on their own garment
    TAP = "tap"  # admins, or a member (own garments for actions)
    ENROL = "enrol"  # admins, or a member creating a garment for themselves
    PURCHASE = "purchase"  # admins, or a member acting on a card assigned to them


class Forbidden(HomeAssistantError):
    """The caller may not do this (websocket error code ``unauthorized``)."""


@dataclass(frozen=True, slots=True)
class Caller:
    """The user behind a request."""

    user_id: str | None
    is_admin: bool
    owner: str | None
    household: bool

    @property
    def role(self) -> str:
        """``admin`` or ``member``."""
        return ROLE_ADMIN if self.is_admin else ROLE_MEMBER

    @property
    def allowed(self) -> bool:
        """May use Laundry Hub at all."""
        return self.is_admin or self.household


def app_access(hub: LaundryHub) -> str:
    """The App access option (admins only by default)."""
    value = hub.options.get(CONF_APP_ACCESS) or ACCESS_ADMINS
    return value if value in (ACCESS_ADMINS, ACCESS_HOUSEHOLD) else ACCESS_ADMINS


def owner_for(hub: LaundryHub, user_id: str | None) -> str | None:
    """The owner (person entity) mapped to an HA user, or None.

    The options mapping wins (an empty value means "no owner"); otherwise the
    person entity whose ``user_id`` attribute is this user.
    """
    if not user_id:
        return None
    mapping: dict[str, Any] = hub.options.get(CONF_USER_OWNERS) or {}
    if user_id in mapping:
        return str(mapping[user_id]) or None
    for state in hub.hass.states.async_all("person"):
        if state.attributes.get("user_id") == user_id:
            return state.entity_id
    return None


def caller_for(hub: LaundryHub, user: User | None) -> Caller:
    """Build the Caller for an HA user (None = not authenticated)."""
    if user is None:
        return Caller(None, False, None, False)
    return Caller(
        user_id=user.id,
        is_admin=bool(user.is_admin),
        owner=owner_for(hub, user.id),
        household=app_access(hub) == ACCESS_HOUSEHOLD,
    )


def can_see_garment(caller: Caller, garment: Garment) -> bool:
    """Admins see everything; members only their own owner's garments."""
    if caller.is_admin:
        return True
    return (
        caller.household and caller.owner is not None and garment.owner == caller.owner
    )


def can_edit_garment(caller: Caller, garment: Garment) -> bool:
    """Same rule as seeing: a member edits only their own garments."""
    return can_see_garment(caller, garment)


def can_see_card(caller: Caller, hub: LaundryHub, card: dict[str, Any]) -> bool:
    """Admins see every card; a member sees cards assigned to their owner.

    A card already matched to one of the member's garments stays visible too.
    """
    if caller.is_admin:
        return True
    if not caller.household or caller.owner is None:
        return False
    if card.get("owner") == caller.owner:
        return True
    for match in card.get("matches") or [{"garment_id": card.get("garment_id")}]:
        garment = hub.catalog.garments.get(str(match.get("garment_id")))
        if garment is not None and garment.owner == caller.owner:
            return True
    return False


def member_unknown_tags(
    hub: LaundryHub, caller: Caller, now: datetime | None = None
) -> list[dict[str, Any]]:
    """The unknown tag a member just tapped with their own phone (0 or 1).

    Members never see the household's unknown-tag list; they see only the
    tag they tapped last, within ``ENROL_WINDOW``, so they can enrol it.
    """
    if caller.user_id is None:
        return []
    now = now or dt_util.utcnow()
    mine = []
    for tag in hub.catalog.unknown_tags.values():
        if tag.get("tapped_by") != caller.user_id:
            continue
        seen = dt_util.parse_datetime(str(tag.get("last_seen") or ""))
        if seen is not None and now - seen <= ENROL_WINDOW:
            mine.append(tag)
    mine.sort(key=lambda t: str(t.get("last_seen")), reverse=True)
    return mine[:1]


def _invalid(key: str, message: str) -> HomeAssistantError:
    from .hub import validation_error  # noqa: PLC0415 - avoid import cycle

    return validation_error(key, message)


def _deny(message: str = "You don't have access to this") -> Forbidden:
    return Forbidden(message)


def check(caller: Caller, policy: Policy, hub: LaundryHub, msg: dict[str, Any]) -> None:
    """Raise Forbidden unless the caller may run this command."""
    if caller.is_admin:
        return
    if not caller.household:
        raise _deny("Laundry Hub is limited to administrators")
    if policy is Policy.ADMIN:
        raise _deny("Only an administrator can do this")
    if policy is Policy.READ or policy is Policy.TAP:
        return
    if policy is Policy.ENROL:
        _check_enrol(caller, hub, msg)
        return
    if policy is Policy.PURCHASE:
        _check_purchase(caller, hub, msg)
        return
    # OWN_GARMENT: the garment must be the member's, and only member fields.
    garment = hub.resolve_garment(str(msg.get("garment_id", "")))
    if not can_edit_garment(caller, garment):
        raise _deny("You can only change your own garments")
    command = str(msg.get("type", "")).split("/", 1)[-1]
    if command == "garment/update":
        extra = set(msg) - MEMBER_UPDATE_FIELDS - {"id", "type", "garment_id", "if_rev"}
        if extra:
            raise _deny(f"Only an administrator can change {', '.join(sorted(extra))}")
    elif command == "garment/wardrobe":
        extra = (
            set(msg) - MEMBER_WARDROBE_FIELDS - {"id", "type", "garment_id", "if_rev"}
        )
        if extra:
            raise _deny(f"Only an administrator can change {', '.join(sorted(extra))}")


def _check_enrol(caller: Caller, hub: LaundryHub, msg: dict[str, Any]) -> None:
    """A member creating a garment: for their own owner, from their own tap."""
    if caller.owner is None:
        raise _invalid(
            "no_owner_mapping",
            "Your Home Assistant user isn't linked to a person in Laundry Hub; "
            "ask an administrator to set it under App access",
        )
    owner = msg.get("owner")
    if owner not in (None, "", caller.owner):
        raise _invalid("owner_not_allowed", "You can only add garments for yourself")
    extra = set(msg) - MEMBER_CREATE_FIELDS - _ENVELOPE
    if extra:
        raise _deny(f"Only an administrator can set {', '.join(sorted(extra))}")
    uid = str(msg.get("uid", "")).replace(":", "").replace(" ", "").upper()
    if uid not in {t.get("uid") for t in member_unknown_tags(hub, caller)}:
        raise _invalid(
            "tag_not_yours",
            "Tap the new tag with your phone first, then add it",
        )


def _check_purchase(caller: Caller, hub: LaundryHub, msg: dict[str, Any]) -> None:
    """A member acting on a purchase card: assigned to them, with their garment."""
    card = hub.catalog.purchases.get(str(msg.get("card_id", "")))
    if card is None:
        return  # the command answers unknown_purchase
    if caller.owner is None or card.get("owner") != caller.owner:
        raise _deny("You can only use purchases assigned to you")
    if "garment_id" in msg:
        garment = hub.resolve_garment(str(msg["garment_id"]))
        if not can_edit_garment(caller, garment):
            raise _deny("You can only match purchases to your own garments")
