r"""Read-only IMAP access for purchase emails.

Laundry Hub owns this connection (it never drives core IMAP). Rules:

* The folder is opened with ``EXAMINE`` (read-only), never ``SELECT``; no
  STORE / COPY / MOVE / EXPUNGE / APPEND is ever sent, and bodies are fetched
  with ``BODY.PEEK`` so not even the \Seen flag changes.
* Headers are fetched first. The body of a message is only fetched when its
  sender domain is opted in, and then only its text/plain and text/html parts
  (attachments are skipped), capped at ``MAX_BODY_BYTES`` in total.
* Nothing here logs the password, a body, a subject or any address other
  than a sender domain. aioimaplib's own DEBUG/INFO logging (which echoes
  server data, including bodies and the login name) is muted while a session
  of ours is open.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import email
from email.header import decode_header, make_header
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime
import logging
import quopri
import re
from typing import Any

from .stores import SHOPIFY_DOMAIN

_LOGGER = logging.getLogger(__name__)

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
GMAIL_FOLDER = "[Gmail]/All Mail"
PROVIDER_GMAIL = "gmail"
PROVIDER_GENERIC = "imap"
MAX_BODY_BYTES = 500 * 1024
HEADER_FIELDS = "FROM SUBJECT DATE MESSAGE-ID"
FETCH_CHUNK = 50
CONNECT_TIMEOUT = 20.0
_UID = re.compile(rb"UID (\d+)")
_LITERAL = re.compile(rb"\{(\d+)\}\s*$")
_UIDVALIDITY = re.compile(rb"UIDVALIDITY (\d+)")
_UIDNEXT = re.compile(rb"UIDNEXT (\d+)")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


class MailboxError(Exception):
    """A mailbox failure with a config-flow error key (no secrets in it)."""

    key = "cannot_connect"


class AuthFailed(MailboxError):
    """The server rejected the login."""

    key = "invalid_auth"


class CannotConnect(MailboxError):
    """The server could not be reached or broke the connection."""

    key = "cannot_connect"


class FolderNotFound(MailboxError):
    """The folder does not exist."""

    key = "folder_not_found"


@dataclass(frozen=True, slots=True)
class Account:
    """Connection settings. ``repr`` never shows the password or address."""

    email: str
    password: str
    host: str
    port: int
    folder: str
    provider: str

    def __repr__(self) -> str:
        """Safe representation for logs and tracebacks."""
        return f"Account(provider={self.provider!r}, host={self.host!r})"

    @property
    def is_gmail(self) -> bool:
        """True for Gmail (server-side X-GM-RAW search)."""
        return self.provider == PROVIDER_GMAIL

    @property
    def folder_key(self) -> str:
        """Key used to track UIDs per folder (no address in it)."""
        return f"{self.host}:{self.port}/{self.folder}"


def account_from_data(data: dict[str, Any] | None) -> Account | None:
    """Build an Account from stored config entry data (None if not connected)."""
    if not data or not data.get("email") or not data.get("password"):
        return None
    provider = data.get("provider") or PROVIDER_GMAIL
    if provider == PROVIDER_GMAIL:
        host, port, folder = GMAIL_HOST, GMAIL_PORT, GMAIL_FOLDER
    else:
        host = str(data.get("host") or "")
        port = int(data.get("port") or 993)
        folder = str(data.get("folder") or "INBOX")
    return Account(
        email=str(data["email"]),
        password=str(data["password"]),
        host=host,
        port=port,
        folder=folder,
        provider=provider,
    )


@dataclass(slots=True)
class Header:
    """The headers we read for every candidate message."""

    uid: int
    message_id: str | None
    sender_domain: str | None
    subject: str
    date: datetime | None
    sender_name: str = ""  # display name only (Shopify shop matching); never stored
    sender_local: str = ""  # address local part (Shopify shop id); never stored


# --- logging guard -----------------------------------------------------------


class _QuietImapLib(logging.Filter):
    """Drop aioimaplib DEBUG/INFO records while one of our sessions is open."""

    active = 0

    def filter(self, record: logging.LogRecord) -> bool:
        return not (self.active and record.levelno < logging.WARNING)


_QUIET = _QuietImapLib()
for _name in ("aioimaplib", "aioimaplib.aioimaplib"):
    logging.getLogger(_name).addFilter(_QUIET)


# --- client ------------------------------------------------------------------


def _create_client(account: Account) -> Any:
    """Create an aioimaplib SSL client (patched in tests with a fake server)."""
    from aioimaplib import IMAP4_SSL  # noqa: PLC0415 - heavy, only when connecting
    from homeassistant.util.ssl import client_context  # noqa: PLC0415

    return IMAP4_SSL(
        account.host, account.port, ssl_context=client_context(), timeout=30
    )


def _ok(response: Any) -> bool:
    return getattr(response, "result", None) == "OK"


def imap_quote(text: str) -> str:
    """Quote a string for an IMAP command."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_date(day: date) -> str:
    """IMAP SEARCH date, e.g. 01-Sep-2026 (locale independent)."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _words(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)


def gmail_shopify_query(
    names: list[str], days: int, keywords: list[str], ids: list[str] | None = None
) -> str:
    """X-GM-RAW query for Shopify shops: the shared sender plus the shop names.

    Gmail matches the names anywhere in the message, so this only narrows the
    search; the header check (shop id, display name or subject) decides. A
    known shop id adds ``from:store+<id>@``: those subjects may name no store.
    """
    parts = [f"from:{SHOPIFY_DOMAIN} ({_words(names)})"] if names else []
    parts += [f"from:store+{shop}@" for shop in ids or []]
    joined = parts[0] if len(parts) == 1 else f"({' OR '.join(parts)})"
    query = f"{joined} newer_than:{max(1, days)}d"
    if keywords:
        query += f" subject:({_words(keywords)})"
    return query


def gmail_query(domains: list[str], days: int, keywords: list[str]) -> str:
    """X-GM-RAW query: senders, age and subject words, filtered on Google's side."""
    query = f"from:({' OR '.join(domains)}) newer_than:{max(1, days)}d"
    if keywords:
        words = " OR ".join(f'"{w}"' if " " in w else w for w in keywords)
        query += f" subject:({words})"
    return query


