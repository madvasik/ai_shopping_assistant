import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class InMemoryRateLimiter:
    """
    Ограничитель частоты в памяти (скользящее окно).
    Не подходит для нескольких инстансов без общего состояния.
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 300):
        self.max_requests = int(max_requests)
        self.window_seconds = int(window_seconds)
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> Tuple[bool, int]:
        """Возвращает (разрешено, секунд до следующей попытки при отказе)."""
        now = time.time()
        q = self._hits[key]

        cutoff = now - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()

        if len(q) >= self.max_requests:
            retry_after = int(max(1, (q[0] + self.window_seconds) - now))
            return False, retry_after

        q.append(now)
        return True, 0
