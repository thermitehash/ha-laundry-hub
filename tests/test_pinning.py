"""DNS rebinding: outbound fetches are pinned to the address that passed the guard."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import web
from homeassistant.core import HomeAssistant
import pytest

from custom_components.laundry_hub import importer
from custom_components.laundry_hub.importer import ImportFailed, PinnedResolver


class _Resp:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self.content = self

    async def read(self, n: int) -> bytes:
        return self._body[:n]

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    """Fake session that records the resolver it was pinned to."""

    def __init__(self, pins: list[tuple[str, list[str]]], replies: list[_Resp]) -> None:
        self.pins = pins
        self.replies = replies
        self.closed = 0

    def get(self, url: str, **kwargs: Any) -> _Resp:
        return self.replies.pop(0)

    async def close(self) -> None:
        self.closed += 1


@pytest.mark.pinned_session
async def test_rebinding_uses_first_checked_address(hass: HomeAssistant) -> None:
    """Resolver says public first, private second: the private one is never used."""
    resolve = AsyncMock(side_effect=[["93.184.216.34"], ["10.0.0.5"]])
    pins: list[tuple[str, list[str]]] = []
    session = _Session(pins, [_Resp(200, {"Content-Type": "image/jpeg"}, b"img")])

    def open_session(hass: HomeAssistant, host: str, addresses: list[str]) -> Any:
        pins.append((host, addresses))
        return session

    with (
        patch.object(importer, "_resolve", resolve),
        patch.object(importer, "_open_session", open_session),
    ):
        assert (
            await importer.fetch_image(hass, "https://cdn.example.com/a.jpg") == b"img"
        )
    assert resolve.await_count == 1  # never resolved a second time
    assert pins == [("cdn.example.com", ["93.184.216.34"])]
    assert session.closed == 1
    # The connector's resolver can only ever return the checked address.
    answer = await PinnedResolver(*pins[0]).resolve("cdn.example.com", 443)
    assert [r["host"] for r in answer] == ["93.184.216.34"]


@pytest.mark.pinned_session
async def test_every_redirect_hop_is_rechecked_and_repinned(
    hass: HomeAssistant,
) -> None:
    resolve = AsyncMock(
        side_effect=[["93.184.216.34"], ["93.184.216.35"], ["10.0.0.5"]]
    )
    pins: list[tuple[str, list[str]]] = []
    replies = [
        _Resp(302, {"Location": "https://b.example.com/x"}),
        _Resp(302, {"Location": "https://c.example.com/y"}),
    ]
    session = _Session(pins, replies)

    def open_session(hass: HomeAssistant, host: str, addresses: list[str]) -> Any:
        pins.append((host, addresses))
        return session

    with (
        patch.object(importer, "_resolve", resolve),
        patch.object(importer, "_open_session", open_session),
        pytest.raises(ImportFailed) as err,
    ):
        await importer.fetch_image(hass, "https://a.example.com/")
    assert err.value.key == "import_not_allowed"  # third hop resolved private
    assert pins == [
        ("a.example.com", ["93.184.216.34"]),
        ("b.example.com", ["93.184.216.35"]),
    ]
    assert session.closed == 2


async def test_pinned_resolver_refuses_other_hosts() -> None:
    resolver = PinnedResolver("shop.example.com", ["93.184.216.34", "2606:2800::1"])
    answer = await resolver.resolve("SHOP.example.com.", 443)
    assert [r["host"] for r in answer] == ["93.184.216.34", "2606:2800::1"]
    assert answer[1]["family"].name == "AF_INET6"
    with pytest.raises(OSError):
        await resolver.resolve("evil.example.com", 443)
    await resolver.close()


@pytest.mark.pinned_session
async def test_real_pinned_session_connects_to_pin_with_original_host(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    """A real aiohttp request to an unresolvable name reaches the pinned address."""
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen["host"] = request.headers["Host"]
        return web.Response(body=b"ok", content_type="image/png")

    app = web.Application()
    app.router.add_get("/a.png", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # noqa: SLF001
    try:
        with (
            patch.object(importer, "_resolve", AsyncMock(return_value=["127.0.0.1"])),
            patch.object(importer, "_ip_allowed", lambda _a: True),
        ):
            body = await importer.fetch_image(
                hass, f"http://pinned.invalid:{port}/a.png"
            )
    finally:
        await runner.cleanup()
    assert body == b"ok"
    assert seen["host"] == f"pinned.invalid:{port}"
