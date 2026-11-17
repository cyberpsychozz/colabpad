-- Fresh Living Canvas schema (AGENTS.md §5). ids are TEXT, times are unix ms,
-- JSON is TEXT. There is no migration from the legacy database.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_user (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT,
    locale TEXT CHECK (locale IN ('ru','en')),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES app_user(id),
    nickname TEXT,
    locale TEXT CHECK (locale IN ('ru','en')),
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER
);

CREATE TABLE IF NOT EXISTS subscription (
    id TEXT PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL REFERENCES app_user(id),
    plan TEXT CHECK (plan IN ('free','plus')),
    status TEXT CHECK (status IN ('active','past_due','canceled')),
    current_period_end INTEGER,
    yk_payment_method_id TEXT
);

CREATE TABLE IF NOT EXISTS payment (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_user(id),
    yk_payment_id TEXT UNIQUE NOT NULL,
    amount_kop INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT CHECK (status IN ('pending','succeeded','canceled')),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS canvas (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('world','private')),
    owner_user_id TEXT REFERENCES app_user(id),
    title TEXT,
    link_role TEXT NOT NULL CHECK (link_role IN ('none','viewer','player','editor')),
    frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0,1)),
    bounds_w INTEGER NOT NULL CHECK (bounds_w > 0),
    bounds_h INTEGER NOT NULL CHECK (bounds_h > 0),
    stroke_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    CHECK (kind = 'world' OR owner_user_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS canvas_member (
    canvas_id TEXT REFERENCES canvas(id),
    user_id TEXT REFERENCES app_user(id),
    role TEXT CHECK (role IN ('viewer','player','editor')),
    invited_by TEXT,
    created_at INTEGER,
    PRIMARY KEY (canvas_id, user_id)
);

CREATE TABLE IF NOT EXISTS quota (
    subject TEXT,
    day TEXT,
    alive_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, day)
);

-- Strokes: append-only and IMMUTABLE. Points are in world coordinates and every
-- stroke lies inside one chunk (the client splits strokes at chunk borders).
CREATE TABLE IF NOT EXISTS stroke (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canvas_id TEXT NOT NULL REFERENCES canvas(id),
    seq INTEGER NOT NULL,
    client_id TEXT NOT NULL,
    session_id TEXT,
    color TEXT NOT NULL,
    width REAL NOT NULL,
    points TEXT NOT NULL,
    chunk_x INTEGER NOT NULL,
    chunk_y INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (canvas_id, seq),
    UNIQUE (canvas_id, client_id)
);
CREATE INDEX IF NOT EXISTS stroke_chunk ON stroke (canvas_id, chunk_x, chunk_y, seq);

-- A stroke's fate: append-only and IMMUTABLE. Forward only:
-- (none) -> consumed -> fossil, or (none) -> destroyed.
-- Current state = latest fate for (canvas_id, seq); no row = static.
CREATE TABLE IF NOT EXISTS stroke_fate (
    canvas_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    fate TEXT NOT NULL CHECK (fate IN ('consumed','destroyed','fossil')),
    entity_id TEXT,
    at INTEGER NOT NULL,
    PRIMARY KEY (canvas_id, seq, fate),
    FOREIGN KEY (canvas_id, seq) REFERENCES stroke (canvas_id, seq)
);

CREATE TRIGGER IF NOT EXISTS stroke_no_update BEFORE UPDATE ON stroke
    BEGIN SELECT RAISE(ABORT, 'strokes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stroke_no_delete BEFORE DELETE ON stroke
    BEGIN SELECT RAISE(ABORT, 'strokes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stroke_fate_no_update BEFORE UPDATE ON stroke_fate
    BEGIN SELECT RAISE(ABORT, 'stroke fates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stroke_fate_no_delete BEFORE DELETE ON stroke_fate
    BEGIN SELECT RAISE(ABORT, 'stroke fates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stroke_fate_forward_only BEFORE INSERT ON stroke_fate
    BEGIN
        SELECT RAISE(ABORT, 'invalid stroke fate transition')
        WHERE (NEW.fate IN ('consumed', 'destroyed') AND EXISTS (
                SELECT 1 FROM stroke_fate
                WHERE canvas_id = NEW.canvas_id AND seq = NEW.seq))
           OR (NEW.fate = 'fossil' AND NOT EXISTS (
                SELECT 1 FROM stroke_fate
                WHERE canvas_id = NEW.canvas_id AND seq = NEW.seq AND fate = 'consumed'));
    END;

CREATE TABLE IF NOT EXISTS alive_job (
    id TEXT PRIMARY KEY,
    canvas_id TEXT,
    session_id TEXT,
    lasso TEXT,
    stroke_seqs TEXT,
    status TEXT CHECK (status IN ('queued','raster','classify','choose','rig','spawned','failed')),
    vlm_result TEXT,
    chosen_type TEXT,
    error_code TEXT,
    timings TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS sprite (
    id TEXT PRIMARY KEY,
    job_id TEXT UNIQUE,
    cutout_path TEXT,
    sheet_path TEXT,
    manifest TEXT
);

CREATE TABLE IF NOT EXISTS entity (
    id TEXT PRIMARY KEY,
    canvas_id TEXT,
    sprite_id TEXT,
    archetype TEXT,
    label TEXT,
    x REAL, y REAL, vx REAL, vy REAL,
    facing TEXT,
    state TEXT,
    hp REAL, hunger REAL, thirst REAL, energy REAL,
    params TEXT,
    origin_x REAL, origin_y REAL,
    death_x REAL, death_y REAL,
    parent_id TEXT,
    bound_to TEXT,
    summoner_session_id TEXT,
    born_at INTEGER,
    died_at INTEGER
);
CREATE INDEX IF NOT EXISTS entity_alive ON entity (canvas_id) WHERE died_at IS NULL;

CREATE TABLE IF NOT EXISTS world_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canvas_id TEXT,
    tick INTEGER,
    at INTEGER,
    type TEXT,
    actors TEXT,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS magic_token (
    token_hash TEXT PRIMARY KEY,
    email TEXT,
    locale TEXT,
    expires_at INTEGER,
    used_at INTEGER
);

CREATE TABLE IF NOT EXISTS metric (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

-- A separate credential lets sign-in rotate the cookie while retaining session ownership.
CREATE TABLE IF NOT EXISTS login_cookie (
    digest TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES session(id)
);
