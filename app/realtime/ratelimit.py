"""In-memory sliding-window rate limiter, configured as "count/seconds"."""

import time
from collections import deque


class SlidingWindow:
    def __init__(self, spec: str) -> None:
        self.limit, self.seconds = map(int, spec.split("/"))
        self.buckets: dict[str, deque[float]] = {}
        self.last_sweep = 0.0

    def consume(self, key: str) -> bool:
        now = time.monotonic()
        if now - self.last_sweep >= self.seconds:
            self.buckets = {
                key: bucket
                for key, bucket in self.buckets.items()
                if bucket and bucket[-1] > now - self.seconds
            }
            self.last_sweep = now
        bucket = self.buckets.setdefault(key, deque())
        while bucket and bucket[0] <= now - self.seconds:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True
