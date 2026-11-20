# Load test (T25)

`scripts/loadtest.py --entities 2000 --clients 50 --seconds 60`, run 2026-11-18 21:24 on Darwin arm64, Python 3.12.7, one Uvicorn worker plus the simulation process.

- World: 1799 living entities, 1787 in active chunks; 48 chunks watched by 50 clients (4x3 chunks each).
- **Simulation tick** (the simulation reports p95 over each 5 s window; 12 windows): p50 16.4 ms; p95 median window 24.5 ms, worst window 310.8 ms; slowest tick 505.2 ms. Target p95 <= 60 ms: **met** (10 of 12 windows within it).
- Tick rate: 5 Hz at the end; ticks seen by clients 6.7/s (median). **Fell back to 5 Hz 2 time(s)**, back to full rate 1 time(s); 4 of 12 windows at full rate.
- world:delta messages per client: 7/s median (one per tick, holding every watched chunk that changed).
- **Bandwidth per client**: median 85 KiB/s, p95 90 KiB/s, max 91 KiB/s; all clients together 4.1 MiB/s. These are bytes after decompression; compressed on the wire about 24 KiB/s per client (estimated by re-compressing on 5 clients).
- **Control latency** (input to first delta showing the car move, 10 presses): median 114 ms, p95 190 ms, max 190 ms. Target <= 150 ms: **NOT met**.
- Socket round trip (an acknowledged no-op event, 223 samples): median 1 ms, p95 34 ms, max 948 ms.
- CPU: server 16% (median), simulation 11%; memory (resident, server and its children) up to 138 MiB.

## Notes

- **The host was busy.** This laptop (8 cores: 4 performance, 4 efficiency) had a load
  average of about 8.5 from other work during the run, and the load generator (50 clients
  parsing every message in one Python process) ran on it too. Both server processes stalled
  together for up to ~0.5–0.9 s a few times (the slowest tick against a 16 ms median, and the
  no-op round trip near 1 s at the same moments); that is the host, not the code. The 5 Hz
  fallback fired and recovered; the slow control presses fell inside those stalls.
- **A quieter 30 s run** on the same machine (with the short handles below): tick p50
  12.2 ms, p95 worst window 49 ms, 10 Hz throughout, control latency median 93 ms / p95
  107 ms, no-op round trip median 1 ms / p95 29 ms, server CPU 28 %, simulation 16 %,
  about 36 KiB/s per client compressed.
- Simulation alone (no server, no clients, 70 s of simulated time over the same world):
  15–19 ms per tick on average, flat over time.
- **Before batching** world:delta, main sent one message per watched chunk per client per
  tick (about 6 000 WebSocket sends a second at 50 clients) and spent most of its time in
  permessage-deflate and in socketio's recursive scan of every payload for binary data: the
  no-op round trip was 96–200 ms median, control latency 240–450 ms, server CPU 78–90 %.
  Now each client gets one world:delta per tick with all its chunks (each chunk is encoded
  once), and socketio's binary scan is off (we never send binary).
- Memory: a 30 s rerun peaked at about 140 MiB resident for the server and the simulation
  process together (macOS figures).
- Density: about 37 entities per chunk, 440 in a 4x3 viewport, all moving or wandering:
  more than twice the §7 "200 visible" figure. Bandwidth scales with what is visible.
- **Short entity handles (done):** delta rows used to start with the entity's 36-character
  UUID. Main now gives every entity a small number (`ref`, carried by every full copy of the
  entity) and rows and `gone` lists use it: a typical row went from about 74 to about 40
  bytes, and the UUIDs were what compression could not shrink. Positions keep 0.1 px and
  the tick rate is unchanged. Compressed traffic per client went from 44–92 KiB/s in the
  earlier runs to 24–36 KiB/s, and the server's CPU from about half a core to under a third.
- **Run it on the production VPS before launch** (it needs nothing but the repository and
  `.venv`; it never touches the real database):

```bash
env -u PYTHONPATH .venv/bin/python scripts/loadtest.py --report /tmp/loadtest.md
```
