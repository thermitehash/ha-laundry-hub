"""Purchase inbox (v0.2.1): order emails from opted-in stores become cards.

Flow of one check (one at a time, backoff after errors):

1. Open the folder read-only (``EXAMINE``) and search for messages from the
   opted-in sender domains newer than the last UID seen (first run: the
   backfill window). A UIDVALIDITY change re-searches the backfill window;
   Message-ID de-duplication keeps that from creating duplicates.
2. Fetch headers only. A message whose sender domain isn't opted in is
   skipped without fetching its body. Already-seen Message-IDs are skipped.
3. Fetch only the text parts (500 KB cap), convert to text, ask AI Task for
   the order, and validate it (links must appear verbatim in the email).
4. A known order (same store + order reference) is *updated* (shipping and
   delivery emails); otherwise each item becomes a card. Card photos go
   through the v0.2 image pipeline (SSRF guard, DNS pinning, size caps).

Only card fields, the sender domain and the Message-ID are stored; never the
email body, subject, addresses or the raw AI answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from . import extract, grouping, taxonomy as tx
from .const import (
    CONF_PURCHASE_ARCHIVE_DAYS,
    CONF_PURCHASE_BACKFILL,
    CONF_PURCHASE_CLOTHING_ONLY,
    CONF_PURCHASE_CUSTOM,
    CONF_PURCHASE_EMAIL,
    CONF_PURCHASE_INTERVAL,
    CONF_PURCHASE_SHOPIFY,
    CONF_PURCHASE_STORES,
    DEFAULT_PURCHASE_ARCHIVE_DAYS,
    DEFAULT_PURCHASE_BACKFILL,
    DEFAULT_PURCHASE_INTERVAL,
    DOMAIN,
    ISSUE_PURCHASE_AUTH,
    MIN_PURCHASE_INTERVAL,
)
from .importer import ImportFailed, fetch_image
from .mailbox import (
    Account,
    AuthFailed,
    Header,
    MailboxError,
    MailboxSession,
    account_from_data,
    open_mailbox,
)
from .photos import (
    PhotoError,
    copy_photo,
    delete_photo,
    photo_dir,
    process_image,
    write_processed,
)
from .stores import (
    BY_KEY,
    ORDER_KEYWORDS,
    SHOPIFY_DOMAIN,
    OptedIn,
    domain_matches,
    is_uniform_supplier,
    shop_id,
)

if TYPE_CHECKING:
    from .hub import LaundryHub
    from .models import Garment

_LOGGER = logging.getLogger(__name__)

STATUSES = ("new", "matched", "dismissed", "archived")
MAX_ATTEMPTS = 3  # failed reads of one email before it's marked unreadable
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MAX_SEEN_IDS = 3000
MAX_MESSAGES_PER_RUN = 200
MAX_BACKOFF = timedelta(hours=6)
FIRST_CHECK_DELAY = 60  # seconds after setup
CARD_FIELDS = (
    "id", "status", "store", "store_key", "sender_domain", "order_ref", "order_date",
    "currency", "name", "brand", "color", "size", "cut", "quantity", "unit_price",
    "product_url", "image_url", "photos", "garment_id", "source_message_id", "created",
    "updated",
    "status_changed",
)  # fmt: skip
MERGE_FIELDS = ("brand", "color", "size", "cut", "unit_price", "product_url")
HISTORY_FIELDS = (
    "id", "order_ref", "order_date", "store", "unit_price", "currency", "quantity",
)  # fmt: skip
GROUPABLE = ("new", "dismissed", "archived")  # grouping never touches matched cards
CLEANUP_STATUSES = ("new", "dismissed")
SUGGEST_AFTER = 2  # similar cards assigned to one owner before a suggestion
CLEANUP_VERSION = 1  # the one-time v0.2.6 cleanup of cards from older versions
CLEANUP_DELAY = 120  # seconds after setup
_COLOR_FAMILIES = {
    "dark": {"black", "navy", "charcoal", "grey", "gray", "brown", "dark", "olive"},
    "black": {"black", "charcoal"},
    "light": {"white", "cream", "ivory", "beige", "light", "pastel", "khaki"},
    "white": {"white", "cream", "ivory"},
    "red": {"red", "pink", "burgundy", "maroon", "wine", "coral"},
    "color": {"blue", "green", "yellow", "orange", "purple", "teal", "multi"},
}
_WORD = re.compile(r"[a-z]+")


def _norm(name: str | None) -> str:
    return " ".join(_WORD.findall((name or "").lower()))


def ensure_history(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The card's purchase history, created from its fields for older cards.

    Cards from before v0.2.6 are one purchase each: their units are matched
    (status matched), dismissed (dismissed or archived) or waiting (new).
    """
    history = card.get("history")
    if isinstance(history, list) and history:
        return history
    quantity = int(card.get("quantity") or 1)
    card["history"] = [
        {
            "id": ulid_now(),
            "order_ref": card.get("order_ref"),
            "order_date": card.get("order_date"),
            "store": card.get("store"),
            "unit_price": card.get("unit_price"),
            "currency": card.get("currency"),
            "quantity": quantity,
            "kinds": [],
            "message_ids": [m for m in [card.get("source_message_id")] if m],
        }
    ]
    card.setdefault("quantity_locked", False)
    card.setdefault("owner", None)
    if "matches" not in card:
        gid = card.get("garment_id")
        matched = card.get("status") == "matched" and gid
        card["matches"] = [{"garment_id": gid, "units": quantity}] if matched else []
    if "dismissed_units" not in card:
        gone = card.get("status") in ("dismissed", "archived")
        card["dismissed_units"] = quantity if gone else 0
    created: list[dict[str, Any]] = card["history"]
    return created


def _matched_units(card: dict[str, Any]) -> int:
    return sum(int(m.get("units") or 0) for m in card.get("matches") or [])


def _remaining(card: dict[str, Any]) -> int:
    """Units neither matched to a garment nor dismissed."""
    return (
        int(card.get("quantity") or 1)
        - _matched_units(card)
        - int(card.get("dismissed_units") or 0)
    )


def _completeness(card: dict[str, Any]) -> int:
    """How many detail fields a card has (the fuller card is kept on merge)."""
    keys = ("brand", "color", "size", "cut", "unit_price", "product_url", "order_ref")
    return sum(1 for k in keys if card.get(k) not in (None, "")) + (
        2 if card.get("photos") else 0
    )


def _duplicate_of(kept: dict[str, Any], card: dict[str, Any]) -> bool:
    """The card is the same item as an earlier card of the same store."""
    if kept.get("store_key") != card.get("store_key"):
        return False
    if grouping.same_item(kept, card):
        return True
    return grouping.refs_equal(kept.get("order_ref"), card.get("order_ref")) and (
        _contains(kept.get("name"), card.get("name"))
    )


def _item_fields(item: extract.Item) -> dict[str, Any]:
    return {
        "name": item.name,
        "color": item.color,
        "size": item.size,
        "cut": item.cut,
        "unit_price": item.unit_price,
    }


def _contains(a: Any, b: Any) -> bool:
    left, right = _norm(str(a or "")), _norm(str(b or ""))
    return bool(left and right and (left in right or right in left))


def _fill(card: dict[str, Any], item: extract.Item) -> bool:
    """Fill empty card fields from an item; never overwrite with emptier data."""
    changed = False
    for key in MERGE_FIELDS:
        value = getattr(item, key)
        if value not in (None, "") and card.get(key) in (None, ""):
            card[key] = value
            changed = True
    return changed


