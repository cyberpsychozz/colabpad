"""All application SQL, one module per table family, on one shared connection."""

import sqlite3

from app.repo.canvases import CanvasRepo
from app.repo.entities import EntityRepo
from app.repo.fates import FateRepo
from app.repo.jobs import JobRepo
from app.repo.metrics import MetricRepo
from app.repo.strokes import StrokeRepo
from app.repo.users import UserRepo


class Repository(
    CanvasRepo, StrokeRepo, FateRepo, MetricRepo, UserRepo, JobRepo, EntityRepo
):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection
