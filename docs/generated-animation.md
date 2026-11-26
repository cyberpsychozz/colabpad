# Generated animation from a text description: v1 design

Status: implemented (v1). This document records the feature scope; the as-built
contract and interpolation rules are in `spec.md` §4a. Provider quality still requires
a separate budgeted real-model evaluation. Implementation checks and browser review:
[`reports/generated-animation.md`](reports/generated-animation.md).

## Scope

The author selects one drawing and describes what it should do, in Russian or English.
The model generates a finite animation for that drawing: motion through the canvas,
movement of its parts, timing, pauses and repetition within one performance. The server
repeats that complete performance indefinitely, with a two-second pause between plays.
Other drawings are not actors or targets. The animation has no gameplay effects.

Examples: a bird flaps its wings, follows a circle and lands; a star spins and bows;
a character waves one arm, jumps twice and stands still.

The existing ALIVE flow remains available when the description is empty. A nonempty
description explicitly requests generated animation and must not silently fall back to
the existing type animations.

## Player flow

1. Lasso one drawing. Enter an optional description next to ALIVE.
2. Submit the lasso and description together. The shared goo and job progress remain.
3. The server identifies and prepares the drawing's layers, shows the author their
   boxes and pivots, then obtains and validates the motion plan.
4. Only after preparation succeeds does the server consume the strokes and spawn the
   animated object. Every viewer sees the same performance.
5. At the end, the object holds its final position and pose for two seconds, then the
   whole performance restarts from the fixed origin and initial pose. This repeats
   indefinitely without another model call. Segment repeats inside each performance
   remain explicit and finite. The type's autonomous behaviour never starts.

If generation is unavailable, over budget, invalid, or cannot satisfy the description,
show a localized explanation and let the author edit and retry. The original strokes
stay static, locks are released and the ALIVE quota is refunded on service failure.
The existing unsafe-content policy still applies. Do not invent missing limbs or
silently ignore requests for other actors or gameplay effects.

## Model contract

Use the existing OpenRouter-compatible HTTP adapter, environment key, VLM-specific
proxy and daily budget. Extend the adapter with a separate typed generation operation
and versioned prompts. Keep the existing classification operation intact. Use two
sequential requests: identify whole movable parts from the image alone
(`parts.md` v4, drawing-parts contract v2), prepare their actual stroke layers and pivots, then compose movement
from the fixed geometry and action text only (`generate.md` v3). Geometry is absent
from the motion response schema. Vision and motion can use different configured
models. Both operations share budget, retries and a generated-job deadline of 90 s;
individual HTTP attempts allow 30 s. Ordinary ALIVE retains its existing 25 s limit.
HTTP 400 and other nontransient 4xx responses advance directly to the fallback model.
Successful vision replies also validate coordinate relationships within the same
retry/fallback loop. A bad rectangle or pivot can recover without another press
of ALIVE. Boundary comparisons tolerate `1e-9` normalized floating-point roundoff;
larger errors remain invalid and include the part and field in diagnostics.
Show the prepared image with part boxes and pivot crosses in a collapsible author-only
panel before starting the motion request, keeping it available during playback and
on later failures. There is no confirmation gate or part hierarchy.

The two outputs describe parts and a timeline, not executable Python or JavaScript:

- Identified parts have stable IDs, bilingual labels, normalized boxes, joint/center pivots, explicit part/wheel kinds and a drawing order. The server resolves pivots to the drawing before motion planning. Wheels own whole strokes mostly contained by their ellipse; crossing body lines are not cut into the wheel layer.
- Each track addresses the root, whole body or one identified part.
- Keyframes specify time and translation, rotation or scale, with a supported easing.
- Tracks share one timeline, so different parts can move simultaneously.
- Repetition is a bounded segment with a count. The server expands it to a canonical
  finite timeline before playback; the model supplies no dynamic loops or playback-time
  calls. The server repeats the whole validated timeline with an inter-cycle pause.
- A structured unsupported result carries an error code rendered through RU/EN
  catalogs, rather than arbitrary provider text shown to the player.

