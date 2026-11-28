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

from . import extract
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
from .photos import PhotoError, delete_photo, photo_dir, process_image, write_processed
from .stores import BY_KEY, ORDER_KEYWORDS, OptedIn, is_uniform_supplier

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
        return OptedIn(
            tuple(options.get(CONF_PURCHASE_STORES) or ()),
            tuple(options.get(CONF_PURCHASE_CUSTOM) or ()),
            tuple(options.get(CONF_PURCHASE_SHOPIFY) or ()),
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
        """Start the schedule (only when active)."""
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
                    shop_names, self.backfill_days, keywords
                )
            else:
                assert isinstance(state, dict)
                last_uid = int(state.get("last_uid") or 0)
                checked = dt_util.parse_datetime(str(state.get("checked") or ""))
                days = 2 if checked is None else (dt_util.utcnow() - checked).days + 2
                window = max(7, days)
                found = await box.search_senders(domains, window, keywords)
                found += await box.search_shopify(shop_names, window, keywords)
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
                header.sender_domain, header.sender_name, header.subject
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
        """Create cards, or update a known order's cards. Returns (new, updated)."""
        store_key = match[0]
        existing = [
            c
            for c in self.cards.values()
            if order.order_ref
            and c.get("store_key") == store_key
            and c.get("order_ref") == order.order_ref
        ]
        now = dt_util.utcnow().isoformat()
        if existing:
            updated = 0
            added = 0
            single = len(order.items) == 1
            for item in order.items:
                found = self._find_card(existing, item.name, allow_single=single)
                if found is None:
                    # Partial shipment: an item not on any card of this order gets
                    # its own card, but only if it is (or may be) clothing.
                    if item.is_apparel and not self._blocked(store_key, item.name):
                        card_new = await self._async_new_card(
                            order, item, match, header
                        )
                        existing.append(card_new)
                        added += 1
                    continue
                card = found
                changed = False
                for key in MERGE_FIELDS:
                    value = getattr(item, key)
                    if value not in (None, "") and card.get(key) in (None, ""):
                        card[key] = value
                        changed = True
                if card.get("order_date") is None and order.order_date:
                    card["order_date"] = order.order_date.isoformat()
                    changed = True
                if (
                    not card.get("photos")
                    and item.image_url
                    and card["status"] == "new"
                ):
                    photo = await self._async_photo(card["id"], item.image_url)
                    if photo:
                        card["photos"] = [photo]
                        changed = True
                if item.image_url and not card.get("image_url"):
                    card["image_url"] = item.image_url
                if changed:
                    card["updated"] = now
                    updated += 1
            self.hub.catalog.schedule_save()
            return added, updated
        new = 0
        for item in order.items:
            if self._blocked(store_key, item.name):
                continue
            await self._async_new_card(order, item, match, header)
            new += 1
        self.hub.catalog.schedule_save()
        return new, 0

    async def _async_new_card(
        self,
        order: extract.Order,
        item: extract.Item,
        match: tuple[str, str],
        header: Header,
    ) -> dict[str, Any]:
        """Create (and store) one card for an item."""
        store_key, store_name = match
        now = dt_util.utcnow().isoformat()
        received = header.date.date() if header.date else None
        card_id = ulid_now()
        new_card: dict[str, Any] = {
            "id": card_id,
            "status": "new",
            "store": store_name,
            "store_key": store_key,
            "sender_domain": header.sender_domain,
            "order_ref": order.order_ref,
            "order_date": (order.order_date or received or date.today()).isoformat(),
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
        }
        if item.image_url:
            photo = await self._async_photo(card_id, item.image_url)
            if photo:
                new_card["photos"] = [photo]
        self.cards[card_id] = new_card
        return new_card

    @staticmethod
    def _find_card(
        cards: list[dict[str, Any]], name: str, *, allow_single: bool = True
    ) -> dict[str, Any] | None:
        wanted = _norm(name)
        for card in cards:
            if _norm(card.get("name")) == wanted:
                return card
        for card in cards:
            have = _norm(card.get("name"))
            if have and wanted and (have in wanted or wanted in have):
                return card
        # One card and an email about one item: the store renamed it slightly.
        return cards[0] if allow_single and len(cards) == 1 else None

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

    async def async_match(self, card_id: str, garment: Garment) -> dict[str, Any]:
        """Fill a garment from a card: purchase details, brand, size, photo copies."""
        self.hub.wardrobe.require()
        card = self.get(card_id)
        if card["status"] not in ("new", "archived"):
            raise _err("purchase_used", "That purchase card was already used")
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
        if card.get("size") and provenance.get("size") != "user":
            garment.size = wardrobe.clean_size(
                {"label": card["size"], "system": "other"}
            )
            provenance["size"] = "import"
        if is_uniform_supplier(card.get("store_key")):
            # Preset only: the user can untick it in the garment's details.
            garment.uniform = True
            garment.wear_for = "work"
        for filename in card.get("photos") or []:
            if len(garment.photos) >= wardrobe.photo_limit():
                break
            await wardrobe.async_copy_photo(garment, filename)
        self._set_status(card, "matched")
        card["garment_id"] = garment.id
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return card

    async def async_dismiss(
        self, card_id: str, *, not_clothing: bool = False
    ) -> dict[str, Any]:
        """Dismiss a card (and its photos); optionally remember 'not clothing'."""
        card = self.get(card_id)
        if not_clothing and card.get("name"):
            blocked = self.meta.setdefault("not_clothing", {}).setdefault(
                str(card.get("store_key")), []
            )
            name = _norm(card["name"])
            if name and name not in blocked:
                blocked.append(name)
        self._set_status(card, "dismissed")
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
        blocked = (self.meta.get("not_clothing") or {}).get(str(card.get("store_key")))
        if blocked and _norm(card.get("name")) in blocked:
            blocked.remove(_norm(card.get("name")))
        self._set_status(card, "new")
        card["garment_id"] = None
        if not card.get("photos") and card.get("image_url"):
            photo = await self._async_photo(card["id"], str(card["image_url"]))
            if photo:
                card["photos"] = [photo]
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return card

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
        }

    def view(self, card: dict[str, Any]) -> dict[str, Any]:
        """Card fields for the panel (no message id, no sender address)."""
        view = {
            key: card.get(key)
            for key in CARD_FIELDS
            if key not in ("source_message_id", "sender_domain", "image_url")
        }
        view["uniform_supplier"] = is_uniform_supplier(card.get("store_key"))
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
                    [store.name], days, list(store.keywords)
                )
                opted = OptedIn((store.key,), ())
                headers = await box.fetch_headers(uids) if uids else []
                counts[store.key] = sum(
                    1
                    for h in headers
                    if opted.match(h.sender_domain, h.sender_name, h.subject)
                )
                continue
            uids = await box.search_senders(
                list(store.domains), days, list(store.keywords)
            )
            counts[store.key] = len(uids)
    return counts
