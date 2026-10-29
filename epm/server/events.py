"""Fan out session events to websocket subscribers from any thread."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, keep: int = 200):
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = {s for s in self._subscribers if s[1] is not queue}

    def publish(self, event: dict[str, Any]) -> None:
        event = {"at": time.time(), **event}
        self.recent.append(event)
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:  # the loop has closed
                self.unsubscribe(queue)


def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
    if queue.full():  # a stalled client loses old events rather than blocking everyone
        queue.get_nowait()
    queue.put_nowait(event)
