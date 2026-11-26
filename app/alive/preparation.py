"""Resolve flat drawing layers and their pivots before motion planning."""

import math

import numpy as np

from app.alive.atlas import build_atlas
from app.alive.choreography import DrawingParts, validate_parts
from app.alive.parts import STEP_PX, curve, split_named


def nearby_distances(points, body, reach):
    """Distance to nearby body ink, bounded in memory for closed outlines too."""
    cells = {}
    for point in body:
        cell = (math.floor(point[0] / reach), math.floor(point[1] / reach))
        cells.setdefault(cell, []).append(point)
    distances = []
    for point in points:
        cx, cy = math.floor(point[0] / reach), math.floor(point[1] / reach)
        local = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 for p in cells.get((cx + dx, cy + dy), ())]
        distances.append(((np.array(local) - point) ** 2).sum(axis=1).min()
                         if local else float('inf'))
    return np.array(distances)


def prepare_atlas(cutout, drawing: DrawingParts, maximum: int = 16):
    validate_parts(drawing.parts, maximum)
    drawing = drawing.model_copy(deep=True)
    groups = split_named(cutout, drawing.parts)
    width, height = cutout.image.size

    def pixels(strokes):
        return np.array([
            [(x - cutout.x) * cutout.scale, (y - cutout.y) * cutout.scale]
            for stroke in strokes
            for x, y, _ in curve(stroke['points'], STEP_PX / cutout.scale)
        ]).reshape(-1, 2)

    body = pixels(groups['body'])
    for part in drawing.parts:
        strokes = groups[part.id]
        ink = pixels(strokes)
        if not len(ink):
            raise ValueError(f'part={part.id}: identified part has no lines')
        if part.pivot_type == 'center':
            pivot = (ink.min(axis=0) + ink.max(axis=0)) / 2
        else:
            if not len(body):
                raise ValueError(f'part={part.id}: joint has no body to attach to')
            widest = max(s['width'] for s in [*strokes, *groups['body']]) * cutout.scale
            reach = widest + max(width, height) * 0.03 + STEP_PX
            candidates = []
            for stroke in strokes:
                ends = stroke['points'][0], stroke['points'][-1]
                if math.dist(ends[0][:2], ends[1][:2]) <= 3 * stroke['width']:
                    # A closed head/limb outline can meet the body anywhere on
                    # its contour, independently of where the player began it.
                    outline = pixels([stroke])
                    distances = nearby_distances(outline, body, reach)
                    best = distances.min()
                    if best <= reach ** 2:
                        candidates.extend(outline[distances <= best + 1e-6])
                else:
                    candidates.extend(pixels([{**stroke, 'points': [end]} for end in ends]))
            candidates = np.array(candidates).reshape(-1, 2)
            distances = nearby_distances(candidates, body, reach)
            contacts = candidates[distances <= reach ** 2]
            if not len(contacts):
                raise ValueError(f'part={part.id}: joint does not touch the body within {reach:.1f}px')
            guess = np.array(part.pivot) * [width, height]
            pivot = contacts[((contacts - guess) ** 2).sum(axis=1).argmin()]
        part.pivot = (float(pivot[0] / width), float(pivot[1] / height))

    atlas, manifest = build_atlas(cutout, drawing.parts, groups=groups)
    layers = {layer['id']: layer for layer in manifest['layers']}
    for part in drawing.parts:
        layer = layers[part.id]
        part.box = (
            (layer['offset'][0] * cutout.scale + width / 2) / width,
            (layer['offset'][1] * cutout.scale + height / 2) / height,
            layer['size'][0] * cutout.scale / width,
            layer['size'][1] * cutout.scale / height,
        )
    validate_parts(drawing.parts, maximum)
    return drawing, atlas, manifest
