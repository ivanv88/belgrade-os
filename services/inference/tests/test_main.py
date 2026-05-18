from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from config import Config
from gen import belgrade_os_pb2
from main import _build_provider, _consumer_loop
from worker import CONSUMER_GROUP


# ---------------------------------------------------------------------------
# Test 1: consumer loop calls process_task then acks
# ---------------------------------------------------------------------------


async def test_consumer_loop_calls_process_task_then_acks():
    task = belgrade_os_pb2.Task(
        task_id="t1", user_id="u1", prompt="test", trace_id="tr1"
    )
    task_bytes = task.SerializeToString()

    mock_redis = AsyncMock()

    async def fake_read(consumer_group, consumer_id):
        if fake_read.call_count == 1:
            return ("msg-001", task_bytes)
        await asyncio.sleep(9999)

    fake_read.call_count = 0

    async def _fake_read_wrapper(consumer_group, consumer_id):
        fake_read.call_count += 1
        return await fake_read(consumer_group, consumer_id)

    mock_redis.read_task = _fake_read_wrapper

    mock_provider = AsyncMock()

    with patch("main.process_task", new_callable=AsyncMock) as mock_process_task:
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_provider, "w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_process_task.assert_awaited_once()
    call_args = mock_process_task.await_args
    assert call_args.args[0].task_id == "t1"

    mock_redis.ack_task.assert_awaited_once_with(CONSUMER_GROUP, "msg-001")


# ---------------------------------------------------------------------------
# Test 2: consumer loop acks even after error
# ---------------------------------------------------------------------------


async def test_consumer_loop_acks_even_after_error():
    task = belgrade_os_pb2.Task(
        task_id="t2", user_id="u2", prompt="test", trace_id="tr2"
    )
    task_bytes = task.SerializeToString()

    mock_redis = AsyncMock()

    async def fake_read(consumer_group, consumer_id):
        if fake_read.call_count == 1:
            return ("msg-002", task_bytes)
        await asyncio.sleep(9999)

    fake_read.call_count = 0

    async def _fake_read_wrapper(consumer_group, consumer_id):
        fake_read.call_count += 1
        return await fake_read(consumer_group, consumer_id)

    mock_redis.read_task = _fake_read_wrapper

    mock_provider = AsyncMock()

    with patch("main.process_task", new_callable=AsyncMock) as mock_process_task:
        mock_process_task.side_effect = Exception("boom")
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_provider, "w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_task.assert_awaited_once_with(CONSUMER_GROUP, "msg-002")


# ---------------------------------------------------------------------------
# Test 3: consumer loop continues on None read (no ack)
# ---------------------------------------------------------------------------


async def test_consumer_loop_continues_on_none_read():
    mock_redis = AsyncMock()

    async def fake_read(consumer_group, consumer_id):
        if fake_read.call_count == 1:
            return None
        await asyncio.sleep(9999)

    fake_read.call_count = 0

    async def _fake_read_wrapper(consumer_group, consumer_id):
        fake_read.call_count += 1
        return await fake_read(consumer_group, consumer_id)

    mock_redis.read_task = _fake_read_wrapper

    mock_provider = AsyncMock()

    with patch("main.process_task", new_callable=AsyncMock):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_provider, "w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    assert mock_redis.ack_task.await_count == 0


# ---------------------------------------------------------------------------
# Test 4: _build_provider returns AnthropicProvider
# ---------------------------------------------------------------------------


def test_build_provider_anthropic():
    from providers.anthropic import AnthropicProvider

    cfg = Config(
        provider="anthropic",
        model="claude-opus-4-7",
        anthropic_api_key="k",
        _env_file=None,
    )
    assert isinstance(_build_provider(cfg), AnthropicProvider)


# ---------------------------------------------------------------------------
# Test 5: _build_provider returns GeminiProvider
# ---------------------------------------------------------------------------


def test_build_provider_gemini():
    from providers.gemini import GeminiProvider

    with patch("providers.gemini.genai"):
        cfg = Config(
            provider="gemini",
            model="gemini-1.5-pro",
            google_api_key="gk",
            _env_file=None,
        )
        assert isinstance(_build_provider(cfg), GeminiProvider)


# ---------------------------------------------------------------------------
# Test 6: _build_provider returns OllamaProvider
# ---------------------------------------------------------------------------


def test_build_provider_ollama():
    from providers.ollama import OllamaProvider

    cfg = Config(provider="ollama", model="qwen2.5-coder", _env_file=None)
    assert isinstance(_build_provider(cfg), OllamaProvider)
