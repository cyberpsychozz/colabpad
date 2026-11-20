# Scenario run (spec §12), 2026-11-18

Two clients in the built-in browser on a throwaway database (`127.0.0.1` and `localhost` give
two sessions), client A in EN then RU, client B in RU. The VLM key was blanked on purpose, so
every ALIVE went through the manual grid (§12.8). Seen working:

- strokes in both clients; goo in both (B's stages in Russian); the grid; the 20 s auto-pick
  (falls back to the scribble without a VLM); name tags in each client's own language;
- the ALIVE 5 s rate limit message; the 11th ALIVE → `QUOTA_EXCEEDED` with the sign-in hint,
  with the Russian plural («ещё 10 оживлений»), `<html lang>` and title following the locale;
- a car and a remote: B claims the remote, drives with held WASD (lease extends), A gets
  "Someone is driving — 49 s left", B lets go;
- a grenade: countdown visible, explosion after 20 s, the stroke 112 px from the centre of a
  118.5 px blast got `destroyed`, the one at 120 px stayed static;
- `kill -9` of the simulation process: restarted within a second, world carried on;
- sign-in (code from the log), Plus granted in the DB (no YooKassa test keys here),
  private world created, link access Player: the guest sees only the hand tool, can pet
  (and the server refuses `stroke:add` and ALIVE with `FORBIDDEN_ROLE`);
- subscription expired → both clients reload into the frozen world with `canvas.frozen` and
  the owner as viewer; reactivated → unfrozen (the minute sweep picked both up).

`docker compose up --build -d --wait` from a clean clone (own project name, port 8020): healthy,
`/w` served, built-in sprites baked with skia inside the image, simulation process running,
clean stop. It had been failing at `npm test` in the image (the catalog test reads
`app/errors.py` and `config/archetypes.yaml`); the Dockerfile now copies those two files.

Not walked: §12.2/3/4/7 (behaviour over minutes: unit-tested per ● cell), §12.10's payment
itself and §12.12's gate (no test shop keys, no ad blocker in the pane).

Fixed on the way: a guest without a name made "someone is driving" start in lower case (now a
whole sentence of its own), the quota hint had no plural forms, and the ALIVE panel shrank
into a narrow column near the right edge.
