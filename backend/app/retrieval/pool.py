"""Process-wide limit held until each underlying synchronous call finishes."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from app.config import settings
from app.documents.validation import fail


class ProviderPool:
    def __init__(self, capacity):
        self.permits = threading.BoundedSemaphore(capacity)
        self.executor = ThreadPoolExecutor(
            max_workers=capacity, thread_name_prefix="retrieval-provider"
        )

    async def call(self, operation, *args, **kwargs):
        if not self.permits.acquire(blocking=False):
            raise fail(
                503, "PROVIDER_BUSY", "Search provider capacity is busy; retry shortly"
            )
        try:
            future = self.executor.submit(partial(operation, *args, **kwargs))
        except BaseException:
            self.permits.release()
            raise
        # asyncio cancellation cannot release this permit. The concurrent future
        # owns it even after the HTTP request/async wrapper has ended.
        future.add_done_callback(lambda _: self.permits.release())
        return await asyncio.wrap_future(future)

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)


provider_pool = ProviderPool(settings.retrieval_search_concurrency)


async def blocking_call(operation, *args, **kwargs):
    return await provider_pool.call(operation, *args, **kwargs)