def _literals(lines: list[Any]) -> list[tuple[bytes, bytes]]:
    """(preceding line, literal bytes) pairs from a FETCH response."""
    out: list[tuple[bytes, bytes]] = []
    for index, line in enumerate(lines):
        is_literal = isinstance(line, bytearray | bytes) and _LITERAL.search(
            bytes(line)
        )
        if is_literal and index + 1 < len(lines):
            out.append((bytes(line), bytes(lines[index + 1])))
    return out


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeError):
        return value


def sender_domain(from_header: str | None) -> str | None:
    """The domain of a From: header (the only address part we ever keep)."""
    _, address = parseaddr(_decode_header(from_header))
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].strip().lower().rstrip(">.")
    return domain or None


def parse_headers(uid: int, raw: bytes) -> Header:
    """Parse the header literal of one message."""
    message = email.message_from_bytes(raw, policy=default_policy)
    when: datetime | None
    try:
        when = parsedate_to_datetime(str(message.get("Date", "")))
    except (TypeError, ValueError, IndexError):
        when = None
    message_id = str(message.get("Message-ID") or "").strip() or None
    from_header = str(message.get("From") or "")
    return Header(
        uid=uid,
        message_id=message_id,
        sender_domain=sender_domain(from_header),
        subject=_decode_header(str(message.get("Subject") or "")),
        date=when,
        sender_name=parseaddr(_decode_header(from_header))[0],
        sender_local=_local_part(from_header),
    )


def _local_part(from_header: str | None) -> str:
    _, address = parseaddr(_decode_header(from_header))
    return address.rsplit("@", 1)[0].strip().lower() if "@" in address else ""


# --- BODYSTRUCTURE -----------------------------------------------------------


def _tokenize(data: bytes) -> list[Any]:
    """Parse an IMAP parenthesized list into nested Python lists."""
    stack: list[list[Any]] = [[]]
    i = 0
    n = len(data)
    while i < n:
        c = data[i : i + 1]
        if c == b"(":
            stack.append([])
            i += 1
        elif c == b")":
            if len(stack) == 1:  # end of the enclosing FETCH response
                break
            done = stack.pop()
            stack[-1].append(done)
            i += 1
        elif c == b'"':
            j = i + 1
            buf = bytearray()
            while j < n and data[j : j + 1] != b'"':
                if data[j : j + 1] == b"\\" and j + 1 < n:
                    j += 1
                buf += data[j : j + 1]
                j += 1
            stack[-1].append(buf.decode("utf-8", "replace"))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and data[j : j + 1] not in (b" ", b"(", b")", b'"'):
                j += 1
            atom = data[i:j].decode("utf-8", "replace")
            stack[-1].append(None if atom.upper() == "NIL" else atom)
            i = j
    return stack[0]


