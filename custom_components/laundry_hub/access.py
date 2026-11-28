"""Who may do what (v0.2.3): one permission helper for every command and view.

Roles:

* **admin**: an HA administrator. Full access, exactly as before v0.2.3.
* **member**: any other HA user, only when the "App access" option is
  ``household``. A member is mapped to one Laundry Hub owner (a ``person``
  entity): by the option's people mapping, else by the person entity linked
  to their HA user. A member may read their own owner's garments (and the
  zones), phone-tap, and edit their own garments' name, notes, photos, size,
  description fields and wear_for. Nothing else.

Every websocket command declares a ``Policy``; ``check()`` enforces it. HTTP
photo views use ``can_see_garment`` / ``can_edit_garment``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError

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


class Policy(StrEnum):
    """What a command needs."""

    ADMIN = "admin"  # admins only
    READ = "read"  # anyone allowed in; results filtered to the caller's owner
    OWN_GARMENT = "own_garment"  # admins, or a member acting on their own garment
    TAP = "tap"  # admins, or a member (own garments for actions)


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
