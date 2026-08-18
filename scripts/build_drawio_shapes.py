"""Generate the vendored draw.io shape catalogue from draw.io's own palettes.

The Azure icons are the reason this exists. draw.io's Azure library is **not**
made of ``shape=mxgraph.azure2.*`` stencils — those names don't exist — it is a
set of **SVG images** referenced as ``image=img/lib/azure2/<folder>/<File>.svg``.
A model asked to "use the Azure firewall icon" reliably invents the stencil form,
draw.io can't resolve it, and every node silently degrades to a plain coloured
rectangle. Shipping the real style strings is the only fix that sticks.

Ground truth is ``Sidebar-Azure2.js`` in the draw.io repo, which is what the
editor's own shape picker is built from. This script parses it into
``drawio_shapes.json`` next to the MCP server.

Usage (needs network):

    uv run python scripts/build_drawio_shapes.py

Re-run it to pick up new Azure services; review the diff like any other change.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

SOURCE = (
    "https://raw.githubusercontent.com/jgraph/drawio/dev/"
    "src/main/webapp/js/diagramly/sidebar/Sidebar-Azure2.js"
)
OUT = Path(__file__).resolve().parents[1] / ("precursor/backend/services/mcp/drawio_shapes.json")

# `var r = 400` in the palette; entry sizes are expressed as `r * <factor>`.
_R = 400

# Which folder each palette function pulls its images from, e.g.
#   this.addAzure2NetworkingPalette(gn, r, sb, s + 'networking/');
_DISPATCH = re.compile(r"this\.(addAzure2\w+Palette)\(gn, r, sb, s \+ '([^']+)'\)")
_PALETTE_DEF = re.compile(r"Sidebar\.prototype\.(addAzure2\w+Palette) = function")
_BASE_TAGS = re.compile(r"var dt = '([^']*)'")
_ENTRY = re.compile(
    r"createVertexTemplateEntry\(\s*s \+ '([^']+?)\.svg;?'\s*,\s*"
    r"r \* ([\d.]+)\s*,\s*r \* ([\d.]+)\s*,\s*'[^']*'\s*,\s*'([^']*)'"
    r"(?:.*?getTagsForStencil\(gn, '([^']*)')?",
    re.DOTALL,
)

# Appended to every catalogue style. draw.io's palette drops these icons with an
# *empty* label, so it never has to say where a label goes; we always label them,
# and a caption under the icon is the conventional cloud-diagram look.
_LABEL = "verticalLabelPosition=bottom;verticalAlign=top;"


def _fetch(source: str) -> str:
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=60) as response:
            return str(response.read().decode("utf-8"))
    return Path(source).read_text(encoding="utf-8")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse(js: str) -> list[dict[str, Any]]:
    folders = dict(_DISPATCH.findall(js))
    bounds = [(m.start(), m.group(1)) for m in _PALETTE_DEF.finditer(js)]
    bounds.append((len(js), ""))

    shapes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in range(len(bounds) - 1):
        start, fn = bounds[index]
        body = js[start : bounds[index + 1][0]]
        folder = folders.get(fn)
        if not folder:
            continue
        category = _slug(folder.rstrip("/"))
        base = _BASE_TAGS.search(body)
        base_tags = base.group(1).strip() if base else ""

        for filename, width, height, title, tags in _ENTRY.findall(body):
            name = title.strip() or filename.replace("_", " ")
            key = f"{category}/{_slug(name)}"
            if key in seen:
                continue
            seen.add(key)
            keywords = sorted(
                {word for word in f"{tags} {base_tags} {name}".lower().split() if len(word) > 2}
            )
            shapes.append(
                {
                    "key": key,
                    "name": name,
                    "category": category,
                    "style": (
                        "image;aspect=fixed;html=1;points=[];align=center;fontSize=12;"
                        f"{_LABEL}image=img/lib/azure2/{folder}{filename}.svg;"
                    ),
                    "width": round(_R * float(width)),
                    "height": round(_R * float(height)),
                    "tags": keywords,
                }
            )
    return shapes


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else SOURCE
    shapes = parse(_fetch(source))
    if len(shapes) < 500:
        print(f"Refusing to write a suspiciously small catalogue ({len(shapes)})")
        return 1
    shapes.sort(key=lambda s: s["key"])
    OUT.write_text(
        json.dumps({"library": "azure2", "shapes": shapes}, indent=1) + "\n",
        encoding="utf-8",
    )
    categories = sorted({s["category"] for s in shapes})
    print(f"Wrote {len(shapes)} shapes in {len(categories)} categories to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