@dataclass(slots=True)
class TextPart:
    """A text part to fetch: IMAP part number, subtype, charset, encoding."""

    section: str
    subtype: str
    charset: str
    encoding: str
    size: int


def _params(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    items = [str(v) for v in value]
    return {items[i].lower(): items[i + 1] for i in range(0, len(items) - 1, 2)}


def text_parts(structure: Any, prefix: str = "") -> list[TextPart]:
    """text/plain and text/html parts of a BODYSTRUCTURE, attachments skipped."""
    if not isinstance(structure, list) or not structure:
        return []
    if isinstance(structure[0], list):  # multipart: children, then subtype
        parts: list[TextPart] = []
        for index, child in enumerate(
            (c for c in structure if isinstance(c, list)), start=1
        ):
            section = f"{prefix}.{index}" if prefix else str(index)
            parts.extend(text_parts(child, section))
        return parts
    section = prefix or "1"
    maintype = str(structure[0] or "").lower()
    subtype = str(structure[1] or "").lower() if len(structure) > 1 else ""
    if maintype != "text" or subtype not in ("plain", "html"):
        return []
    # text parts: type subtype params id desc encoding size lines [md5 disp ...]
    disposition = structure[9] if len(structure) > 9 else None
    if isinstance(disposition, list) and str(disposition[0] or "").lower() == (
        "attachment"
    ):
        return []
    params = _params(structure[2] if len(structure) > 2 else None)
    if "name" in params:  # a named text file is an attachment in all but name
        return []
    try:
        size = int(structure[6]) if len(structure) > 6 else 0
    except (TypeError, ValueError):
        size = 0
    return [
        TextPart(
            section=section,
            subtype=subtype,
            charset=params.get("charset", "utf-8"),
            encoding=str(structure[5] or "7bit").lower() if len(structure) > 5 else "",
            size=size,
        )
    ]


def decode_part(raw: bytes, part: TextPart) -> str:
    """Undo the transfer encoding and charset of a fetched part."""
    data = raw
    try:
        if part.encoding == "base64":
            data = base64.b64decode(re.sub(rb"[^A-Za-z0-9+/=]", b"", raw) + b"===")
        elif part.encoding == "quoted-printable":
            data = quopri.decodestring(raw)
    except (ValueError, TypeError):
        data = raw
    try:
        return data.decode(part.charset or "utf-8", errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


# --- session -----------------------------------------------------------------


class MailboxSession:
    """One connected, read-only (EXAMINE) session."""

    def __init__(self, client: Any, account: Account) -> None:
        """Initialize."""
        self.client = client
        self.account = account
        self.uidvalidity: int | None = None
        self.uidnext: int | None = None

    async def search(self, *criteria: str) -> list[int]:
        """UID SEARCH; returns UIDs (never fetches anything)."""
        response = await self.client.uid_search(*criteria, charset=None)
        if not _ok(response):
            raise CannotConnect("search failed")
        uids: list[int] = []
        for line in response.lines:
            text = bytes(line).decode("ascii", "replace")
            if text.upper().startswith("SEARCH"):
                text = text[6:]
            for token in text.split():
                if token.isdigit():
                    uids.append(int(token))
        return sorted(set(uids))

    async def search_senders(
        self, domains: list[str], days: int, keywords: list[str]
    ) -> list[int]:
        """Messages from these sender domains in the last ``days`` days."""
        if not domains:
            return []
        if self.account.is_gmail:
            query = gmail_query(domains, days, keywords)
            return await self.search("X-GM-RAW", imap_quote(query))
        since = imap_date(date.today() - timedelta(days=max(1, days)))
        found: set[int] = set()
        for domain in domains:
            found.update(await self.search("SINCE", since, "FROM", imap_quote(domain)))
        return sorted(found)

    async def search_shopify(
        self,
        names: list[str],
        days: int,
        keywords: list[str],
        ids: list[str] | None = None,
    ) -> list[int]:
        """Shopify mail that may be from these shops (headers decide later)."""
        if not names and not ids:
            return []
        if self.account.is_gmail:
            query = gmail_shopify_query(names, days, keywords, ids)
            return await self.search("X-GM-RAW", imap_quote(query))
        since = imap_date(date.today() - timedelta(days=max(1, days)))
        return await self.search("SINCE", since, "FROM", imap_quote(SHOPIFY_DOMAIN))

    async def fetch_headers(self, uids: list[int]) -> list[Header]:
        """Headers only (FROM, SUBJECT, DATE, MESSAGE-ID), read-only PEEK."""
        headers: list[Header] = []
        for start in range(0, len(uids), FETCH_CHUNK):
            chunk = uids[start : start + FETCH_CHUNK]
            response = await self.client.uid(
                "fetch",
                ",".join(str(u) for u in chunk),
                f"(UID BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])",
            )
            if not _ok(response):
                raise CannotConnect("fetch failed")
            for line, literal in _literals(response.lines):
                match = _UID.search(line)
                if match:
                    headers.append(parse_headers(int(match.group(1)), literal))
        return headers

    async def fetch_text(self, uid: int) -> tuple[str, str]:
        """(plain text, html) of one message: text parts only, size capped."""
        response = await self.client.uid("fetch", str(uid), "(UID BODYSTRUCTURE)")
        if not _ok(response):
            raise CannotConnect("fetch failed")
        raw = b" ".join(
            bytes(line)
            for line in response.lines
            if isinstance(line, bytes | bytearray)
        )
        start = raw.upper().find(b"BODYSTRUCTURE")
        if start < 0:
            return "", ""
        tree = _tokenize(raw[start + len(b"BODYSTRUCTURE") :])
        structure = tree[0] if tree and isinstance(tree[0], list) else []
        plain: list[str] = []
        html: list[str] = []
        budget = MAX_BODY_BYTES
        for part in text_parts(structure):
            if budget <= 0:
                break
            section = f"BODY.PEEK[{part.section}]<0.{budget}>"
            fetched = await self.client.uid("fetch", str(uid), f"(UID {section})")
            if not _ok(fetched):
                continue
            for _line, data in _literals(fetched.lines):
                literal = data[:budget]
                budget -= len(literal)
                text = decode_part(literal, part)
                (html if part.subtype == "html" else plain).append(text)
        return "\n".join(plain), "\n".join(html)


@asynccontextmanager
async def open_mailbox(
    account: Account, factory: Callable[[Account], Any] | None = None
) -> AsyncIterator[MailboxSession]:
    """Connect, log in, EXAMINE the folder (read-only); always log out."""
    from aioimaplib import AioImapException  # noqa: PLC0415

    client: Any = None
    _QUIET.active += 1
    try:
        try:
            client = (factory or _create_client)(account)
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await client.wait_hello_from_server()
        except (OSError, TimeoutError, AioImapException) as err:
            raise CannotConnect(type(err).__name__) from None
        try:
            login = await client.login(account.email, account.password)
        except (OSError, TimeoutError, AioImapException) as err:
            raise CannotConnect(type(err).__name__) from None
        if not _ok(login):
            raise AuthFailed("login rejected")
        try:
            examined = await client.examine(imap_quote(account.folder))
        except (OSError, TimeoutError, AioImapException) as err:
            raise CannotConnect(type(err).__name__) from None
        if not _ok(examined):
            raise FolderNotFound("folder not found")
        # aioimaplib only tracks SELECT; EXAMINE also enters the selected state.
        from aioimaplib import SELECTED  # noqa: PLC0415

        client.protocol.state = SELECTED
        session = MailboxSession(client, account)
        for line in examined.lines:
            if match := _UIDVALIDITY.search(bytes(line)):
                session.uidvalidity = int(match.group(1))
            if match := _UIDNEXT.search(bytes(line)):
                session.uidnext = int(match.group(1))
        try:
            yield session
        except (OSError, AioImapException) as err:
            raise CannotConnect(type(err).__name__) from None
    finally:
        if client is not None:
            try:
                await client.logout()
            except Exception:
                _LOGGER.debug("IMAP logout failed")
        _QUIET.active -= 1


async def test_login(
    account: Account, factory: Callable[[Account], Any] | None = None
) -> None:
    """Log in, open the folder read-only, log out (options flow Step A)."""
    async with open_mailbox(account, factory):
        return
