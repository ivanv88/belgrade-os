import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from shared.ntfy import NotifyService


async def test_send_posts_to_ntfy() -> None:
    service = NotifyService(topic="test_topic")
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await service.send("Hello", title="Test", priority="default")
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "test_topic" in str(call_args)


async def test_send_includes_title_and_priority() -> None:
    service = NotifyService(topic="alerts")
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await service.send("body text", title="Alert Title", priority="high")
        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Title") == "Alert Title"
        assert headers.get("Priority") == "high"


async def test_no_topic_skips_send() -> None:
    service = NotifyService(topic=None)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await service.send("ignored")
        mock_post.assert_not_called()
