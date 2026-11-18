"""T7: how often the real VLM gets the test drawings right (target: >= 80% top-1).

    env -u PYTHONPATH .venv/bin/python scripts/vlm_accuracy.py [--report docs/agent/vlm-accuracy.md]
        [--only cow,wolf] [--sheets DIR] [--budget 2]

Each drawing in scripts/drawings.py is rasterized exactly as ALIVE does it, sent to the
VLM through the app's own adapter (primary model, retry, fallback) and post-processed
with config/labels.yaml. The key comes from VLM_API_KEY in .env; this costs real money,
about $0.004 a drawing, and stops at --budget dollars.

With --sheets, every drawing that came back with moving parts is also rigged, and its
walk cycle is saved as a PNG there, to look at how the limbs move. --from-json replays
the VLM answers saved by an earlier --json run instead of asking again (free), which is
how rig changes are checked against real part boxes.
"""

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.alive.postprocess import postprocess  # noqa: E402
from app.alive.rasterize import rasterize  # noqa: E402
from app.alive.rig import rig_sprite  # noqa: E402
from app.alive.vlm import Budget, OpenRouterVlm, VlmError, load_prompt  # noqa: E402
from app.config import GameConfig, Settings  # noqa: E402
from app.errors import AppError  # noqa: E402
from scripts.drawings import DRAWINGS, Drawing  # noqa: E402


class Replay:
    """Answers from an earlier run's --json file, by drawing id."""

    def __init__(self, path: Path) -> None:
        from app.alive.vlm import VlmResult

        self.answers = {row["id"]: VlmResult.model_validate(row["raw"]) for row in json.loads(path.read_text()) if "raw" in row}
        self.current = ""

    async def classify(self, image_png: bytes) -> Any:
        if self.current not in self.answers:
            raise VlmError("VLM_UNAVAILABLE", "not in the replay file")
        return self.answers[self.current]


async def classify(vlm: Any, config: GameConfig, drawing: Drawing) -> dict[str, Any]:
    limits = config.limits["alive"]
    cutout = rasterize(drawing.strokes, limits["raster_long_side"], limits["raster_pad"])
    buffer = io.BytesIO()
    cutout.on_white().save(buffer, "PNG")
    long_side = (max(cutout.image.size) - 2 * limits["raster_pad"]) / cutout.scale
    started = time.monotonic()
    if isinstance(vlm, Replay):
        vlm.current = drawing.id
    row: dict[str, Any] = {"id": drawing.id, "expected": drawing.expected, "also": list(drawing.also), "hint": drawing.hint}
    try:
        result = await vlm.classify(buffer.getvalue())
    except VlmError as error:
        return {**row, "error": error.code, "seconds": round(time.monotonic() - started, 2)}
    row["seconds"] = round(time.monotonic() - started, 2)
    row["raw"] = result.model_dump()
    try:
        classified = postprocess(result, config.labels, long_side, config.balance["size_class_px"], limits["confidence_min"])
    except AppError as error:
        return {**row, "error": error.code, "type": result.type}
    row.update(type=classified.type, label=classified.label, confidence=result.confidence,
               top3=[choice.type for choice in result.top3], parts=classified.parts,
               locomotion=classified.locomotion, chooser=bool(classified.choices))  # fmt: skip
    row["_classified"], row["_cutout"] = classified, cutout
    return row


def walk_strip(row: dict[str, Any], directory: Path) -> None:
    from PIL import Image, ImageDraw

    classified, cutout = row["_classified"], row["_cutout"]
    parts = classified.parts
    walker = classified.locomotion == "walk" and classified.type in ("creature", "predator", "person")
    if not walker and not any(parts.get(kind) for kind in ("legs", "wings", "wheels", "tail")):
        return
    sheet, manifest, _ = rig_sprite(cutout, classified)
    w, h = manifest["frameW"], manifest["frameH"]
    move = manifest["anims"]["move"]
    frames = [sheet.crop((i * w, move["row"] * h, (i + 1) * w, (move["row"] + 1) * h)) for i in range(move["frames"])]
    boxes = cutout.image.copy()
    draw = ImageDraw.Draw(boxes)
    colours = {"legs": "red", "wings": "blue", "wheels": "green", "head": "orange", "tail": "purple"}
    for kind, colour in colours.items():
        items = parts.get(kind) or []
        items = [items] if items and not isinstance(items[0], list | tuple) else items
        for x, y, bw, bh in items:
            draw.rectangle([x * boxes.width, y * boxes.height, (x + bw) * boxes.width, (y + bh) * boxes.height], outline=colour, width=2)
    strip = Image.new("RGBA", (boxes.width + len(frames) * (w + 4) + 8, max(h, boxes.height) + 16), "white")
    strip.alpha_composite(boxes, (0, 16))
    for i, frame in enumerate(frames):
        strip.alpha_composite(frame, (boxes.width + 8 + i * (w + 4), 16))
    ImageDraw.Draw(strip).text((2, 2), f"{row['id']} -> {row['type']} ({row['label']['en']})", fill="black")
    strip.convert("RGB").save(directory / f"{row['id'].replace(' ', '_').replace('+', '')}.png")


