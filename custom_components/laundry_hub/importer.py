"""Import garment details from a product page URL.

Fetches the page with HA's shared aiohttp session and extracts details from
JSON-LD ``Product`` data, then Open Graph / product meta tags, then Twitter
cards. Every host (page, redirect targets, images) passes an SSRF guard: after
DNS resolution, private, loopback, link-local, multicast, reserved and
unspecified addresses are refused, as are ``localhost`` and ``.local`` names.

DNS rebinding: each request is pinned to the addresses that passed the guard.
The request runs on a one-off connector whose resolver only knows that host
and those addresses, so aiohttp never resolves the name a second time. The
URL keeps its hostname, so the Host header, SNI and certificate check are
unchanged. Every redirect hop is checked and pinned again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from html import unescape as html_unescape
from html.parser import HTMLParser
import ipaddress
import json
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import client_context

from .const import (
    IMPORT_MAX_HTML,
    IMPORT_MAX_IMAGES,
    IMPORT_MAX_REDIRECTS,
    IMPORT_TIMEOUT,
    IMPORT_USER_AGENT,
    PHOTO_UPLOAD_MAX_BYTES,
)

_LOGGER = logging.getLogger(__name__)

BLOCKED = "This store blocks automatic imports — add photos with your camera instead."
TIMEOUT = "That page took too long to answer — try again, or add details by hand."
NOTHING = "Couldn't find product details on that page — add them by hand instead."
NOT_ALLOWED = "That address can't be imported (only public http or https pages)."
UNREACHABLE = "Couldn't reach that page — check the link and try again."


class ImportFailed(Exception):
    """A friendly, user-facing import failure."""

    def __init__(self, reason: str, key: str = "import_failed") -> None:
        """Initialize."""
        super().__init__(reason)
        self.reason = reason
        self.key = key


@dataclass(slots=True)
class ProductData:
    """What a page tells us about a product (all optional)."""

    name: str | None = None
    brand: str | None = None
    price: float | None = None
    currency: str | None = None
    color: str | None = None
    material: str | None = None
    size: str | None = None
    store: str | None = None
    url: str | None = None
    images: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        """True when nothing useful was found."""
        return not (self.name or self.images or self.price is not None or self.brand)


# --- SSRF guard ---------------------------------------------------------------


def _ip_allowed(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


async def _resolve(hass: HomeAssistant, host: str, port: int) -> list[str]:
    """Resolve a host to IP strings (patched in tests: no live DNS)."""
    infos = await hass.loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def resolve_checked(hass: HomeAssistant, url: str) -> tuple[str, list[str]]:
    """Check a URL; return (host, addresses that passed) or raise ImportFailed."""
    try:
        parts = urlsplit(url.strip())
    except ValueError as err:
        raise ImportFailed(NOT_ALLOWED, "import_not_allowed") from err
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ImportFailed(NOT_ALLOWED, "import_not_allowed")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise ImportFailed(NOT_ALLOWED, "import_not_allowed")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as err:
        raise ImportFailed(NOT_ALLOWED, "import_not_allowed") from err
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = await _resolve(hass, host, port)
        except (OSError, UnicodeError) as err:
            raise ImportFailed(UNREACHABLE, "import_unreachable") from err
    else:
        addresses = [host]
    if not addresses or not all(_ip_allowed(a) for a in addresses):
        raise ImportFailed(NOT_ALLOWED, "import_not_allowed")
    return host, list(dict.fromkeys(addresses))


async def check_url(hass: HomeAssistant, url: str) -> str:
    """Return the URL if it is a public http(s) URL, else raise ImportFailed."""
    await resolve_checked(hass, url)
    return url.strip()


class PinnedResolver(AbstractResolver):
    """Resolver that only answers for one host, with pre-checked addresses."""

    def __init__(self, host: str, addresses: list[str]) -> None:
        """Initialize."""
        self.host = host
        self.addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """Return the pinned addresses; any other host is refused."""
        if host.rstrip(".").lower() != self.host:
            raise OSError(f"{host} is not the checked host")
        results: list[ResolveResult] = []
        for address in self.addresses:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
            results.append(
                {
                    "hostname": host,
                    "host": str(ip),
                    "port": port,
                    "family": socket.AF_INET6 if ip.version == 6 else socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        return results

    async def close(self) -> None:
        """Nothing to release."""


def _open_session(hass: HomeAssistant, host: str, addresses: list[str]) -> Any:
    """A one-off session pinned to the checked addresses (patched in tests)."""
    connector = aiohttp.TCPConnector(
        resolver=PinnedResolver(host, addresses),
        ssl=client_context(),
        use_dns_cache=False,
        limit=2,
    )
    return aiohttp.ClientSession(connector=connector)


# --- fetching -----------------------------------------------------------------


async def _get(
    hass: HomeAssistant, url: str, *, accept: str, max_bytes: int
) -> tuple[str, str, bytes]:
    """GET with manual, re-checked and re-pinned redirects.

    Returns (final url, content type, body).
    """
    headers = {"User-Agent": IMPORT_USER_AGENT, "Accept": accept}
    current = url
    for _ in range(IMPORT_MAX_REDIRECTS + 1):
        host, addresses = await resolve_checked(hass, current)
        session = _open_session(hass, host, addresses)
        try:
            async with asyncio.timeout(IMPORT_TIMEOUT):
                async with session.get(
                    current, headers=headers, allow_redirects=False
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise ImportFailed(UNREACHABLE, "import_unreachable")
                        current = urljoin(current, location)
                        continue
                    if resp.status in (401, 403, 429, 451):
                        raise ImportFailed(BLOCKED, "import_blocked")
                    if resp.status >= 400:
                        raise ImportFailed(UNREACHABLE, "import_unreachable")
                    content_type = resp.headers.get("Content-Type", "").lower()
                    body = await resp.content.read(max_bytes + 1)
                    return current, content_type, body[:max_bytes]
        except TimeoutError as err:
            raise ImportFailed(TIMEOUT, "import_timeout") from err
        except aiohttp.ClientError as err:
            raise ImportFailed(UNREACHABLE, "import_unreachable") from err
        finally:
            await _close(session)
    raise ImportFailed(UNREACHABLE, "import_unreachable")


async def _close(session: Any) -> None:
    close = getattr(session, "close", None)
    if close is not None and not getattr(session, "_laundry_shared", False):
        await close()


async def fetch_image(hass: HomeAssistant, url: str) -> bytes:
    """Download one image (public host, image/* content type, size-capped)."""
    _, content_type, body = await _get(
        hass, url, accept="image/*", max_bytes=PHOTO_UPLOAD_MAX_BYTES
    )
    if not content_type.startswith("image/"):
        raise ImportFailed("Not an image", "import_not_image")
    return body


async def fetch_product(hass: HomeAssistant, url: str) -> ProductData:
    """Fetch a product page and extract its details (no images downloaded)."""
    final_url, content_type, body = await _get(
        hass, url, accept="text/html,application/xhtml+xml", max_bytes=IMPORT_MAX_HTML
    )
    if content_type and "html" not in content_type and "xml" not in content_type:
        raise ImportFailed(NOTHING, "import_nothing")
    html = body.decode("utf-8", errors="replace")
    data = parse_product(html, final_url)
    if data.empty():
        raise ImportFailed(NOTHING, "import_nothing")
    return data


# --- parsing ------------------------------------------------------------------


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.json_ld: list[str] = []
        self.title = ""
        self.canonical: str | None = None
        self._in_ld = False
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (
                a.get("property") or a.get("name") or a.get("itemprop") or ""
            ).lower()
            if key and a.get("content"):
                self.meta.setdefault(key, []).append(a["content"].strip())
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld, self._buf = True, []
        elif tag == "title":
            self._in_title = True
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href") or None

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self.json_ld.append("".join(self._buf))
            self._in_ld = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data


def _types(node: dict[str, Any]) -> list[str]:
    t = node.get("@type")
    return [str(x).lower() for x in (t if isinstance(t, list) else [t])]


def _walk(node: Any) -> list[dict[str, Any]]:
    """Every dict in a JSON-LD document (handles @graph and nesting)."""
    found: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            found.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str | dict)), None)
        return _text(value)
    if isinstance(value, str | int | float) and str(value).strip():
        # JSON-LD inside <script> is raw text; stores often HTML-escape it.
        return html_unescape(str(value)).strip()
    return None


def _price(value: Any) -> float | None:
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _images(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        url = (
            item.get("url") or item.get("contentUrl")
            if isinstance(item, dict)
            else item
        )
        if isinstance(url, str) and url.strip():
            out.append(url.strip())
    return out


def _from_json_ld(blocks: list[str], data: ProductData) -> None:
    for block in blocks:
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        for node in _walk(doc):
            if "product" not in _types(node) and "productgroup" not in _types(node):
                continue
            data.name = data.name or _text(node.get("name"))
            data.brand = data.brand or _text(node.get("brand"))
            data.color = data.color or _text(node.get("color"))
            data.material = data.material or _text(node.get("material"))
            data.size = data.size or _text(node.get("size"))
            data.images.extend(_images(node.get("image")))
            offers = node.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                price = offer.get("price", offer.get("lowPrice"))
                spec = offer.get("priceSpecification")
                if price is None and isinstance(spec, dict):
                    price = spec.get("price")
                if data.price is None and _price(price) is not None:
                    data.price = _price(price)
                    data.currency = data.currency or _text(offer.get("priceCurrency"))
            if data.name:
                return


def parse_product(html: str, page_url: str) -> ProductData:
    """Extract product details from HTML (JSON-LD, then OG, then Twitter)."""
    parser = _MetaParser()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError):  # pragma: no cover - parser is lenient
        pass
    data = ProductData()
    _from_json_ld(parser.json_ld, data)
    meta = parser.meta

    def first(*keys: str) -> str | None:
        for key in keys:
            if meta.get(key):
                return meta[key][0]
        return None

    data.name = data.name or first("og:title", "twitter:title")
    if data.price is None:
        data.price = _price(first("product:price:amount", "og:price:amount"))
        data.currency = data.currency or first(
            "product:price:currency", "og:price:currency"
        )
    data.brand = data.brand or first("product:brand", "og:brand")
    data.color = data.color or first("product:color")
    data.images += meta.get("og:image", []) + meta.get("og:image:secure_url", [])
    data.images += meta.get("twitter:image", []) + meta.get("twitter:image:src", [])
    data.store = first("og:site_name") or urlsplit(page_url).hostname
    canonical = first("og:url") or parser.canonical
    data.url = urljoin(page_url, canonical) if canonical else page_url
    seen: list[str] = []
    for image in data.images:
        absolute = urljoin(page_url, image)
        if urlsplit(absolute).scheme in ("http", "https") and absolute not in seen:
            seen.append(absolute)
    data.images = seen[: IMPORT_MAX_IMAGES * 2]  # extras in case some fail
    if data.name:
        data.name = " ".join(data.name.split())[:100]
    return data