def _purchase(
    store: Any,
    item: extract.Item,
    *,
    order: extract.Order,
    header: Header,
    kind: str,
    order_date: str,
) -> dict[str, Any]:
    """One history line: an order of this item."""
    return {
        "id": ulid_now(),
        "order_ref": order.order_ref,
        "order_date": order_date,
        "store": store,
        "unit_price": item.unit_price,
        "currency": order.currency,
        "quantity": item.quantity,
        "kinds": [kind],
        "message_ids": [header.message_id] if header.message_id else [],
    }


_SIZE_LINE_CUTS = ("tall", "petite", "plus", "maternity")


def cut_size_line(cut: Any) -> str | None:
    """Tall, petite, plus and maternity are size lines, not fits (size.cut)."""
    words = set(grouping.tokens(cut))
    return next((c for c in _SIZE_LINE_CUTS if c in words), None)


def cut_fit(cut: Any) -> str | None:
    """A printed cut mapped to a fit option (slim, regular, relaxed…), else None."""
    if not cut or cut_size_line(cut):
        return None
    for word in [str(cut), *grouping.tokens(cut)]:
        fit = tx.coerce(word, tx.FITS)
        if fit and fit != "n/a":
            return fit
    return None


def _err(key: str, message: str) -> HomeAssistantError:
    from .hub import validation_error  # noqa: PLC0415 - avoid import cycle

    return validation_error(key, message)


def redact(text: str, limit: int = 300) -> str:
    """An error message safe for the log: no addresses, short."""
    return _ADDRESS.sub("<address>", " ".join(str(text).split()))[:limit]


def subject_matches(subject: str, keywords: list[str]) -> bool:
    """Subject contains one of the order words (case-insensitive)."""
    text = subject.lower()
    return any(word in text for word in keywords)


def color_score(card: dict[str, Any], bin_name: str | None) -> int:
    """1 when the card's colour words fit the wash bin's name, else 0."""
    if not bin_name:
        return 0
    words = set(
        _WORD.findall(f"{card.get('color') or ''} {card.get('name') or ''}".lower())
    )
    bin_words = set(_WORD.findall(bin_name.lower()))
    for family, colours in _COLOR_FAMILIES.items():
        if (family in bin_words or family + "s" in bin_words) and words & colours:
            return 1
    return 0


def rank_cards(
    cards: list[dict[str, Any]],
    owner_stores: set[str],
    bin_name: str | None,
) -> list[dict[str, Any]]:
    """Most recent order day first, then the owner's usual stores, then colour fit."""

    def key(card: dict[str, Any]) -> tuple[int, int, int, str]:
        day = card.get("order_date") or str(card.get("created") or "")[:10]
        try:
            ordinal = date.fromisoformat(day).toordinal()
        except ValueError:
            ordinal = 0
        usual = 1 if (card.get("store") or "") in owner_stores else 0
        return (-ordinal, -usual, -color_score(card, bin_name), str(card.get("id")))

    return sorted(cards, key=key)