def report(rows: list[dict[str, Any]], spent: float, model: str) -> str:
    done = [r for r in rows if "type" in r]
    strict = [r for r in done if r["type"] == r["expected"]]
    fair = [r for r in done if r["type"] == r["expected"] or r["type"] in r["also"]]
    top3 = [r for r in done if r["expected"] in (r.get("top3") or [])]
    hints = [r for r in done if r["hint"]]
    lines = [
        "# VLM accuracy (T7)",
        "",
        f"`scripts/vlm_accuracy.py` over the {len(rows)} drawings in `scripts/drawings.py`, run "
        f"{time.strftime('%Y-%m-%d')} with `{model}` (fallbacks as in `config/vlm.yaml`), prompt "
        f"v{rows and rows[0].get('prompt', '?')}. Cost about ${spent:.2f}.",
        "",
        f"- **Top-1: {len(strict)} / {len(rows)} = {len(strict) / len(rows):.0%}** (target 80%). "
        f"Counting a fair second reading (a dog as a predator, a boat as water): "
        f"{len(fair)} / {len(rows)} = {len(fair) / len(rows):.0%}.",
        f"- Top-3 contains the expected type: {len(top3)} / {len(rows)} = {len(top3) / len(rows):.0%}.",
        f"- Handwriting hints (RU and EN words on vague shapes): "
        f"{sum(r['type'] == r['expected'] for r in hints)} / {len(hints)} right.",
        f"- Sent to the chooser (confidence under 0.6): {sum(bool(r.get('chooser')) for r in done)}; "
        f"errors: {len(rows) - len(done)} ({', '.join(sorted({r['error'] for r in rows if 'type' not in r})) or 'none'}).",
        f"- Latency: median {statistics.median(r['seconds'] for r in rows):.1f} s, "
        f"max {max(r['seconds'] for r in rows):.1f} s.",
        "",
        "| type | right | drawings |",
        "|---|---|---|",
    ]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[row["expected"]].append(row)
    for kind, items in sorted(by_type.items()):
        lines.append(f"| {kind} | {sum(r.get('type') == kind for r in items)} / {len(items)} | "
                     + ", ".join(r["id"] for r in items) + " |")  # fmt: skip
    lines += ["", "Misses (expected → got, the VLM's label, confidence):", ""]
    for row in rows:
        if row.get("type") != row["expected"]:
            got = row.get("type") or row.get("error")
            label = (row.get("label") or {}).get("en", "")
            fairly = " (a fair reading)" if row.get("type") in row["also"] else ""
            lines.append(f"- {row['id']}: {row['expected']} → {got}, \"{label}\", "
                         f"{row.get('confidence', 0):.2f}{fairly}")  # fmt: skip
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", type=Path, help="raw results, one entry per drawing")
    parser.add_argument("--sheets", type=Path)
    parser.add_argument("--only", default="")
    parser.add_argument("--budget", type=float, default=2.0)
    parser.add_argument("--from-json", type=Path, help="replay answers saved with --json (no VLM calls)")
    args = parser.parse_args()
    settings = Settings()
    if not settings.vlm_api_key and not args.from_json:
        raise SystemExit("VLM_API_KEY is not set (in .env)")
    config = GameConfig(settings.config_dir)
    spent = {"microusd": 0}
    budget = Budget(args.budget, lambda key: spent["microusd"], lambda key, amount: spent.__setitem__("microusd", spent["microusd"] + amount))
    prompt = load_prompt(ROOT / "prompts" / "classify.md")
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    drawings = [d for d in DRAWINGS if not wanted or d.id in wanted]
    async with httpx.AsyncClient() as http:
        vlm: Any = Replay(args.from_json) if args.from_json else OpenRouterVlm(http, config.vlm, settings.vlm_api_key, prompt, budget)
        gate = asyncio.Semaphore(1 if args.from_json else 4)

        async def one(drawing: Drawing) -> dict[str, Any]:
            async with gate:
                row = await classify(vlm, config, drawing)
                mark = "ok " if row.get("type") == drawing.expected else "MISS"
                print(f"{mark} {drawing.id:24} expected {drawing.expected:9} got {row.get('type') or row.get('error')}", flush=True)
                return row

        rows = await asyncio.gather(*(one(d) for d in drawings))
    for row in rows:
        row["prompt"] = prompt[0]
    if args.sheets:
        args.sheets.mkdir(parents=True, exist_ok=True)
        for row in rows:
            if "_classified" in row:
                walk_strip(row, args.sheets)
    clean = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    text = report(clean, spent["microusd"] / 1_000_000, config.vlm["primary_model"])
    print("\n" + text)
    if args.report:
        args.report.write_text(text)
    if args.json:
        args.json.write_text(json.dumps(clean, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
