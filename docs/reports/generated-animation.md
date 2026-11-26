# Generated animation v1: implementation verification

2026-11-24. Contract: [spec §4a](../spec.md#4a-text-directed-repeating-animation-choreography-v1).
Scope: [generated-animation.md](../generated-animation.md).

| Check | Result |
|---|---|
| Python 3.12: `env -u PYTHONPATH .venv/bin/python -m pytest -q` | 248 passed |
| Node 24: `cd web && npm test` | 48 passed |
| `cd web && npm run build` | passed |
| Docker frontend: `docker build --target frontend .` (Node 22) | passed, 48 client tests and build |
| Chromium: `cd web && E2E_CHROMIUM=/usr/bin/chromium npm run e2e` | 14 passed |
| Visual inspection | description, pause and repeated performance reviewed in two clients, EN and RU |

The server tests exercise strict model data, parallel tracks, explicit finite repeats,
configured limits, nonfinite values, world-edge rejection and shared Python/JS transform
fixtures. Mock HTTP tests check the generation request, output-token allowance, shared
budget and invalid responses. Pipeline tests check quota refunds, locks, immutable strokes,
deadlines and atomic creation after preparation. Simulation tests cover inert components,
all type indexes and nearby-target queries, inputs, sleep, freeze, chunk crossing,
repeated playback, the inter-cycle pause and persistence through the repository.

The browser scenarios use the real application pipeline and separate simulation process,
with a deterministic generation provider in `tests.browser_server`. They cover an author
and another viewer, a late viewer, page reload, a two-second final-pose pause followed by another play from
the fixed origin, canvas freeze, chunk sleep,
killing and recovering the simulation process, editable localized failures, switching
language while an error is visible, and an ordinary bomb exploding next to a scripted
object without harming it. Ordinary empty-description ALIVE scenarios also pass.

No real-provider request was made. These checks establish implementation behavior;
semantic accuracy, part-identification quality, real-model latency and cost remain a
separate budgeted verification. Continuous bounds are conservative and can reject a
performance near an edge that a tighter calculation could fit. Recovery retains the
existing allowance of up to five seconds of lost playback; disconnected clients stop
extrapolating after 300 ms.

## Separate part preparation and motion planning (2026-11-24)

Generation now uses image-only part identification (`parts.md` v1), deterministic
layer/pivot preparation, then text-only motion planning (`generate.md` v3). The motion
schema cannot redefine geometry. No part hierarchy was added. The author receives
a collapsible preview with actual layer bounds, pivots and bilingual part names;
the second viewer receives the synchronized performance without the preview.

| Check | Result |
|---|---|
| Python: `env -u PYTHONPATH .venv/bin/python -m pytest -q` | 258 passed |
| Node 24: `cd web && npm test` | 49 passed |
| Vite production build (through `npm run e2e`) | passed |
| Chromium: `cd web && E2E_CHROMIUM=/usr/bin/chromium npm run e2e` | 14 passed |
| Visual inspection | author previews in RU and EN, animated drawing in a second client reviewed |

New regressions cover a short arm box and an incorrect mid-arm pivot resolving to
the whole arm and shoulder, disconnected joints, geometry immutability during motion
planning, strict rejection of geometry in a motion response, separate text-model
configuration, shared budget exhaustion between stages, saved raw/prepared/motion
results, and intact strokes/refunded quota when motion fails after preparation.
Browser scenarios additionally check preview-before-spawn event order, author-only
delivery, part selection and language switching while the preview remains visible.

These checks use deterministic providers and mocked HTTP. No paid real-model request
was made for this change. Anatomical recognition and semantic motion quality remain
unverified with real providers; rectangle ownership and endpoint attachment remain
heuristics, especially for continuous outlines and overlapping parts. Two model stages
share the unchanged 25 s deadline, so real-provider latency also needs evaluation.

## Failures before the parts preview (2026-11-24)

A user reported a generation refusal before the Drawing parts panel appeared, with
no corresponding error in Docker logs. The exact failed job has not yet been read:
the initial local read-only database query could not open its assumed path. The
follow-up diagnostic in `development.md` uses `Settings.db_path` and lists the data
directory before querying; no original provider response has been obtained yet.

Two independent regressions were reproduced with failing tests and fixed:

- Expected AppError and timeout paths were silently converted to `alive:failed`.
  Every failed job now logs a WARNING with its ID, stage, error code and cause, and
  retains `failure:{stage,code,reason}` with completed stage results in the database.
  Provider schema failures identify invalid field paths/types; HTTP rejections show
  status and a bounded error message with the API key removed. Structured refusals
  include the provider's status/reason. Images and request text are not dumped.
- Joint preparation assumed the attachment was a stroke endpoint. A closed head
  started at its top was rejected even though the neck touched its lower contour.
  Closed outlines now offer their actual contour contact with body ink, using
  spatial cells for the proximity search. Open limbs retain endpoint candidates.
  `parts.md` v2 describes this distinction; no hierarchy or new dependency was added.

The new two-viewer browser scenario draws the closed head starting at its top,
generates a head-only movement, and checks a neck pivot, the author's preview and
the synchronized entity in both clients. The author and viewer screenshots were
reviewed. These checks use deterministic provider responses and do not establish
that the separately reported real-provider failure had the same cause.

Validation for the logging/closed-outline change: 263 server tests, 49 client tests
and 15 Chromium scenarios passed, including the new two-viewer closed-head case.

## Provider timeouts and motion HTTP 400 (2026-11-25)

The user subsequently supplied actionable logs: part identification timed out on
Gemini, and a separate job reached motion planning but received HTTP 400 twice from
Gemini before the shared time budget expired. These establish transport/time-budget
failures in the reported runs; the independently fixed contour bug was not shown to
be their cause.

The former 25 s job / 10 s HTTP limits were inherited from single-stage ordinary
classification. Generated animation now has its own configured 90 s shared job
deadline and 30 s HTTP attempts. Both stages and all retries still share one deadline
and daily budget, with the preparation reserve preserved. A nontransient HTTP 4xx
advances directly to the fallback instead of repeating an identical rejected
primary request. HTTP 408/429 and server errors retain transient retries.

Generation routing now requires support for the requested parameters. The motion
schema uses `minimum:1` for the strict integer repeat end, equivalent to `>0` without
the `exclusiveMinimum` keyword outside Gemini's documented subset. The exact cause
of the reported HTTP 400 remains unknown because the previous log retained only
OpenRouter's generic wrapper. New diagnostics extract the nested provider message
from `error.metadata.raw` JSON, with bounded output and API-key removal.

Regression tests reproduce a parts timeout followed by a motion HTTP 400 and
successful fallback, assert the primary is not retried after HTTP 400, preserve
transient retries, check separate HTTP budgets and one shared generated deadline,
and verify nested provider-error diagnostics. The browser fixture additionally
exercises the real adapter against mocked HTTP 400/success responses and checks
that both viewers see the resulting animation with one quota charge.

Provider references checked while implementing this change:
[OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
(`require_parameters`) and
[Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
(documented schema subset and inclusive numeric bounds). No paid provider request
was made in this environment; real-provider completion after these fixes still
requires confirmation.

| Final check | Result |
|---|---|
| Python: `env -u PYTHONPATH .venv/bin/python -m pytest -q` | 272 passed |
| Node: `cd web && npm test` | 49 passed |
| Vite production build (through `npm run e2e`) | passed |
| Chromium: `cd web && E2E_CHROMIUM=/usr/bin/chromium npm run e2e` | 16 passed |

The HTTP 400 browser regression passed with the author and another viewer. The full
run also retained ordinary ALIVE, localization, editable failures, playback/freeze/
sleep/recovery, quota, explosion, billing and legal-page coverage.

## Gemini schema state limit (2026-11-25)

The user confirmed a real generated character waving its arm as requested. Their
new logs also supplied the previously hidden Gemini explanation: "The specified
schema produces a constraint that has too many states for serving", naming large
nested array bounds as a typical cause. A separate `stage=parts` unsupported job
was also logged; that refused job cannot itself have spawned the successful entity.
The successful animation is consistent with the configured fallback path.

Motion's wire schema previously allowed up to 90 tracks, each with up to 256 keys,
plus eight repeat segments. Gemini's decoder need not enforce those upper bounds:
the application already validates all outputs with the full model and compiler.
The adapter now removes array `maxItems` from a fresh Gemini motion wire schema,
keeping required fields, object structure, enums and minimum key counts. Server
validation models, compiler limits and other providers' schemas are unchanged.

New regressions reject the original nested-bound schema in a mock provider, accept
the compact schema on the primary, verify no mutation of the full/Qwen schema,
and still reject oversized key/track/repeat responses. The two-viewer browser case
uses the real adapter and a mock endpoint that accepts only the compact Gemini
request, with no successful fallback available. Real Gemini acceptance after this
change still needs confirmation; no paid model calls were made here.

Validation: 277 server tests, 49 client tests and 17 Chromium scenarios passed;
the production frontend build and `git diff --check` also passed. The compact-schema
browser regression succeeded on the simulated primary with no successful fallback.

## Generated wheel stroke ownership (2026-11-25)

A user reported a wheel rotating a fragment of an unrelated line inside its
rectangle. The ordinary rig already reserves whole strokes for wheels, but the
generated-animation splitter assigned every sample inside any named part's rectangle.
The bug was reproduced with a long body line crossing the wheel and an unrelated
short line in the rectangle's corner; both leaked into the rotating layer.

Part identification now uses contract v2 (`drawing_parts_v2`, `parts.md` v3), with
explicit `kind:part|wheel`. Wheel ownership uses whole logical strokes: chunk-cut
pieces are rejoined for analysis, and at least 80% of sampled arc length must lie
inside the wheel ellipse with the ordinary rig's 1.25 tolerance. The original
strokes are assigned intact to the selected wheel. Wheels are excluded from the
rectangular splitting pass, leaving rejected body lines intact. Generic center
parts keep rectangular selection; the wheel rule does not infer kind from IDs or
translated labels. Wheel pivots must be center pivots.

Regressions cover crossing lines and corner fragments, original stroke preservation,
multiple raster scales/world origins, nonuniform sample density, a chunk cut that
would otherwise look like a spoke, generic non-wheel parts, pivot validation and
actual atlas transparency. The browser test draws a wheel with one spoke and a body
line across its top, checks both atlas ownership and visible body pixels, and sees
the rotating wheel in two clients. Author and viewer screenshots were reviewed.

Existing saved atlases are retained. Motion/canonical plan versions stay 1; only
new preparation uses the new part identification contract. Whole-stroke elliptical
ownership is a geometric heuristic: wholly internal unrelated lines and a wheel
joined to its body in one logical stroke can remain ambiguous. Real-model emission
of the new kind field was not evaluated with paid requests in this environment.

Validation: 285 server tests, 49 client tests and 18 Chromium scenarios passed,
including visible body-line pixels in both clients during wheel rotation. The
production frontend build and `git diff --check` passed as well.

## Invalid part geometry and same-job recovery (2026-11-25)

The user reported `stage=prepare code=ANIMATION_INVALID reason=invalid part geometry`
on one submission followed by successful generation on the next. That old message
does not identify the part, coordinate or whether validation failed before or after
atlas preparation, so the precise saved job cannot be diagnosed from the log alone.

Offline regressions found two defects in the rejection path. The provider adapter
validated the shape/types of drawing-parts JSON but left coordinate relationships
to preparation, outside its retry/fallback loop. An invalid `x + width`, box size or
pivot therefore ended the job immediately. In addition, the world/raster conversion
of a trimmed atlas layer can produce an edge of `1.0000000000000002`; strict `<=1`
validation rejected this valid layer too. A 22 px raster at scale `.137` reproduces
the latter with actual stroke rendering and atlas preparation.

Successful vision replies now run the shared part validator within the existing
provider attempts. Rejected geometry can recover through a retry or fallback in
the same ALIVE job, within the existing shared deadline and daily budget. Refused
statuses retain their semantics. Boundary checks allow only `1e-9` normalized
roundoff; nonpositive boxes and larger coordinate errors remain invalid. A typed
geometry error carries the part ID and offending field through provider logs and
final job diagnostics without logging labels, action text or response values.
`parts.md` v4 explicitly distinguishes width/height from right/bottom coordinates
and states all normalized bounds; drawing-parts contract version remains 2.

Server regressions cover invalid edges, size and pivots, provider fallback, recovery
after one bad response, exhaustion with preserved strokes/refunded quota, the daily
budget stop, and roundoff through atlas preparation and compilation. The existing
two-viewer wheel scenario now feeds invalid primary vision coordinates through the
real adapter with a mocked HTTP transport; only the fallback supplies valid geometry.
It checks one preview, one quota charge, no failed event, and a working wheel/body
in both clients after a single user submission. No paid provider calls were made.

Validation: 297 server tests, 49 client tests and all 18 Chromium scenarios passed.
The production frontend build and `git diff --check` passed. Author and viewer wheel
screenshots were reviewed after recovery through the mocked fallback.
