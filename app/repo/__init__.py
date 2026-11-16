"""All application SQL, one module per table family, on one shared connection."""

import sqlite3

from app.repo.canvases import CanvasRepo
from app.repo.fates import FateRepo
from app.repo.metrics import MetricRepo
from app.repo.strokes import StrokeRepo


class Repository(CanvasRepo, StrokeRepo, FateRepo, MetricRepo):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection
