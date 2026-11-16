"""All application SQL, one module per table family, on one shared connection."""

import sqlite3

from app.config import Settings
from app.repo.canvases import CanvasRepo
from app.repo.metrics import MetricRepo
from app.repo.strokes import StrokeRepo


class Repository(CanvasRepo, StrokeRepo, MetricRepo):
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.db = connection
        self.settings = settings
