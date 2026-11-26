"""Compact layered atlas, made solely from the selected drawing's strokes."""

from PIL import Image

from app.alive.parts import split_named
from app.alive.rasterize import render


def build_atlas(cutout, parts, *, groups=None):
    width, height = cutout.image.size
    scale = cutout.scale
    groups = split_named(cutout, parts) if groups is None else groups
    definitions = {p.id: p for p in parts}
    images, layers = [], []
    for name, strokes in groups.items():
        if not strokes:
            if name != "body":
                raise ValueError(f"part={name}: identified part has no lines")
            continue
        image = Image.fromarray(
            render(strokes, cutout.x, cutout.y, width, height, scale)
        )
        box = image.getchannel("A").getbbox()
        if box is None:
            raise ValueError(f"part={name}: empty raster layer")
        x0, y0, _x1, _y1 = box
        image = image.crop(box)
        definition = definitions.get(name)
        pivot = (
            [0.0, 0.0]
            if definition is None
            else [
                (definition.pivot[0] * width - width / 2) / scale,
                (definition.pivot[1] * height - height / 2) / scale,
            ]
        )
        layers.append(
            {
                "id": name,
                "order": definition.order if definition else 0,
                "offset": [(x0 - width / 2) / scale, (y0 - height / 2) / scale],
                "size": [image.width / scale, image.height / scale],
                "pivot": pivot,
            }
        )
        images.append(image)
    # Pack trimmed rectangles in shelves; a layer is at most 512px wide.
    x = y = row_h = max_x = 0
    for image, layer in zip(images, layers):
        if x + image.width > 2048:
            x = 0
            y += row_h
            row_h = 0
        layer["src"] = [x, y, image.width, image.height]
        x += image.width
        max_x = max(max_x, x)
        row_h = max(row_h, image.height)
    atlas = Image.new("RGBA", (max_x, y + row_h))
    for image, layer in zip(images, layers):
        atlas.paste(image, tuple(layer["src"][:2]))
    return atlas, {
        "format": "layered",
        "version": 1,
        "layers": sorted(layers, key=lambda l: (l["order"], l["id"])),
    }
