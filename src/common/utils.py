"""
공통 유틸리티
"""

import asyncio
import logging
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    coro_func: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> T:
    """비동기 함수 재시도 (exponential backoff)

    Args:
        coro_func: 재시도할 비동기 함수 (인자 없는 코루틴 팩토리)
        max_retries: 최대 시도 횟수
        base_delay: 기본 딜레이 (초). 2s, 4s, 8s 순으로 증가
    """
    for attempt in range(max_retries):
        try:
            return await coro_func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"재시도 {attempt + 1}/{max_retries} ({delay:.0f}초 후): {e}"
            )
            await asyncio.sleep(delay)
