"""Perception: a spatial hash with 128 px cells over the active entities."""

from collections import defaultdict

import numpy as np

CELL = 128


class SpatialHash:
    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)

    def rebuild(self, indices: np.ndarray, x: np.ndarray, y: np.ndarray) -> None:
        self.cells.clear()
        cx = (x[indices] // CELL).astype(int).tolist()
        cy = (y[indices] // CELL).astype(int).tolist()
        cells = self.cells
        for index, key in zip(indices.tolist(), zip(cx, cy)):
            cells[key].append(index)

    def near(self, x: float, y: float, radius: float) -> list[int]:
        """Indices in the cells a circle touches (a superset; callers check distance)."""
        cells = self.cells
        found: list[int] = []
        for cy in range(int((y - radius) // CELL), int((y + radius) // CELL) + 1):
            for cx in range(int((x - radius) // CELL), int((x + radius) // CELL) + 1):
                bucket = cells.get((cx, cy))
                if bucket:
                    found.extend(bucket)
        return found
