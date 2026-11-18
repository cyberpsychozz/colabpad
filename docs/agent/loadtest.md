# Load test (T25)

`scripts/loadtest.py --entities 2000 --clients 50 --seconds 60`, run 2026-11-18 20:14 on Darwin arm64, Python 3.12.7, one Uvicorn worker plus the simulation process.

- World: 1782 living entities, 1769 in active chunks; 48 chunks watched by 50 clients (4x3 chunks each).
- **Simulation tick** (the simulation reports p95 over each 5 s window; 11 windows): p50 17.0 ms; p95 median window 27.5 ms, worst window 433.7 ms; slowest tick 816.9 ms. Target p95 <= 60 ms: **met** (9 of 11 windows within it).
- Tick rate: 10 Hz at the end; ticks seen by clients 6.5/s (median). **Fell back to 5 Hz 1 time(s)**, back to full rate 1 time(s); 4 of 11 windows at full rate.
- world:delta messages per client: 7/s median (one per tick, holding every watched chunk that changed).
- **Bandwidth per client**: median 144 KiB/s, p95 155 KiB/s, max 156 KiB/s; all clients together 7.0 MiB/s. These are bytes after decompression; compressed on the wire about 61 KiB/s per client (estimated by re-compressing on 5 clients).
- **Control latency** (input to first delta showing the car move, 10 presses): median 174 ms, p95 1756 ms, max 1756 ms. Target <= 150 ms: **NOT met**.
- Socket round trip (an acknowledged no-op event, 209 samples): median 1 ms, p95 88 ms, max 1272 ms.
- CPU: server 28% (median), simulation 10%.

## Notes

- **The host was busy.** This laptop (8 cores: 4 performance, 4 efficiency) had a load
  average of 6.5–10 from other work during the run, and the load generator (50 clients
  parsing every message in one Python process) ran on it too. Both server processes stalled
  together for up to ~0.8 s a few times (slowest tick 817 ms against a 17 ms median, and the
  no-op round trip at 1.3 s at the same moments); that is the host, not the code. The
  5 Hz fallback fired once and recovered. The slow control presses fell inside those stalls.
- **A quieter 30 s run** on the same machine (same seed): tick p50 15.3 ms, p95 worst window
  24.9 ms, 10 Hz throughout, control latency median 99 ms / p95 102 ms, no-op round trip
  median 3 ms / p95 49 ms, server CPU 52 %, simulation 15 %.
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
- **Possible next step if bandwidth matters:** rows carry a 36-character UUID each. A short
  per-entity handle assigned by main (rows `[h, x, y, st, anim, hp]`, a handle→id map on the
  client) would roughly halve the payload. Not done.
- **Run it on the production VPS before launch** (it needs nothing but the repository and
  `.venv`; it never touches the real database):

```bash
env -u PYTHONPATH .venv/bin/python scripts/loadtest.py --report /tmp/loadtest.md
```