class PurchaseInbox:
    """Purchase emails → cards, bound to a hub."""

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        self.hub = hub
        self.hass: HomeAssistant = hub.hass
        self._lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._failures = 0
        self._next_allowed: datetime | None = None
        self._schema_logged = False
        # Patched in tests with a fake IMAP server.
        self.client_factory: Callable[[Account], Any] | None = None

    # --- settings --------------------------------------------------------

    @property
    def account(self) -> Account | None:
        """Stored account (None when not connected)."""
        return account_from_data(self.hub.entry.data.get(CONF_PURCHASE_EMAIL))

    @property
    def opted(self) -> OptedIn:
        """Opted-in stores and custom domains."""
        options = self.hub.options
        learned = self.meta.get("shop_ids") or {}
        return OptedIn(
            tuple(options.get(CONF_PURCHASE_STORES) or ()),
            tuple(options.get(CONF_PURCHASE_CUSTOM) or ()),
            tuple(options.get(CONF_PURCHASE_SHOPIFY) or ()),
            tuple((str(k), str(v)) for k, v in learned.items()),
        )

    @property
    def interval(self) -> timedelta:
        """Time between checks (at least the minimum)."""
        minutes = int(
            self.hub.options.get(CONF_PURCHASE_INTERVAL, DEFAULT_PURCHASE_INTERVAL)
        )
        return timedelta(minutes=max(MIN_PURCHASE_INTERVAL, minutes))

    @property
    def backfill_days(self) -> int:
        """Days searched on the first run (0 = only new mail)."""
        return int(
            self.hub.options.get(CONF_PURCHASE_BACKFILL, DEFAULT_PURCHASE_BACKFILL)
        )

    @property
    def clothing_only(self) -> bool:
        """Drop non-apparel items."""
        return bool(self.hub.options.get(CONF_PURCHASE_CLOTHING_ONLY, True))

    @property
    def archive_after(self) -> timedelta:
        """Unmatched cards are archived after this long."""
        return timedelta(
            days=int(
                self.hub.options.get(
                    CONF_PURCHASE_ARCHIVE_DAYS, DEFAULT_PURCHASE_ARCHIVE_DAYS
                )
            )
        )

    @property
    def connected(self) -> bool:
        """Credentials stored."""
        return self.account is not None

    @property
    def active(self) -> bool:
        """Checks run: wardrobe on, connected, at least one sender opted in."""
        return (
            self.hub.wardrobe.enabled
            and self.connected
            and bool(self.opted.domains() or self.opted.shopify())
        )

    @property
    def meta(self) -> dict[str, Any]:
        """Persistent inbox state (UIDs, seen IDs, last check)."""
        return self.hub.catalog.purchase_meta

    @property
    def cards(self) -> dict[str, dict[str, Any]]:
        """All cards by id."""
        return self.hub.catalog.purchases

    # --- lifecycle -------------------------------------------------------

    async def async_setup(self) -> None:
        """Start the schedule (only when active) and the one-time cleanup."""
        if self.hub.wardrobe.enabled and self.cleanup_pending:
            self._unsubs.append(
                async_call_later(self.hass, CLEANUP_DELAY, self._scheduled_cleanup)
            )
        if not self.active:
            return
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._scheduled, self.interval, name="laundry_hub purchases"
            )
        )
        self._unsubs.append(
            async_call_later(self.hass, FIRST_CHECK_DELAY, self._scheduled)
        )

    async def async_unload(self) -> None:
        """Stop the schedule."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _scheduled_cleanup(self, _now: datetime) -> None:
        self.hub.entry.async_create_background_task(
            self.hass, self.async_cleanup_once(), "laundry_hub purchase cleanup"
        )

    @callback
    def _scheduled(self, _now: datetime) -> None:
        self.hub.entry.async_create_background_task(
            self.hass, self.async_check(), "laundry_hub purchase emails"
        )

    # --- checking --------------------------------------------------------

    async def async_check(self, *, manual: bool = False) -> dict[str, Any]:
        """Run one check. Returns a summary (counts only)."""
        account = self.account
        if not self.hub.wardrobe.enabled or account is None:
            if manual:
                raise _err(
                    "purchases_not_connected",
                    "Connect purchase emails in Laundry Hub's settings first",
                )
            return {"skipped": "not_connected"}
        if not self.hub.wardrobe.ai_entity:
            self._record_error("NoAIEntity")
            if manual:
                raise _err(
                    "no_ai_entity", "Choose an AI Task entity in Laundry Hub's settings"
                )
            return {"skipped": "no_ai_entity"}
        now = dt_util.utcnow()
        if not manual and self._next_allowed and now < self._next_allowed:
            return {"skipped": "backoff"}
        if self._lock.locked():
            return {"skipped": "running"}
        async with self._lock:
            await self._async_cleanup()
            try:
                summary = await self._async_run(account)
            except AuthFailed:
                self._failures += 1
                self._backoff(now)
                self._record_error("AuthFailed")
                self._raise_auth_issue()
                if manual:
                    raise _err(
                        "invalid_auth",
                        "Google rejected the password — use an app password",
                    ) from None
                return {"error": "AuthFailed"}
            except MailboxError as err:
                self._failures += 1
                self._backoff(now)
                self._record_error(type(err).__name__)
                _LOGGER.warning("Purchase email check failed: %s", type(err).__name__)
                if manual:
                    raise _err(err.key, "Couldn't check purchase emails") from None
                return {"error": type(err).__name__}
            self._failures = 0
            self._next_allowed = None
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_PURCHASE_AUTH)
            self.meta["last_check"] = now.isoformat()
            self.meta["last_error"] = summary.get("error")
            self.meta["last_new"] = summary["new_cards"]
            self.hub.catalog.schedule_save()
            self.hub.notify_changed()
            return summary

    def _backoff(self, now: datetime) -> None:
        delay = min(self.interval * (2 ** min(self._failures, 6)), MAX_BACKOFF)
        self._next_allowed = now + delay

    def _record_error(self, name: str) -> None:
        self.meta["last_error"] = name
        self.meta["last_error_time"] = dt_util.utcnow().isoformat()
        self.hub.catalog.schedule_save()

    def _raise_auth_issue(self) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_PURCHASE_AUTH,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_PURCHASE_AUTH,
            data={"entry_id": self.hub.entry.entry_id},
        )

    async def _async_run(self, account: Account) -> dict[str, Any]:
        opted = self.opted
        domains = opted.domains()
        shop_names = [name for _key, name in opted.shopify()]
        shop_ids = sorted({i for ids in opted.shop_ids().values() for i in ids})
        keywords = opted.keywords()
        folders: dict[str, Any] = self.meta.setdefault("folders", {})
        self._schema_logged = False  # "too complex" is logged once per run
        summary: dict[str, Any] = {
            "messages": 0,
            "skipped_senders": 0,
            "new_cards": 0,
            "updated_cards": 0,
            "error": None,
        }
        async with open_mailbox(account, self.client_factory) as box:
            state = folders.get(account.folder_key)
            fresh = (
                not isinstance(state, dict)
                or state.get("uidvalidity") != box.uidvalidity
            )
            last_uid = 0
            if fresh and self.backfill_days <= 0:
                uids: list[int] = []  # only mail that arrives from now on
            elif fresh:
                uids = await box.search_senders(domains, self.backfill_days, keywords)
                uids += await box.search_shopify(
                    shop_names, self.backfill_days, keywords, shop_ids
                )
            else:
                assert isinstance(state, dict)
                last_uid = int(state.get("last_uid") or 0)
                checked = dt_util.parse_datetime(str(state.get("checked") or ""))
                days = 2 if checked is None else (dt_util.utcnow() - checked).days + 2
                window = max(7, days)
                found = await box.search_senders(domains, window, keywords)
                found += await box.search_shopify(
                    shop_names, window, keywords, shop_ids
                )
                uids = [u for u in found if u > last_uid]
            uids = sorted(set(uids))[:MAX_MESSAGES_PER_RUN]
            processed = await self._async_process(box, uids, opted, keywords, summary)
            pending = summary.get("pending") or []
            cap = min(pending) - 1 if pending else None
            highest = max(
                [last_uid, *(u for u in processed if cap is None or u <= cap)]
            )
            if fresh and self.backfill_days <= 0:
                highest = max(highest, (box.uidnext or 1) - 1)
            if summary["error"] and fresh:
                pass  # unfinished backfill: the next run repeats it (IDs dedupe)
            else:
                checked_at = dt_util.utcnow().isoformat()
                if summary["error"] and isinstance(state, dict):
                    # Unfinished: keep the old search window so nothing is lost.
                    checked_at = str(state.get("checked") or checked_at)
                folders[account.folder_key] = {
                    "uidvalidity": box.uidvalidity,
                    "last_uid": highest,
                    "checked": checked_at,
                }
        await self._async_archive_stale()
        return summary

    async def _async_process(
        self,
        box: MailboxSession,
        uids: list[int],
        opted: OptedIn,
        keywords: list[str],
        summary: dict[str, Any],
    ) -> list[int]:
        """Process messages in UID order; returns the UIDs fully handled."""
        done: list[int] = []
        if not uids:
            return done
        seen: list[str] = self.meta.setdefault("seen_ids", [])
        seen_set = set(seen)
        for header in sorted(await box.fetch_headers(uids), key=lambda h: h.uid):
            match = opted.match(
                header.sender_domain,
                header.sender_name,
                header.subject,
                header.sender_local,
            )
            if match is None:
                summary["skipped_senders"] += 1  # body never fetched
                done.append(header.uid)
                continue
            if header.message_id and header.message_id in seen_set:
                done.append(header.uid)
                continue
            if not box.account.is_gmail and not subject_matches(
                header.subject, keywords or list(ORDER_KEYWORDS)
            ):
                done.append(header.uid)  # generic IMAP: subject filter is ours
                continue
            self._learn_shop_id(opted, match[0], header)
            key = header.message_id or f"uid:{box.account.folder_key}:{header.uid}"
            try:
                order = await self._async_extract(box, header, match[1])
            except HomeAssistantError as err:
                if self._record_failure(key, header, match[1], err, box):
                    summary["unreadable"] = summary.get("unreadable", 0) + 1
                    if header.message_id:  # moved past: never read again
                        seen.append(header.message_id)
                        seen_set.add(header.message_id)
                    done.append(header.uid)
                else:
                    summary["error"] = "AIFailed"
                    summary.setdefault("pending", []).append(header.uid)
                    self._record_error("AIFailed")
                continue  # one email never blocks the others
            (self.meta.get("failures") or {}).pop(key, None)
            summary["messages"] += 1
            if order is not None and order.is_order and order.items:
                new, updated = await self._async_apply(order, match, header)
                summary["new_cards"] += new
                summary["updated_cards"] += updated
            if header.message_id:
                seen.append(header.message_id)
                seen_set.add(header.message_id)
            done.append(header.uid)
        del seen[:-MAX_SEEN_IDS]
        return done

    def _learn_shop_id(self, opted: OptedIn, store_key: str, header: Header) -> None:
        """Remember a Shopify shop's id from its plus-address (first name match)."""
        found = shop_id(header.sender_local)
        if not found or not domain_matches(header.sender_domain or "", SHOPIFY_DOMAIN):
            return
        if opted.shop_ids().get(store_key):
            return
        self.meta.setdefault("shop_ids", {})[store_key] = found
        self.hub.catalog.schedule_save()

    def _record_failure(
        self,
        key: str,
        header: Header,
        store_name: str,
        err: Exception,
        box: MailboxSession,
    ) -> bool:
        """Count a failed read; True once the email is marked unreadable."""
        failures: dict[str, Any] = self.meta.setdefault("failures", {})
        entry = failures.setdefault(key, {"count": 0})
        entry.update(
            count=int(entry.get("count", 0)) + 1,
            message_id=header.message_id,
            sender_domain=header.sender_domain,
            store=store_name,
            date=header.date.isoformat() if header.date else None,
            error=type(err.__cause__ or err).__name__,
            uid=header.uid,
            folder=box.account.folder_key,
        )
        detail = redact(str(err.__cause__ or err) or type(err).__name__)
        if entry["count"] < MAX_ATTEMPTS:
            _LOGGER.warning(
                "Reading an order email from %s failed (attempt %s of %s): %s; "
                "will retry",
                header.sender_domain,
                entry["count"],
                MAX_ATTEMPTS,
                detail,
            )
            self.hub.catalog.schedule_save()
            return False
        del failures[key]
        self.meta.setdefault("unreadable", {})[key] = entry
        _LOGGER.warning(
            "An order email from %s couldn't be read after %s attempts (%s); "
            "skipping it. Retry from Laundry Hub's settings",
            header.sender_domain,
            MAX_ATTEMPTS,
            detail,
        )
        self.hub.catalog.schedule_save()
        return True

    def unreadable(self) -> list[dict[str, Any]]:
        """Emails that couldn't be read, newest first (no message ids)."""
        out = [
            {
                "store": e.get("store"),
                "date": e.get("date"),
                "sender_domain": e.get("sender_domain"),
                "error": e.get("error"),
            }
            for e in (self.meta.get("unreadable") or {}).values()
        ]
        return sorted(out, key=lambda e: str(e.get("date") or ""), reverse=True)

    async def async_retry_unreadable(self) -> dict[str, Any]:
        """Clear the unreadable marks and read those emails again now."""
        self.hub.wardrobe.require()
        marked: dict[str, Any] = self.meta.pop("unreadable", {}) or {}
        seen: list[str] = self.meta.setdefault("seen_ids", [])
        folders: dict[str, Any] = self.meta.setdefault("folders", {})
        for entry in marked.values():
            if entry.get("message_id") in seen:
                seen.remove(entry["message_id"])
            state = folders.get(str(entry.get("folder")))
            if not isinstance(state, dict):
                continue
            uid = int(entry.get("uid") or 0)
            state["last_uid"] = min(int(state.get("last_uid") or 0), max(uid - 1, 0))
            when = dt_util.parse_datetime(str(entry.get("date") or ""))
            checked = dt_util.parse_datetime(str(state.get("checked") or ""))
            if when is not None and (checked is None or when < checked):
                state["checked"] = (when - timedelta(days=1)).isoformat()
        self.hub.catalog.schedule_save()
        summary = await self.async_check(manual=True)
        return {**summary, "retried": len(marked)}

    async def _async_extract(
        self, box: MailboxSession, header: Header, store_name: str
    ) -> extract.Order | None:
        plain, html = await box.fetch_text(header.uid)
        text = (
            extract.html_to_text(html) if html.strip() else extract.plain_to_text(plain)
        )
        if not text:
            return None
        entity = self.hub.wardrobe.ai_entity
        if not entity:
            raise HomeAssistantError("No AI Task entity")
        try:
            return await extract.extract(
                self.hass,
                entity_id=entity,
                store_name=store_name,
                text=text,
                clothing_only=self.clothing_only,
                on_schema_error=self._schema_rejected,
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(f"{type(err).__name__}: {err}") from err

    # --- one-time cleanup (v0.2.6) -----------------------------------------

    @property
    def cleanup_pending(self) -> bool:
        """The v0.2.6 cleanup hasn't run on this store yet."""
        state = self.meta.get("cleanup") or {}
        return int(state.get("version") or 0) < CLEANUP_VERSION

    async def async_cleanup_once(self) -> dict[str, Any] | None:
        """Run the one-time cleanup now (if it hasn't run). Returns its counts."""
        if not self.cleanup_pending or not self.hub.wardrobe.enabled:
            return None
        async with self._lock:
            return await self._async_cleanup()

    async def _async_cleanup(self) -> dict[str, Any] | None:
        """Clean up cards made by older versions, once (counts logged only).

        1. Re-classify every new and dismissed card with the small step-2
           request (a). Health products are deleted; with clothing-only on,
           non-apparel cards and their photos are deleted outright. A card
           the AI didn't answer for is kept (deletion fails closed).
        2. Group duplicates, keeping the most complete data.
        3. Split " - colour / size / cut" name suffixes on what's left.

        Cards matched to a garment are never touched.
        """
        if not self.cleanup_pending or not self.hub.wardrobe.enabled:
            return None
        self._schema_logged = False
        removed, classified = await self._async_cleanup_classify()
        grouped = await self._async_cleanup_group()
        split = self._cleanup_split()
        result = {
            "version": CLEANUP_VERSION,
            "at": dt_util.utcnow().isoformat(),
            "removed": removed,
            "grouped": grouped,
            "split": split,
            "classified": classified,
            "seen": not (removed or grouped),
        }
        self.meta["cleanup"] = result
        _LOGGER.info(
            "Purchase cards cleaned up: %s non-clothing removed, %s duplicates "
            "grouped, %s names split%s",
            removed,
            grouped,
            split,
            "" if classified else " (not re-classified: no AI answer)",
        )
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return result

    async def _async_cleanup_classify(self) -> tuple[int, bool]:
        """Delete health and (clothing-only) non-apparel cards. (removed, ran)."""
        cards = [c for c in self.cards.values() if c.get("status") in CLEANUP_STATUSES]
        if not cards:
            return 0, True
        entity = self.hub.wardrobe.ai_entity
        if not entity:
            return 0, False
        names = [str(c.get("name") or "") for c in cards]
        try:
            answers = await extract.classify(
                self.hass,
                entity_id=entity,
                names=names,
                on_schema_error=self._schema_rejected,
            )
        except Exception as err:
            _LOGGER.warning(
                "Couldn't re-classify old purchase cards (%s); none were removed",
                type(err).__name__,
            )
            return 0, False
        removed = 0
        for card, answer in zip(cards, answers, strict=True):
            health = answer.get("is_health") is True
            not_apparel = answer.get("is_apparel") is False
            if health or (self.clothing_only and not_apparel):
                await self._async_delete_photos(card)
                self.cards.pop(card["id"], None)
                removed += 1
        return removed, True

    async def _async_cleanup_group(self) -> int:
        """Group duplicate new/dismissed cards. Returns how many were absorbed."""
        cards = sorted(
            (c for c in self.cards.values() if c.get("status") in CLEANUP_STATUSES),
            key=lambda c: (str(c.get("order_date") or ""), str(c.get("created") or "")),
        )
        kept: list[dict[str, Any]] = []
        absorbed = 0
        for card in cards:
            ensure_history(card)
            target = next(
                (k for k in kept if _duplicate_of(k, card)),
                None,
            )
            if target is None:
                kept.append(card)
                continue
            fields = {
                k: card.get(k) for k in ("name", "color", "size", "cut", "unit_price")
            }
            same = any(
                grouping.same_purchase(
                    line,
                    target,
                    fields,
                    order_ref=card.get("order_ref"),
                    order_date=card.get("order_date"),
                    kind="other",
                )
                for line in ensure_history(target)
            )
            self._absorb(target, card, same_units=same)
            if same and target.get("status") == "new":
                target["dismissed_units"] = 0  # a dismissed copy of the same units
            self.cards.pop(card["id"], None)
            await self._async_delete_photos(card)
            self._refresh(target)
            absorbed += 1
        return absorbed

    def _cleanup_split(self) -> int:
        """Split " - A / B[ / C]" name suffixes into empty fields."""
        count = 0
        for card in self.cards.values():
            if card.get("status") not in CLEANUP_STATUSES or not card.get("name"):
                continue
            name, color, size, cut = extract.split_suffix(
                str(card["name"]), card.get("color"), card.get("size"), card.get("cut")
            )
            if name != card["name"]:
                card.update(name=name, color=color, size=size, cut=cut)
                count += 1
        return count

    def cleanup_seen(self) -> None:
        """The cleanup result was shown: stop showing it."""
        state = self.meta.get("cleanup")
        if isinstance(state, dict):
            state["seen"] = True
            self.hub.catalog.schedule_save()
            self.hub.notify_changed()

    def _schema_rejected(self, structure: str, fields: int) -> None:
        """The AI provider refused a structure as too complex: log once per run."""
        if self._schema_logged:
            return
        self._schema_logged = True
        _LOGGER.warning(
            "The AI Task provider rejected the purchase-email structure %r "
            "(%s fields) as too complex; using a smaller one",
            structure,
            fields,
        )

    # --- cards -----------------------------------------------------------

    def _blocked(self, store_key: str, name: str) -> bool:
        blocked = (self.meta.get("not_clothing") or {}).get(store_key) or []
        return _norm(name) in blocked

    async def _async_apply(
        self, order: extract.Order, match: tuple[str, str], header: Header
    ) -> tuple[int, int]:
        """Record an order email's items. Returns (new cards, updated cards).

        Each item is either the same units as a purchase already recorded
        (confirmation → shipping → delivery of one order: fields fill in,
        the quantity is never added up), a new purchase of an item that
        already has a card (it joins that group with its own history line and
        quantity), or a new card. See ``grouping``.
        """
        store_key = match[0]
        kind = grouping.email_kind(header.subject)
        received = header.date.date() if header.date else None
        order_date = (order.order_date or received or date.today()).isoformat()
        same_ref = [
            c
            for c in self.cards.values()
            if c.get("store_key") == store_key
            and order.order_ref
            and any(
                grouping.refs_equal(h.get("order_ref"), order.order_ref)
                for h in ensure_history(c)
            )
        ]
        single = len(order.items) == 1
        known_order = bool(same_ref)
        new = updated = 0
        for item in order.items:
            if self._blocked(store_key, item.name):
                continue
            fields = _item_fields(item)
            found = self._find_purchase(
                store_key,
                fields,
                order,
                order_date=order_date,
                kind=kind,
                same_ref=same_ref,
                single=single,
            )
            if found is not None:
                card, purchase = found
                changed = self._record_same(
                    card, purchase, item, order=order, header=header, kind=kind
                )
                if (
                    item.image_url
                    and not card.get("photos")
                    and card["status"] == "new"
                    and await self._async_add_photo(card, item.image_url)
                ):
                    changed = True
                updated += int(changed)
                continue
            if known_order and not item.is_apparel:
                # A shipment only ever adds clothing to a known order.
                continue
            group = self._find_group(store_key, fields)
            if group is not None:
                self._add_purchase(
                    group,
                    item,
                    order=order,
                    header=header,
                    kind=kind,
                    order_date=order_date,
                )
                if item.image_url and not group.get("photos"):
                    await self._async_add_photo(group, item.image_url)
                updated += 1
                continue
            card = await self._async_new_card(
                order, item, match, header, kind=kind, order_date=order_date
            )
            same_ref.append(card)
            new += 1
        self.hub.catalog.schedule_save()
        return new, updated

    def _find_purchase(
        self,
        store_key: str,
        fields: dict[str, Any],
        order: extract.Order,
        *,
        order_date: str,
        kind: str,
        same_ref: list[dict[str, Any]],
        single: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The card and purchase this item is already recorded as (any status)."""
        for card in self.cards.values():
            if card.get("store_key") != store_key:
                continue
            ref_match = card in same_ref
            identity = grouping.same_item(card, fields) or (
                ref_match
                and (
                    _contains(card.get("name"), fields["name"])
                    or (single and len(same_ref) == 1)  # the store renamed it
                )
            )
            if not identity:
                continue
            for purchase in ensure_history(card):
                if grouping.same_purchase(
                    purchase,
                    card,
                    fields,
                    order_ref=order.order_ref,
                    order_date=order_date,
                    kind=kind,
                ):
                    return card, purchase
        return None

    def _find_group(
        self, store_key: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """An unmatched card of the same item to add a new purchase to."""
        for card in self.cards.values():
            if (
                card.get("store_key") == store_key
                and card.get("status") in GROUPABLE
                and grouping.same_item(card, fields)
            ):
                return card
        return None

    def _record_same(
        self,
        card: dict[str, Any],
        purchase: dict[str, Any],
        item: extract.Item,
        *,
        order: extract.Order,
        header: Header,
        kind: str,
    ) -> bool:
        """Another email about the same units: fill gaps, never add quantities."""
        kinds: list[str] = purchase.setdefault("kinds", [])
        changed = False
        if kind not in kinds:
            kinds.append(kind)
        if header.message_id and header.message_id not in purchase.setdefault(
            "message_ids", []
        ):
            purchase["message_ids"].append(header.message_id)
        if order.order_ref and not purchase.get("order_ref"):
            purchase["order_ref"] = order.order_ref
            changed = True
        when = order.order_date.isoformat() if order.order_date else None
        if (
            when
            and (kind == "confirmation" or not purchase.get("order_date"))
            and purchase.get("order_date") != when
        ):
            purchase["order_date"] = when
            changed = True
        if kind == "confirmation":
            quantity = item.quantity  # the confirmation's quantity wins
        elif "confirmation" in kinds:
            quantity = int(purchase.get("quantity") or 1)
        else:
            quantity = max(int(purchase.get("quantity") or 1), item.quantity)
        if quantity != purchase.get("quantity"):
            purchase["quantity"] = quantity
            changed = True
        if item.unit_price is not None and purchase.get("unit_price") is None:
            purchase["unit_price"] = item.unit_price
            changed = True
        changed = _fill(card, item) or changed
        if changed:
            self._refresh(card)
        return changed

    def _add_purchase(
        self,
        card: dict[str, Any],
        item: extract.Item,
        *,
        order: extract.Order,
        header: Header,
        kind: str,
        order_date: str,
    ) -> None:
        """A new purchase of an item that already has a card: join the group."""
        ensure_history(card).append(
            _purchase(
                card.get("store"),
                item,
                order=order,
                header=header,
                kind=kind,
                order_date=order_date,
            )
        )
        if card.get("status") in ("dismissed", "archived"):
            # The old units stay dismissed; the new ones are waiting.
            self._set_status(card, "new")
        _fill(card, item)
        self._refresh(card)

    def _refresh(self, card: dict[str, Any]) -> None:
        """Recount the quantity (unless locked), summary fields and status."""
        history = ensure_history(card)
        if not card.get("quantity_locked"):
            card["quantity"] = max(1, sum(int(h.get("quantity") or 1) for h in history))
        latest = max(history, key=lambda h: str(h.get("order_date") or ""))
        card["order_ref"] = latest.get("order_ref") or card.get("order_ref")
        card["order_date"] = latest.get("order_date") or card.get("order_date")
        if card.get("unit_price") is None:
            card["unit_price"] = next(
                (h["unit_price"] for h in history if h.get("unit_price") is not None),
                None,
            )
        remaining = _remaining(card)
        if remaining <= 0 and card.get("status") == "new":
            self._set_status(card, "matched" if _matched_units(card) else "dismissed")
        elif remaining > 0 and card.get("status") == "matched":
            self._set_status(card, "new")
        card["updated"] = dt_util.utcnow().isoformat()

    async def _async_new_card(
        self,
        order: extract.Order,
        item: extract.Item,
        match: tuple[str, str],
        header: Header,
        *,
        kind: str = "other",
        order_date: str | None = None,
    ) -> dict[str, Any]:
        """Create (and store) one card for an item."""
        store_key, store_name = match
        now = dt_util.utcnow().isoformat()
        received = header.date.date() if header.date else None
        order_date = (
            order_date or (order.order_date or received or date.today()).isoformat()
        )
        card_id = ulid_now()
        new_card: dict[str, Any] = {
            "id": card_id,
            "status": "new",
            "store": store_name,
            "store_key": store_key,
            "sender_domain": header.sender_domain,
            "order_ref": order.order_ref,
            "order_date": order_date,
            "currency": order.currency or self.hass.config.currency,
            "name": item.name,
            "brand": item.brand,
            "color": item.color,
            "size": item.size,
            "cut": item.cut,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "product_url": item.product_url,
            "image_url": item.image_url,
            "photos": [],
            "garment_id": None,
            "source_message_id": header.message_id,
            "created": now,
            "updated": now,
            "status_changed": now,
            "history": [
                _purchase(
                    store_name,
                    item,
                    order=order,
                    header=header,
                    kind=kind,
                    order_date=order_date,
                )
            ],
            "quantity_locked": False,
            "matches": [],
            "dismissed_units": 0,
            "owner": None,
        }
        new_card["history"][0]["currency"] = new_card["currency"]
        if item.image_url:
            photo = await self._async_photo(card_id, item.image_url)
            if photo:
                new_card["photos"] = [photo]
        self.cards[card_id] = new_card
        return new_card

    async def _async_add_photo(self, card: dict[str, Any], url: str) -> bool:
        photo = await self._async_photo(card["id"], url)
        if not card.get("image_url"):
            card["image_url"] = url
        if photo:
            card["photos"] = [*(card.get("photos") or []), photo]
        return photo is not None

    async def _async_photo(self, card_id: str, url: str) -> str | None:
        """Download a card photo; None (card kept without photo) on any failure."""
        try:
            raw = await fetch_image(self.hass, url)
            processed = await self.hass.async_add_executor_job(process_image, raw)
            filename = f"purchase-{card_id.lower()}-{ulid_now().lower()}.jpg"
            await self.hass.async_add_executor_job(
                write_processed, photo_dir(self.hass), filename, processed
            )
        except (ImportFailed, PhotoError, OSError) as err:
            _LOGGER.debug("Skipped a purchase photo: %s", type(err).__name__)
            return None
        return filename

    async def _async_delete_photos(self, card: dict[str, Any]) -> None:
        directory = photo_dir(self.hass)
        for filename in card.get("photos") or []:
            await self.hass.async_add_executor_job(delete_photo, directory, filename)
        card["photos"] = []

    async def _async_archive_stale(self) -> None:
        cutoff = dt_util.utcnow() - self.archive_after
        for card in list(self.cards.values()):
            if card.get("status") != "new":
                continue
            # A restored card counts from when it was restored, not created.
            since = dt_util.parse_datetime(
                str(card.get("status_changed") or card.get("created") or "")
            )
            if since is not None and since < cutoff:
                self._set_status(card, "archived")
                await self._async_delete_photos(card)

    def _set_status(self, card: dict[str, Any], status: str) -> None:
        card["status"] = status
        card["status_changed"] = dt_util.utcnow().isoformat()

    def get(self, card_id: str) -> dict[str, Any]:
        """A card by id, or a validation error."""
        card = self.cards.get(card_id)
        if card is None:
            raise _err("unknown_purchase", "That purchase card no longer exists")
        return card

    async def async_match(
        self, card_id: str, garment: Garment, units: int | None = None
    ) -> dict[str, Any]:
        """Fill a garment from a card: purchase details, brand, size, cut, photos.

        A group of N units can be matched to up to N garments, one unit per
        match (``units=1``), or to one garment for all remaining units
        (``units=None``). It stays in the inbox until every unit is matched or
        dismissed.
        """
        self.hub.wardrobe.require()
        card = self.get(card_id)
        ensure_history(card)
        remaining = _remaining(card)
        if card["status"] not in ("new", "archived") or remaining <= 0:
            raise _err("purchase_used", "That purchase card was already used")
        take = remaining if units is None else max(1, min(int(units), remaining))
        wardrobe = self.hub.wardrobe
        garment.purchase = wardrobe.clean_purchase(
            {
                "store": card.get("store"),
                "purchased_on": card.get("order_date"),
                "price": card.get("unit_price"),
                "currency": card.get("currency"),
                "product_url": card.get("product_url"),
                "order_ref": card.get("order_ref"),
                "source": "email",
            },
            source="email",
        )
        provenance = garment.description_provenance
        if card.get("brand") and provenance.get("brand") != "user":
            garment.brand = str(card["brand"])[:60]
            provenance["brand"] = "import"
        size_cut = cut_size_line(card.get("cut"))
        if card.get("size") and provenance.get("size") != "user":
            garment.size = wardrobe.clean_size(
                {"label": card["size"], "system": "other", "cut": size_cut}
            )
            provenance["size"] = "import"
        fit = cut_fit(card.get("cut"))
        if fit and provenance.get("fit") != "user":
            desc = dict(garment.description or dict.fromkeys(tx.DESCRIPTION_FIELDS))
            if not desc.get("fit") or provenance.get("fit") in (None, "import"):
                desc["fit"] = fit
                garment.description = desc
                provenance["fit"] = "import"
        if is_uniform_supplier(card.get("store_key")):
            # Preset only: the user can untick it in the garment's details.
            garment.uniform = True
            garment.wear_for = "work"
        for filename in card.get("photos") or []:
            if len(garment.photos) >= wardrobe.photo_limit():
                break
            await wardrobe.async_copy_photo(garment, filename)
        matches: list[dict[str, Any]] = card.setdefault("matches", [])
        existing = next((m for m in matches if m["garment_id"] == garment.id), None)
        if existing is not None:
            existing["units"] += take
        else:
            matches.append({"garment_id": garment.id, "units": take})
        card["garment_id"] = card.get("garment_id") or garment.id
        if _remaining(card) <= 0:
            self._set_status(card, "matched")
        card["updated"] = dt_util.utcnow().isoformat()
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return card

    async def async_dismiss(
        self, card_id: str, *, not_clothing: bool = False
    ) -> dict[str, Any]:
        """Dismiss a card's remaining units; optionally remember 'not clothing'."""
        card = self.get(card_id)
        ensure_history(card)
        if not_clothing and card.get("name"):
            blocked = self.meta.setdefault("not_clothing", {}).setdefault(
                str(card.get("store_key")), []
            )
            name = _norm(card["name"])
            if name and name not in blocked:
                blocked.append(name)
        card["dismissed_units"] = int(card.get("dismissed_units") or 0) + max(
            0, _remaining(card)
        )
        # Matched units live on with their garments (which have photo copies).
        self._set_status(card, "matched" if _matched_units(card) else "dismissed")
        await self._async_delete_photos(card)
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return card

    async def async_restore(self, card_id: str) -> dict[str, Any]:
        """Move a dismissed or archived card back to the inbox.

        Its photo was deleted when it was dismissed or archived, so it is
        fetched again (once, through the pinned pipeline) from the stored
        image link, if there is one. A "Not clothing" rule for it is lifted.
        """
        self.hub.wardrobe.require()
        card = self.get(card_id)
        if card.get("status") not in ("dismissed", "archived"):
            raise _err("purchase_not_archived", "That purchase card isn't archived")
        ensure_history(card)
        blocked = (self.meta.get("not_clothing") or {}).get(str(card.get("store_key")))
        if blocked and _norm(card.get("name")) in blocked:
            blocked.remove(_norm(card.get("name")))
        card["dismissed_units"] = 0
        self._set_status(card, "new")
        if not _matched_units(card):
            card["garment_id"] = None
        if not card.get("photos") and card.get("image_url"):
            photo = await self._async_photo(card["id"], str(card["image_url"]))
            if photo:
                card["photos"] = [photo]
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return card

    # --- groups (v0.2.6) ---------------------------------------------------

    def set_quantity(self, card_id: str, quantity: int | None) -> dict[str, Any]:
        """Set (and lock) a group's quantity; None unlocks and recounts."""
        card = self.get(card_id)
        ensure_history(card)
        if quantity is None:
            card["quantity_locked"] = False
        else:
            floor = max(1, _matched_units(card))
            if quantity < floor:
                raise _err(
                    "quantity_too_low",
                    f"{floor} of these are already matched to garments",
                )
            card["quantity"] = int(quantity)
            card["quantity_locked"] = True
            card["dismissed_units"] = min(
                int(card.get("dismissed_units") or 0), quantity - _matched_units(card)
            )
        self._refresh(card)
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return card

    async def async_split(self, card_id: str, purchase_id: str) -> dict[str, Any]:
        """Pull one purchase out of a group into its own card (returns the new card)."""
        card = self.get(card_id)
        history = ensure_history(card)
        purchase = next((h for h in history if h.get("id") == purchase_id), None)
        if purchase is None:
            raise _err("unknown_purchase_line", "That purchase isn't in this card")
        if len(history) < 2:
            raise _err("cannot_split", "A card with one purchase can't be split")
        units = int(purchase.get("quantity") or 1)
        if card.get("quantity_locked"):
            left = int(card["quantity"]) - units
        else:
            left = sum(int(h.get("quantity") or 1) for h in history) - units
        if left < _matched_units(card):
            raise _err("cannot_split", "Those units are already matched to garments")
        history.remove(purchase)
        if card.get("quantity_locked"):
            card["quantity"] = max(1, left)
        now = dt_util.utcnow().isoformat()
        new_id = ulid_now()
        twin = {
            key: card.get(key)
            for key in (
                "store", "store_key", "sender_domain", "currency", "name", "brand",
                "color", "size", "cut", "product_url", "image_url", "owner",
            )
        }  # fmt: skip
        twin.update(
            id=new_id,
            status="new",
            order_ref=purchase.get("order_ref"),
            order_date=purchase.get("order_date"),
            unit_price=purchase.get("unit_price"),
            quantity=units,
            quantity_locked=False,
            history=[purchase],
            matches=[],
            dismissed_units=0,
            garment_id=None,
            photos=[],
            source_message_id=(purchase.get("message_ids") or [None])[0],
            created=now,
            updated=now,
            status_changed=now,
        )
        directory = photo_dir(self.hass)
        copies: list[str] = []
        for filename in card.get("photos") or []:
            target = f"purchase-{new_id.lower()}-{ulid_now().lower()}.jpg"
            if await self.hass.async_add_executor_job(
                copy_photo, directory, filename, target
            ):
                copies.append(target)
        twin["photos"] = copies
        self.cards[new_id] = twin
        card["dismissed_units"] = min(
            int(card.get("dismissed_units") or 0),
            max(0, left - _matched_units(card)),
        )
        self._refresh(card)
        self._refresh(twin)
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return twin

    async def async_merge(self, card_id: str, other_id: str) -> dict[str, Any]:
        """Combine two cards into one group (returns the kept card)."""
        if card_id == other_id:
            raise _err("cannot_merge", "Choose two different cards")
        first, second = self.get(card_id), self.get(other_id)
        for card in (first, second):
            ensure_history(card)
            if card.get("status") == "matched":
                raise _err("cannot_merge", "Matched cards can't be merged")
        keep, gone = (
            (first, second) if _completeness(first) >= _completeness(second)
            else (second, first)
        )  # fmt: skip
        self._absorb(keep, gone, same_units=False)
        self.cards.pop(gone["id"], None)
        await self._async_delete_photos(gone)
        self._refresh(keep)
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return keep

    def _absorb(
        self, keep: dict[str, Any], gone: dict[str, Any], *, same_units: bool
    ) -> None:
        """Move ``gone`` into ``keep``: history, fields, photos, units, owner.

        ``same_units``: the two cards are the same purchase (a duplicate), so
        their history lines become one and nothing is added up.
        """
        history = ensure_history(keep)
        theirs = ensure_history(gone)
        if same_units:
            base = history[0]
            for line in theirs:
                base.setdefault("message_ids", [])
                for mid in line.get("message_ids") or []:
                    if mid not in base["message_ids"]:
                        base["message_ids"].append(mid)
                for key in ("order_ref", "order_date", "unit_price"):
                    if not base.get(key) and line.get(key):
                        base[key] = line[key]
        else:
            history.extend(theirs)
            history.sort(key=lambda h: str(h.get("order_date") or ""))
            if gone.get("status") in ("dismissed", "archived"):
                keep["dismissed_units"] = (
                    int(keep.get("dismissed_units") or 0)
                    + int(gone.get("quantity") or 1)
                    - _matched_units(gone)
                )
            else:
                keep["dismissed_units"] = int(keep.get("dismissed_units") or 0) + int(
                    gone.get("dismissed_units") or 0
                )
            if keep.get("quantity_locked") or gone.get("quantity_locked"):
                keep["quantity"] = int(keep.get("quantity") or 1) + int(
                    gone.get("quantity") or 1
                )
                keep["quantity_locked"] = True
        for key in ("brand", "color", "size", "cut", "unit_price", "product_url",
                    "image_url", "order_ref", "currency"):  # fmt: skip
            if keep.get(key) in (None, "") and gone.get(key) not in (None, ""):
                keep[key] = gone[key]
        keep["owner"] = keep.get("owner") or gone.get("owner")
        keep.setdefault("matches", []).extend(gone.get("matches") or [])
        if not keep.get("photos") and gone.get("photos"):
            keep["photos"], gone["photos"] = gone["photos"], []
        if keep.get("status") != "new" and gone.get("status") == "new":
            self._set_status(keep, "new")

    def assign(self, card_ids: list[str], owner: str | None) -> list[dict[str, Any]]:
        """Assign cards to a household member (None = unassigned)."""
        owner = self.hub.validate_owner(owner)
        cards = [self.get(cid) for cid in card_ids]
        for card in cards:
            card["owner"] = owner
            card["updated"] = dt_util.utcnow().isoformat()
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return cards

    def suggested_owner(self, card: dict[str, Any]) -> str | None:
        """'Suggested: <owner>' after two similar cards went to one owner.

        Similar: the same size line ("Kids", "Toddler", "Girls'") or the same
        product line at the same store. Never assigned without a tap.
        """
        if card.get("owner") or card.get("status") != "new":
            return None
        keys = grouping.similarity_keys(card)
        if not keys:
            return None
        counts: dict[tuple[str, str], int] = {}
        for other in self.cards.values():
            owner = other.get("owner")
            if not owner or other is card:
                continue
            for key in keys & grouping.similarity_keys(other):
                counts[(key, owner)] = counts.get((key, owner), 0) + 1
        best = sorted(
            ((n, key.startswith("line:"), owner) for (key, owner), n in counts.items()
             if n >= SUGGEST_AFTER),
            reverse=True,
        )  # fmt: skip
        return best[0][2] if best else None

    def archived(self, limit: int = 100) -> list[dict[str, Any]]:
        """Dismissed and auto-archived cards, newest first."""
        cards = [
            c
            for c in self.cards.values()
            if c.get("status") in ("dismissed", "archived")
        ]
        cards.sort(key=lambda c: str(c.get("status_changed") or ""), reverse=True)
        return cards[:limit]

    def uniform_offers(self) -> list[dict[str, Any]]:
        """Uniform-supplier stores with matched garments not yet marked uniform.

        Offered once per store: accepting or declining records the store.
        """
        done = set(self.meta.get("uniform_offers_done") or [])
        offers: dict[str, dict[str, Any]] = {}
        for card in self.cards.values():
            key = str(card.get("store_key") or "")
            if card.get("status") != "matched" or key in done:
                continue
            if not is_uniform_supplier(key):
                continue
            garment = self.hub.catalog.garments.get(str(card.get("garment_id")))
            if garment is None or garment.uniform:
                continue
            offer = offers.setdefault(
                key, {"store_key": key, "store": BY_KEY[key].name, "count": 0}
            )
            offer["count"] += 1
        return sorted(offers.values(), key=lambda o: o["store"])

    def mark_store_uniform(self, store_key: str, *, accept: bool = True) -> int:
        """Accept (mark matched garments uniform) or decline a store's offer."""
        self.hub.wardrobe.require()
        if not is_uniform_supplier(store_key):
            raise _err("not_uniform_supplier", "That store isn't a uniform supplier")
        count = 0
        if accept:
            for card in self.cards.values():
                if (
                    card.get("status") != "matched"
                    or card.get("store_key") != store_key
                ):
                    continue
                garment = self.hub.catalog.garments.get(str(card.get("garment_id")))
                if garment is not None and not garment.uniform:
                    garment.uniform = True
                    garment.wear_for = "work"
                    count += 1
        done: list[str] = self.meta.setdefault("uniform_offers_done", [])
        if store_key not in done:
            done.append(store_key)
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return count

    async def async_delete_all(self) -> int:
        """Delete every card and its photos (Disconnect → delete cards)."""
        count = len(self.cards)
        for card in list(self.cards.values()):
            await self._async_delete_photos(card)
        self.cards.clear()
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return count

    def reset_state(self) -> None:
        """Forget UIDs, seen IDs and errors (Disconnect)."""
        for key in ("folders", "seen_ids", "last_check", "last_error", "last_new"):
            self.meta.pop(key, None)
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_PURCHASE_AUTH)
        self.hub.catalog.schedule_save()

    # --- views -----------------------------------------------------------

    def unmatched(self) -> list[dict[str, Any]]:
        """Cards waiting to be matched."""
        return [c for c in self.cards.values() if c.get("status") == "new"]

    def owns_photo(self, filename: str) -> bool:
        """True if a card has this photo (for the photo view)."""
        return any(filename in (c.get("photos") or []) for c in self.cards.values())

    def suggest(self, owner: str | None, wash_bin: str | None) -> list[dict[str, Any]]:
        """Unmatched cards ranked for the Add flow (never auto-matched)."""
        stores = {
            str((g.purchase or {}).get("store"))
            for g in self.hub.catalog.garments.values()
            if owner and g.owner == owner and g.purchase and g.purchase.get("store")
        }
        bin_name = None
        if wash_bin:
            zone = self.hub.zones.get(wash_bin)
            bin_name = zone.name if zone else None
        return rank_cards(self.unmatched(), stores, bin_name)

    def status(self) -> dict[str, Any]:
        """Connection summary for the options menu and the panel."""
        account = self.account
        return {
            "connected": account is not None,
            "provider": account.provider if account else None,
            "stores": self.opted.count(),
            "last_check": self.meta.get("last_check"),
            "last_error": self.meta.get("last_error"),
            "new_cards": len(self.unmatched()),
            "unreadable": self.unreadable(),
            "cleanup": self._cleanup_status(),
        }

    def _cleanup_status(self) -> dict[str, int] | None:
        """The cleanup result, until it has been shown once."""
        state = self.meta.get("cleanup")
        if not isinstance(state, dict) or state.get("seen"):
            return None
        return {"removed": int(state["removed"]), "grouped": int(state["grouped"])}

    def view(self, card: dict[str, Any]) -> dict[str, Any]:
        """Card fields for the panel (no message ids, no sender address)."""
        ensure_history(card)
        view = {
            key: card.get(key)
            for key in CARD_FIELDS
            if key not in ("source_message_id", "sender_domain", "image_url")
        }
        view["uniform_supplier"] = is_uniform_supplier(card.get("store_key"))
        view["history"] = [
            {k: line.get(k) for k in HISTORY_FIELDS} for line in card["history"]
        ]
        view["matches"] = [dict(m) for m in card.get("matches") or []]
        view["quantity_locked"] = bool(card.get("quantity_locked"))
        view["dismissed_units"] = int(card.get("dismissed_units") or 0)
        view["remaining"] = max(0, _remaining(card))
        view["owner"] = card.get("owner")
        view["suggested_owner"] = self.suggested_owner(card)
        view["fit"] = cut_fit(card.get("cut"))
        view["size_cut"] = cut_size_line(card.get("cut"))
        return self.hub.revs.stamp("purchase", str(card.get("id")), view)


async def async_count_store_emails(
    account: Account,
    days: int,
    factory: Callable[[Account], Any] | None = None,
) -> dict[str, int]:
    """Matching emails per catalog store in the last ``days`` days.

    One read-only search per store; only UIDs come back and only the counts
    are returned. Shopify shops share one sender, so their results are
    narrowed by reading headers (never bodies) and checking the shop name in
    the display name or subject. Used by the options flow.
    """
    from .stores import CATALOG, OptedIn  # noqa: PLC0415

    counts: dict[str, int] = {}
    async with open_mailbox(account, factory) as box:
        for store in CATALOG:
            if store.kind == "shopify":
                uids = await box.search_shopify(
                    [store.name], days, list(store.keywords), list(store.shop_ids)
                )
                own = [d for d in store.domains if d != SHOPIFY_DOMAIN]
                if own:
                    uids += await box.search_senders(own, days, list(store.keywords))
                opted = OptedIn((store.key,), ())
                headers = await box.fetch_headers(sorted(set(uids))) if uids else []
                counts[store.key] = sum(
                    1
                    for h in headers
                    if opted.match(
                        h.sender_domain, h.sender_name, h.subject, h.sender_local
                    )
                )
                continue
            uids = await box.search_senders(
                list(store.domains), days, list(store.keywords)
            )
            counts[store.key] = len(uids)
    return counts
