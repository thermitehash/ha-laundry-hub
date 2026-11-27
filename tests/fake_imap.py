"""A fake aioimaplib IMAP4_SSL client backed by recorded .eml files.

It answers the commands Laundry Hub sends (LOGIN, EXAMINE, UID SEARCH with
X-GM-RAW or SINCE/FROM, UID FETCH of header fields, BODYSTRUCTURE and
BODY.PEEK[part]) in aioimaplib's response format, and records every command
so tests can assert what was (and wasn't) fetched. Like real servers, FROM
and Gmail ``from:`` match by substring, so look-alike senders come back from
the search and must be rejected by Laundry Hub's own header check.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime, timedelta
import email
from email.message import Message
from email.policy import compat32
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
import re
from typing import Any

Response = namedtuple("Response", "result lines")
FIXTURES = Path(__file__).parent / "fixtures" / "email"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def load(name: str) -> bytes:
    """A recorded email."""
    return (FIXTURES / name).read_bytes()


class _Protocol:
    def __init__(self) -> None:
        self.state = "NONAUTH"


def _q(value: str | None) -> str:
    if value is None:
        return "NIL"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _structure(part: Message) -> str:
    if part.is_multipart():
        children = "".join(_structure(p) for p in part.get_payload())  # type: ignore[union-attr]
        return f"({children} {_q(part.get_content_subtype())})"
    maintype, subtype = part.get_content_maintype(), part.get_content_subtype()
    params = [f"{_q(k)} {_q(v)}" for k, v in part.get_params()[1:]]  # type: ignore[index]
    raw = _raw(part)
    encoding = part.get("Content-Transfer-Encoding", "7bit")
    disposition = part.get_content_disposition()
    disp = "NIL"
    if disposition:
        filename = part.get_filename()
        disp = f"({_q(disposition)} {('(' + _q('filename') + ' ' + _q(filename) + ')') if filename else 'NIL'})"
    plist = f"({' '.join(params)})" if params else "NIL"
    lines = raw.count(b"\n") if maintype == "text" else None
    base = f"{_q(maintype)} {_q(subtype)} {plist} NIL NIL {_q(encoding)} {len(raw)}"
    if lines is not None:
        return f"({base} {lines} NIL {disp} NIL NIL)"
    return f"({base} NIL {disp} NIL NIL)"


def _raw(part: Message) -> bytes:
    payload = part.get_payload(decode=False)
    return str(payload).encode() if not isinstance(payload, bytes) else payload


def _section(message: Message, section: str) -> Message:
    part = message
    for index in section.split("."):
        if part.is_multipart():
            part = part.get_payload()[int(index) - 1]  # type: ignore[index]
        elif index != "1":
            raise KeyError(section)
    return part


class FakeImap:
    """One fake mailbox (shared by every client the factory creates)."""

    def __init__(
        self,
        messages: dict[int, bytes] | None = None,
        *,
        password: str = "app-password-1234",
        folder: str = "[Gmail]/All Mail",
        uidvalidity: int = 7,
        uidnext: int | None = None,
        fail_connect: bool = False,
    ) -> None:
        """Initialize."""
        self.messages = dict(messages or {})
        self.password = password
        self.folder = folder
        self.uidvalidity = uidvalidity
        self.uidnext = uidnext
        self.fail_connect = fail_connect
        self.commands: list[tuple[Any, ...]] = []
        self.body_fetches: list[tuple[int, str]] = []
        self.header_fetches: list[int] = []
        self.logins = 0
        self.logouts = 0

    # The factory Laundry Hub calls instead of aioimaplib.IMAP4_SSL(...)
    def __call__(self, account: Any) -> FakeImap:
        """Return a connected client (self)."""
        self.protocol = _Protocol()
        return self

    async def wait_hello_from_server(self) -> None:
        """Server greeting."""
        if self.fail_connect:
            raise OSError("connection refused")
        self.protocol.state = "NONAUTH"

    async def login(self, user: str, password: str) -> Response:
        """LOGIN."""
        self.commands.append(("LOGIN", user))
        self.logins += 1
        if password != self.password:
            return Response("NO", [b"[AUTHENTICATIONFAILED] Invalid credentials"])
        self.protocol.state = "AUTH"
        return Response("OK", [b"CAPABILITY IMAP4rev1 X-GM-EXT-1", b"Success"])

    async def examine(self, folder: str) -> Response:
        """EXAMINE (read-only)."""
        self.commands.append(("EXAMINE", folder))
        if folder.strip('"') != self.folder:
            return Response("NO", [b"[NONEXISTENT] Unknown Mailbox"])
        uidnext = self.uidnext or (max(self.messages, default=0) + 1)
        return Response(
            "OK",
            [
                f"{len(self.messages)} EXISTS".encode(),
                f"OK [UIDVALIDITY {self.uidvalidity}] UIDs valid.".encode(),
                f"OK [UIDNEXT {uidnext}] Predicted next UID.".encode(),
                b"[READ-ONLY] Success",
            ],
        )

    async def select(self, folder: str) -> Response:  # pragma: no cover - must not run
        """SELECT would allow changes; Laundry Hub must never call it."""
        self.commands.append(("SELECT", folder))
        return Response("OK", [])

    async def logout(self) -> Response:
        """LOGOUT."""
        self.commands.append(("LOGOUT",))
        self.logouts += 1
        return Response("OK", [])

    # --- searching -----------------------------------------------------------

    def _parsed(self, uid: int) -> Message:
        return email.message_from_bytes(self.messages[uid], policy=compat32)

    def _from(self, uid: int) -> str:
        return parseaddr(str(self._parsed(uid)["From"]))[1].lower()

    def _date(self, uid: int) -> datetime:
        return parsedate_to_datetime(str(self._parsed(uid)["Date"]))

    def _subject(self, uid: int) -> str:
        return str(self._parsed(uid)["Subject"]).lower()

    async def uid_search(
        self, *criteria: str, charset: str | None = "utf-8"
    ) -> Response:
        """UID SEARCH (X-GM-RAW, or SINCE + FROM)."""
        self.commands.append(("UID SEARCH", *criteria))
        uids = sorted(self.messages)
        args = list(criteria)
        if args and args[0] == "X-GM-RAW":
            query = args[1].strip('"').replace('\\"', '"')
            senders = re.search(r"from:\(([^)]*)\)", query)
            days = re.search(r"newer_than:(\d+)d", query)
            subject = re.search(r"subject:\((.*)\)", query)
            domains = senders.group(1).split(" OR ") if senders else []
            words = (
                [w.strip('"').lower() for w in re.split(r" OR ", subject.group(1))]
                if subject
                else []
            )
            cutoff = NOW - timedelta(days=int(days.group(1))) if days else None
            uids = [
                u
                for u in uids
                if any(d in self._from(u) for d in domains)
                and (cutoff is None or self._date(u) >= cutoff)
                and (not words or any(w in self._subject(u) for w in words))
            ]
        else:
            while args:
                key = args.pop(0)
                value = args.pop(0).strip('"')
                if key == "FROM":
                    uids = [u for u in uids if value.lower() in self._from(u)]
                elif key == "SINCE":
                    since = datetime.strptime(value, "%d-%b-%Y").replace(tzinfo=UTC)
                    uids = [u for u in uids if self._date(u) >= since]
        return Response(
            "OK", [" ".join(str(u) for u in uids).encode(), b"SEARCH completed"]
        )

    # --- fetching ------------------------------------------------------------

    async def uid(self, command: str, *criteria: str) -> Response:
        """UID FETCH of header fields, BODYSTRUCTURE or a body part."""
        assert command.lower() == "fetch", command  # never STORE/COPY/MOVE
        message_set, parts = criteria
        self.commands.append(("UID FETCH", message_set, parts))
        lines: list[Any] = []
        uids = [int(u) for u in message_set.split(",")]
        for seq, uid in enumerate(uids, start=1):
            if uid not in self.messages:
                continue
            if "HEADER.FIELDS" in parts:
                self.header_fetches.append(uid)
                message = self._parsed(uid)
                header = (
                    "".join(
                        f"{k}: {message[k]}\r\n"
                        for k in ("From", "Subject", "Date", "Message-ID")
                        if message[k]
                    ).encode()
                    + b"\r\n"
                )
                lines += [
                    f"{seq} FETCH (UID {uid} BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] {{{len(header)}}}".encode(),
                    bytearray(header),
                    b")",
                ]
            elif "BODYSTRUCTURE" in parts:
                structure = _structure(self._parsed(uid))
                lines.append(
                    f"{seq} FETCH (UID {uid} BODYSTRUCTURE {structure})".encode()
                )
            else:
                match = re.search(r"BODY\.PEEK\[([\d.]+)\]<0\.(\d+)>", parts)
                assert match, parts
                section, limit = match.group(1), int(match.group(2))
                self.body_fetches.append((uid, section))
                data = _raw(_section(self._parsed(uid), section))[:limit]
                lines += [
                    f"{seq} FETCH (UID {uid} BODY[{section}]<0> {{{len(data)}}}".encode(),
                    bytearray(data),
                    b")",
                ]
        lines.append(b"Success")
        return Response("OK", lines)
