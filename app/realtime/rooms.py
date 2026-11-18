"""Chunk rooms: who is subscribed to which chunks of which canvas."""

from collections import Counter
from collections.abc import Callable

Chunk = tuple[int, int]


def room(canvas_id: str, chunk: Chunk) -> str:
    return f"{canvas_id}:{chunk[0]}:{chunk[1]}"


def session_room(session_id: str) -> str:
    """Every socket of one session: events meant for an author only."""
    return f"session:{session_id}"


class Rooms:
    def __init__(self) -> None:
        self.viewports: dict[str, tuple[str, frozenset[Chunk]]] = {}
        self.subscribers: Counter[tuple[str, Chunk]] = Counter()
        # Called with (canvas, chunk, has_subscribers) when a chunk gains its first
        # subscriber or loses its last one.
        self.on_change: Callable[[str, Chunk, bool], None] = lambda canvas, chunk, on: None

    def canvas_of(self, sid: str) -> str | None:
        viewport = self.viewports.get(sid)
        return viewport[0] if viewport else None

    def set_viewport(
        self, sid: str, canvas_id: str, chunks: frozenset[Chunk]
    ) -> tuple[list[str], list[str]]:
        """Replace a client's subscription. Returns (rooms to enter, rooms to leave)."""
        old_canvas, old_chunks = self.viewports.get(sid, (canvas_id, frozenset()))
        before = {(old_canvas, chunk) for chunk in old_chunks}
        after = {(canvas_id, chunk) for chunk in chunks}
        for key in before - after:
            self.subscribers[key] -= 1
            if not self.subscribers[key]:
                del self.subscribers[key]
                self.on_change(key[0], key[1], False)
        for key in after - before:
            self.subscribers[key] += 1
            if self.subscribers[key] == 1:
                self.on_change(key[0], key[1], True)
        self.viewports[sid] = (canvas_id, chunks)
        return (
            sorted(room(*key) for key in after - before),
            sorted(room(*key) for key in before - after),
        )

    def drop(self, sid: str) -> None:
        canvas_id = self.canvas_of(sid)
        if canvas_id is not None:
            self.set_viewport(sid, canvas_id, frozenset())
            del self.viewports[sid]

    def count(self, canvas_id: str, chunk: Chunk) -> int:
        return self.subscribers.get((canvas_id, chunk), 0)
