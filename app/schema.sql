PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS canvas (
    id TEXT PRIMARY KEY,
    width INTEGER NOT NULL DEFAULT 1600,
    height INTEGER NOT NULL DEFAULT 1000,
    background TEXT NOT NULL DEFAULT '#FFFFFF',
    stroke_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stroke (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canvas_id TEXT NOT NULL REFERENCES canvas(id),
    seq INTEGER NOT NULL,
    client_id TEXT NOT NULL,
    author_name TEXT,
    color TEXT NOT NULL,
    width REAL NOT NULL,
    points TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (canvas_id, seq),
    UNIQUE (canvas_id, client_id)
);

CREATE TABLE IF NOT EXISTS metric (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TRIGGER IF NOT EXISTS stroke_no_update BEFORE UPDATE ON stroke
    BEGIN SELECT RAISE(ABORT, 'strokes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stroke_no_delete BEFORE DELETE ON stroke
    BEGIN SELECT RAISE(ABORT, 'strokes are immutable'); END;