Root translation changes the object's world position. Body and part transforms are
visual offsets relative to that root: a jump can lift the body without changing its
ground position. Translation uses units relative to the drawing's width and height;
the server resolves those units once at compilation. Angles are degrees and times are
integer milliseconds. Define transform order and pivot coordinates in the contract,
and apply exactly the same rules in the Python compiler and JavaScript renderer.

This is generated choreography, not selection among the existing `idle`, `move` and
`act` animations. Low-level transform operations and interpolation are implemented
in code; the model supplies their values, timing and composition.

Configurable initial limits: description 1,000 characters, duration 30 s
including repeats, 16 movable parts, 256 keyframes after expansion, 8 repetitions,
64 KiB canonical plan, and root displacement within 512 px of the spawn point.
Reject nonfinite numbers, unknown part references, overlapping tracks writing the
same property, invalid times and transforms outside allowed ranges. Validate the
trajectory and transformed visual bounds against the world edges before consuming
strokes. Unsupported or invalid output must not partially execute.

## Drawing and rendering

Reuse rasterization and stroke splitting, extending named parts to support arbitrary
identified limbs such as left and right arms. Save a compact atlas containing the
body and part layers plus their pivots and placement. Preserve the user's lines.
Extend the sprite manifest with a versioned layered format; retain sprite sheets for
ordinary objects. The atlas is downloaded once, not once per animation frame.

The client evaluates the canonical tracks at the authoritative playback position and
composes the layers with Canvas 2D. The server evaluates the same root trajectory for
world position, chunk membership and subscriptions. Bounds and hit testing must
account for the generated transforms. No video generation or additional runtime
dependency is needed for this design.

## Simulation and synchronization

Use an explicit `scripted_visual` entity mode. Its classification can describe the
drawing, but must not enable the existing type's brain, needs, spawners, fuses, fire,
weather, reproduction or vehicle control. It also cannot be eaten, damaged, pushed,
dragged, used as a shelter or selected as a gameplay target by ordinary objects.
Its trajectory does not push, damage or block other entities. Visual overlap is
allowed. Population quotas and role checks still apply.

The simulation owns total active playback time, cycle and pause boundaries, and root
position. Pose time is `min(duration, elapsed % (duration + pauseMs))`; `pauseMs` defaults
to 2,000 in `config/vlm.yaml`. During the inter-cycle pause the final pose is held, and
`running` stays true until freeze or sleep stops the clock. Send the canonical plan once with the full entity; send small playback checkpoints with a
server time reference and revision through the normal world transport. Clients
interpolate between checkpoints and correct drift. Using each client's receipt time
as the start time would desynchronize viewers and is insufficient.

Store the validated plan, format/prompt version, atlas manifest, fixed origin and
playback state through the main process, the only DB writer. Existing JSON params
and data fields may be sufficient; verify before deciding on a schema migration.
Persist progress in the normal snapshots. Recovery may lose up to the existing
5-second snapshot interval, but must not rerun generation or restart at frame zero.

Pause playback while the canvas is frozen or the object's chunk sleeps, and resume
from the saved elapsed time on wake. Root coordinates remain relative to the stored
origin across saves and resumes. A new subscriber receives the current checkpoint,
not the beginning of the animation.

## Implementation and acceptance

Deliver as one complete feature, with these internal steps:

1. Define and test the versioned model schema, canonical plan compiler, limits and
   interpolation semantics, including bounded parallel motion and repetition.
2. Add generation with fake-provider tests, budget/deadline handling and failure paths.
3. Export part atlases and add layered rendering, with matching Python/JS fixtures.
4. Add the isolated simulation mode, playback state, persistence and synchronization.
5. Add the optional description field and localized progress/errors; update the
   as-built spec, development instructions and status with the implementation.

Required acceptance scenarios: one author generates a performance visible in two
browser clients; a late viewer joins midway; reload, freeze, chunk sleep and process
recovery preserve playback and the remaining inter-cycle pause; each performance holds
its final pose for two seconds and restarts from the fixed origin; ordinary entities do
not interact with the visual object in either direction. Provider failure, invalid
output and unsupported requests leave the drawing intact. Empty-description ALIVE
continues to work as before.

Run pytest, client unit tests and Playwright, and inspect the performance in two
browser clients. Real-provider quality is a separate, budgeted verification: fake
responses test the implementation but do not establish generation quality.
