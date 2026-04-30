from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from gen import belgrade_os_pb2
from drivers.ntfy import NtfyDriver
from drivers import get_driver
from config import Config


def _make_req(title="Goal reached", body="You hit your calorie target", priority=belgrade_os_pb2.NOTIFICATION_NORMAL, tags=None):
    req = belgrade_os_pb2.NotificationRequest()
    req.trace_id = "tr1"
    req.app_id = "nutrition"
    req.user_id = "ivan"
    req.title = title
    req.body = body
    req.priority = priority
    req.driver = "ntfy"
    req.tags.extend(tags or [])
    return req


async def test_ntfy_driver_posts_to_correct_url():
    driver = NtfyDriver(base_url="http://ntfy.local", topic="house")
    req = _make_req()

    with patch("drivers.ntfy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        await driver.send(req)

    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.await_args
    assert call_kwargs.args[0] == "http://ntfy.local/house"
    assert call_kwargs.kwargs["content"] == "You hit your calorie target"
    headers = call_kwargs.kwargs["headers"]
    assert headers["Title"] == "Goal reached"
    assert headers["Priority"] == "3"  # NOTIFICATION_NORMAL maps to ntfy default (3)


async def test_ntfy_driver_maps_low_priority():
    driver = NtfyDriver(base_url="http://ntfy.local", topic="house")
    req = _make_req(priority=belgrade_os_pb2.NOTIFICATION_LOW)

    with patch("drivers.ntfy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        await driver.send(req)

    headers = mock_client.post.await_args.kwargs["headers"]
    assert headers["Priority"] == "2"  # low


async def test_ntfy_driver_maps_high_priority():
    driver = NtfyDriver(base_url="http://ntfy.local", topic="house")
    req = _make_req(priority=belgrade_os_pb2.NOTIFICATION_HIGH)

    with patch("drivers.ntfy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        await driver.send(req)

    headers = mock_client.post.await_args.kwargs["headers"]
    assert headers["Priority"] == "5"  # urgent


async def test_ntfy_driver_includes_tags_header():
    driver = NtfyDriver(base_url="http://ntfy.local", topic="house")
    req = _make_req(tags=["shopping_cart", "warning"])

    with patch("drivers.ntfy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        await driver.send(req)

    headers = mock_client.post.await_args.kwargs["headers"]
    assert headers["Tags"] == "shopping_cart,warning"


async def test_ntfy_driver_omits_tags_header_when_empty():
    driver = NtfyDriver(base_url="http://ntfy.local", topic="house")
    req = _make_req(tags=[])

    with patch("drivers.ntfy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        await driver.send(req)

    headers = mock_client.post.await_args.kwargs["headers"]
    assert "Tags" not in headers


def test_get_driver_returns_ntfy_driver():
    cfg = Config(notification_driver="ntfy", ntfy_base_url="http://ntfy.local", ntfy_topic="house", _env_file=None)
    driver = get_driver(cfg)
    assert isinstance(driver, NtfyDriver)


def test_get_driver_raises_on_unknown():
    cfg = Config(notification_driver="unknown_driver", _env_file=None)
    with pytest.raises(ValueError, match="Unknown notification driver"):
        get_driver(cfg)
