import asyncio
import logging
from typing import Optional

from config import Config, load_config
from gen import belgrade_os_pb2
from redis_client import RedisClient
from providers.base import InferenceProvider
from providers.anthropic import AnthropicProvider
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from worker import process_task, CONSUMER_GROUP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _build_provider(cfg: Config) -> InferenceProvider:
    if cfg.provider == "anthropic":
        return AnthropicProvider(
            api_key=cfg.anthropic_api_key,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
        )
    if cfg.provider == "gemini":
        return GeminiProvider(
            api_key=cfg.google_api_key,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
        )
    if cfg.provider == "ollama":
        return OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
        )
    raise ValueError(f"Unknown provider: {cfg.provider}")


async def _consumer_loop(
    redis: RedisClient,
    provider: InferenceProvider,
    consumer_id: str,
) -> None:
    while True:
        result: Optional[tuple] = await redis.read_task(CONSUMER_GROUP, consumer_id)
        if result is None:
            continue
        msg_id, task_bytes = result
        task = belgrade_os_pb2.Task()
        task.ParseFromString(task_bytes)
        try:
            await process_task(task, redis, provider, consumer_id)
        except Exception:
            log.exception(
                "Unhandled error processing task task_id=%s", task.task_id
            )
        finally:
            await redis.ack_task(CONSUMER_GROUP, msg_id)


async def main() -> None:
    cfg = load_config()
    redis = RedisClient(cfg.redis_url)
    provider = _build_provider(cfg)
    await redis.ensure_consumer_groups()
    consumer_id = cfg.effective_consumer_id
    log.info(
        "inference controller starting provider=%s model=%s consumer=%s",
        cfg.provider,
        cfg.model,
        consumer_id,
    )
    await _consumer_loop(redis, provider, consumer_id)


if __name__ == "__main__":
    asyncio.run(main())
