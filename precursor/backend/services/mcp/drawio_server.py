"""Built-in MCP server: draw.io diagram authoring (sandboxed).

Runs as a stdio subprocess (like ``workspace_fs_server``) and writes native
``.drawio`` files — plain mxGraph XML — into a Workspace working tree. Every
path is routed through :func:`workspace_fs.safe_join`, so nothing outside
``workspaces_dir/<slug>`` is ever reachable, and the result lands in a
git-backed tree the user can commit from the Workspace UI.

Two things here exist because a model left to its own devices gets them wrong:

**Layout.** Models write plausible mxGraph markup but pick poor ``x``/``y``
coordinates, so shapes overlap and edges cross. ``create_diagram`` takes a graph
— nodes, edges and nested ``groups`` — and derives the geometry itself.

**Real icons.** draw.io's Azure library is a set of SVG *images*
(``image=img/lib/azure2/...``), not ``shape=mxgraph.azure2.*`` stencils. Models
reliably invent the stencil form, draw.io can't resolve it, and every service
silently degrades to a blank rectangle. ``search_shapes`` serves the genuine
style strings from a vendored catalogue built out of draw.io's own palette (see
``scripts/build_drawio_shapes.py``), and ``create_diagram`` resolves shape names
through it — reporting what it matched instead of quietly drawing a box.

Tools:
- ``list_workspaces()`` — discover workspace ids to write into.
- ``search_shapes(query)`` — find real cloud/product icons by keyword.
- ``list_shapes()`` — the generic shape/colour/edge presets.
- ``create_diagram(workspace_id, path, nodes, edges, groups, ...)`` — laid out.
- ``write_diagram_xml(workspace_id, path, xml, ...)`` — raw mxGraph escape hatch.
- ``read_diagram(workspace_id, path)`` — read a diagram back for editing.

Output is **deterministic**: no timestamps or random ids, so regenerating a
diagram produces an empty git diff when nothing changed.
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import Workspace
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services.mcp.workspace_links import with_open_link

mcp = FastMCP("drawio")

# Suffixes we accept as "already a diagram file"; anything else gets `.drawio`
# appended so the file opens in draw.io / the VS Code extension by double-click.
_DIAGRAM_SUFFIXES = (".drawio", ".xml", ".drawio.xml")
_MAX_NODES = 500
_MAX_XML_BYTES = 2_000_000

_CATALOG_PATH = Path(__file__).with_name("drawio_shapes.json")

_DEFAULT_WIDTH = 160
_DEFAULT_HEIGHT = 60
# Gaps between sibling boxes in a layer, and between layers.
_GAP_WITHIN = 50
_GAP_BETWEEN = 80
_MARGIN = 40
# Room reserved under an icon whose caption renders below it, so the label
# doesn't collide with the next layer.
_LABEL_SPACE = 26
# Group padding — the top band leaves room for the container's own title.
# Group padding. The top band is computed per container from its own title
# (see ``_title_band``) — a fixed one lets a wrapping "Spoke 1 — Production
# 10.1.0.0/16" land on top of the first icon.
_GROUP_PAD = 30
# Rough text metrics for the 13px bold container titles and 12px captions. Only
# used for spacing, so an approximation is fine.
_CHAR_WIDTH = 7.6
_LINE_HEIGHT = 19
# Never widen a container past this just to fit its title on one line.
_TITLE_MAX_WIDTH = 360
# Same idea for the caption under an icon: reserve room so neighbours don't
# collide, but don't let one long service name space the whole row out.
_CAPTION_CHAR_WIDTH = 6.4
_CAPTION_MAX_WIDTH = 170

# Shape presets → mxGraph style fragments. Keys are what the model passes as
# ``shape``; the values are lifted from draw.io's own default shape styles so a
# generated file looks like a hand-drawn one.
SHAPES: dict[str, str] = {
    "process": "rounded=0;whiteSpace=wrap;html=1;",
    "rounded": "rounded=1;whiteSpace=wrap;html=1;arcSize=20;",
    "terminator": "rounded=1;whiteSpace=wrap;html=1;arcSize=40;",
    "start": "ellipse;whiteSpace=wrap;html=1;",
    "end": "ellipse;whiteSpace=wrap;html=1;",
    "decision": "rhombus;whiteSpace=wrap;html=1;",
    "data": (
        "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;fixedSize=1;"
    ),
    "document": "shape=document;whiteSpace=wrap;html=1;boundedLbl=1;",
    "database": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;",
    "cloud": "ellipse;shape=cloud;whiteSpace=wrap;html=1;",
    "note": "shape=note;whiteSpace=wrap;html=1;backgroundOutline=1;darkOpacity=0.05;size=14;",
    "actor": (
        "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;"
    ),
    "component": "shape=component;align=left;spacingLeft=36;whiteSpace=wrap;html=1;dropTarget=0;",
    "step": "shape=step;perimeter=stepPerimeter;whiteSpace=wrap;html=1;fixedSize=1;",
    "hexagon": "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;",
    "container": ("rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;verticalAlign=top;"),
}

# Shapes whose natural aspect ratio isn't the default box. Applied only when the
# caller didn't pin an explicit width/height, so a stick-figure actor doesn't
# come out as a stretched 160x60 rectangle.
SHAPE_SIZES: dict[str, tuple[int, int]] = {
    "actor": (40, 80),
    "start": (120, 60),
    "end": (120, 60),
    "decision": (140, 80),
    "note": (140, 90),
    "database": (140, 80),
    "cloud": (160, 90),
}

# draw.io's stock palette, so diagrams match the editor's own colour picker.
COLORS: dict[str, str] = {
    "blue": "fillColor=#dae8fc;strokeColor=#6c8ebf;",
    "green": "fillColor=#d5e8d4;strokeColor=#82b366;",
    "orange": "fillColor=#ffe6cc;strokeColor=#d79b00;",
    "yellow": "fillColor=#fff2cc;strokeColor=#d6b656;",
    "red": "fillColor=#f8cecc;strokeColor=#b85450;",
    "purple": "fillColor=#e1d5e7;strokeColor=#9673a6;",
    "gray": "fillColor=#f5f5f5;strokeColor=#666666;",
    "none": "fillColor=none;",
}

# ``labelBackgroundColor`` on every edge: an unbacked edge label sitting on top
# of a container border or another edge is the single most common reason a
# generated diagram reads as noise.
_EDGE_BASE = "html=1;labelBackgroundColor=#ffffff;fontSize=11;"
EDGE_STYLES: dict[str, str] = {
    "orthogonal": f"edgeStyle=orthogonalEdgeStyle;rounded=0;{_EDGE_BASE}",
    "rounded": f"edgeStyle=orthogonalEdgeStyle;rounded=1;{_EDGE_BASE}",
    "curved": f"edgeStyle=orthogonalEdgeStyle;curved=1;rounded=0;{_EDGE_BASE}",
    "straight": _EDGE_BASE,
    "dashed": f"edgeStyle=orthogonalEdgeStyle;rounded=0;dashed=1;{_EDGE_BASE}",
    "bidirectional": (
        f"edgeStyle=orthogonalEdgeStyle;rounded=0;startArrow=classic;startFill=1;{_EDGE_BASE}"
    ),
    "plain": f"edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=none;{_EDGE_BASE}",
}

# Container presets. Unlike a plain node these are drawn behind their children,
# so they're unfilled by default and title-aligned to the top-left.
GROUP_STYLE = (
    "rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=12;"
    "spacingTop=6;fontStyle=1;fontSize=13;arcSize=6;"
)


class DiagramError(ValueError):
    """Raised when a caller-supplied graph spec can't be turned into a diagram."""


@lru_cache(maxsize=1)
def _catalog() -> list[dict[str, Any]]:
    """The vendored draw.io shape catalogue (see scripts/build_drawio_shapes.py)."""
    if not _CATALOG_PATH.is_file():
        return []
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    return shapes if isinstance(shapes, list) else []


def _haystack(shape: dict[str, Any]) -> str:
    return " ".join(
        (str(shape.get("key", "")), str(shape.get("name", "")), *shape.get("tags", []))
    ).lower()


# Categories that are junk drawers in draw.io's Azure palette — real duplicates
# of core services live there ("other/expressroute-direct" alongside
# "networking/expressroute-circuits"), so they lose ties rather than winning on
# a shorter name.
_FRINGE_CATEGORIES = frozenset(
    {"other", "preview", "menu", "cxp", "azure-ecosystem", "azure-stack", "migrate"}
)


def search_catalog(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Rank catalogue shapes against ``query``. Exact keys win, then coverage."""
    tokens = [t for t in query.lower().replace("/", " ").replace("_", " ").split() if t]
    if not tokens:
        return []
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for shape in _catalog():
        text = _haystack(shape)
        matched = sum(1 for token in tokens if token in text)
        if not matched:
            continue
        name = str(shape.get("name", "")).lower()
        score = matched * 5
        if shape.get("key") == query.lower().strip():
            score += 100
        if all(token in name for token in tokens):
            score += 20  # the product's own name beats an incidental tag hit
        if str(shape.get("category")) in _FRINGE_CATEGORIES:
            score -= 8
        # Shortest name breaks remaining ties: "firewall" should land on
        # "Firewalls", not "Web Application Firewall Policies (WAF)".
        ranked.append((score, -len(name), shape))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [shape for _, _, shape in ranked[:limit]]


def resolve_shape(token: str) -> tuple[str | None, tuple[int, int] | None, str | None]:
    """Resolve a ``shape`` value to ``(style, size, note)``.

    Accepts, in order: a raw mxGraph style (anything with ``=``), a generic
    preset name, a catalogue key, or a free-text product name resolved against
    the catalogue. The ``note`` reports a fuzzy catalogue match so the caller
    learns what it actually got rather than discovering a blank box later.
    """
    key = (token or "").strip()
    if not key:
        return None, None, None
    if "=" in key:
        return (key if key.endswith(";") else key + ";"), None, None
    lowered = key.lower()
    if lowered in SHAPES:
        return SHAPES[lowered], SHAPE_SIZES.get(lowered), None
    hits = search_catalog(lowered, limit=3)
    if not hits:
        return None, None, f"Unknown shape '{key}' — drew a plain box instead."
    best = hits[0]
    size = (int(best["width"]), int(best["height"]))
    if best.get("key") == lowered:
        return str(best["style"]), size, None
    return (
        str(best["style"]),
        size,
        f"Matched shape '{key}' to '{best['key']}' ({best['name']}).",
    )


def _attr(value: str) -> str:
    """Escape ``value`` for use inside a double-quoted XML attribute."""
    return escape(value, {'"': "&quot;", "\n": "&#10;"})


def _resolve_style(preset: str | None, table: dict[str, str], fallback: str) -> str:
    """Look ``preset`` up in ``table``, or pass it through when it's raw mxGraph."""
    key = (preset or "").strip()
    if not key:
        return fallback
    if "=" in key:
        return key if key.endswith(";") else key + ";"
    return table.get(key.lower(), fallback)


@dataclass
class _Box:
    """A laid-out rectangle: a node, or a container holding more boxes."""

    id: str
    width: int
    height: int
    # Room a caption below the box needs; excluded from the drawn geometry.
    label_space: int = 0
    # How wide the caption wants to be. An icon is ~60px but "Customer Edge
    # Routers" underneath it is not, and letting captions collide (or spill out
    # of their container) is a big part of what makes a generated diagram
    # unreadable — so spacing reserves the wider of the two.
    label_width: int = 0
    is_group: bool = False
    direction: str = "vertical"
    children: list[_Box] = field(default_factory=list)
    x: int = 0
    y: int = 0

    @property
    def outer_width(self) -> int:
        return max(self.width, self.label_width)

    @property
    def outer_height(self) -> int:
        return self.height + self.label_space

    @property
    def draw_x(self) -> int:
        """Where the box itself sits inside the slot its caption reserved."""
        return self.x + (self.outer_width - self.width) // 2


def assign_layers(node_ids: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Group ``node_ids`` into layers so every edge points at a later layer.

    Longest-path layering over a Kahn traversal. Cycles are tolerated rather
    than rejected — a state machine or a retry loop is a perfectly reasonable
    thing to diagram — by cutting the loop at its earliest unreached node.
    """
    known = set(node_ids)
    incoming: dict[str, set[str]] = {n: set() for n in node_ids}
    outgoing: dict[str, set[str]] = {n: set() for n in node_ids}
    for src, dst in edges:
        if src in known and dst in known and src != dst:
            outgoing[src].add(dst)
            incoming[dst].add(src)

    depth = dict.fromkeys(node_ids, 0)
    pending = {n: set(preds) for n, preds in incoming.items()}
    settled: set[str] = set()
    queue = deque(n for n in node_ids if not pending[n])
    while len(settled) < len(node_ids):
        if not queue:
            # Everything left sits inside a cycle, so nothing has an in-degree
            # of zero. Cut the loop at the earliest such node and carry on —
            # otherwise the whole cycle collapses onto a single row.
            stuck = next(n for n in node_ids if n not in settled)
            resolved = [depth[p] for p in incoming[stuck] if p in settled]
            depth[stuck] = max(resolved) + 1 if resolved else 0
            pending[stuck].clear()
            queue.append(stuck)
        node = queue.popleft()
        if node in settled:
            continue
        settled.add(node)
        for succ in outgoing[node]:
            pending[succ].discard(node)
            if succ in settled:
                continue  # back edge — leave the already-placed layer alone
            depth[succ] = max(depth[succ], depth[node] + 1)
            if not pending[succ]:
                queue.append(succ)

    layers: list[list[str]] = [[] for _ in range(max(depth.values(), default=0) + 1)]
    for node in node_ids:  # input order keeps the layout stable and predictable
        layers[depth[node]].append(node)
    return _reduce_crossings(layers, incoming)


def _reduce_crossings(layers: list[list[str]], incoming: dict[str, set[str]]) -> list[list[str]]:
    """One barycenter pass: sort each layer by the mean position of its parents.

    Cheap (single downward sweep, no iteration to convergence) but it removes
    most of the obvious edge crossings a naive insertion-order layout produces.
    """
    for index in range(1, len(layers)):
        above = {node: pos for pos, node in enumerate(layers[index - 1])}
        ranked: list[tuple[float, int, str]] = []
        for pos, node in enumerate(layers[index]):
            parents = [above[p] for p in incoming[node] if p in above]
            # No parent in the layer above => keep the incoming order.
            center = sum(parents) / len(parents) if parents else float(pos)
            ranked.append((center, pos, node))
        ranked.sort()
        layers[index] = [node for _, _, node in ranked]
    return layers


def _arrange(boxes: list[_Box], edges: list[tuple[str, str]], direction: str) -> tuple[int, int]:
    """Place ``boxes`` relative to (0, 0); return the content size they occupy."""
    if not boxes:
        return 0, 0
    if not edges:
        # Nothing to derive an order from — the sibling resources of a subnet,
        # say. Layering them would put everything in one row perpendicular to
        # `direction`, which reads as the opposite of what was asked for, so
        # pack them along `direction` instead.
        return _pack(boxes, direction)

    by_id = {b.id: b for b in boxes}
    layers = assign_layers([b.id for b in boxes], edges)

    spans: list[int] = []
    for layer in layers:
        sizes = [
            by_id[i].outer_width if direction == "vertical" else by_id[i].outer_height
            for i in layer
        ]
        spans.append(sum(sizes) + _GAP_WITHIN * max(len(layer) - 1, 0))
    widest = max(spans, default=0)

    main = 0
    for index, layer in enumerate(layers):
        cross = (widest - spans[index]) // 2
        thickest = 0
        for item in layer:
            box = by_id[item]
            if direction == "vertical":
                box.x, box.y = cross, main
                cross += box.outer_width + _GAP_WITHIN
                thickest = max(thickest, box.outer_height)
            else:
                box.x, box.y = main, cross
                cross += box.outer_height + _GAP_WITHIN
                thickest = max(thickest, box.outer_width)
        main += thickest + _GAP_BETWEEN
    depth = max(main - _GAP_BETWEEN, 0)
    return (widest, depth) if direction == "vertical" else (depth, widest)


def _pack(boxes: list[_Box], direction: str) -> tuple[int, int]:
    """Flow unconnected boxes along ``direction``, wrapping to stay compact."""
    count = len(boxes)
    per_line = count if count <= 4 else math.ceil(math.sqrt(count))
    lines = [boxes[i : i + per_line] for i in range(0, count, per_line)]
    horizontal = direction == "horizontal"

    def along(box: _Box) -> int:
        return box.outer_width if horizontal else box.outer_height

    def across(box: _Box) -> int:
        return box.outer_height if horizontal else box.outer_width

    spans = [sum(along(b) for b in line) + _GAP_WITHIN * (len(line) - 1) for line in lines]
    widest = max(spans, default=0)

    main = 0
    for index, line in enumerate(lines):
        cross = (widest - spans[index]) // 2
        thickest = 0
        for box in line:
            if horizontal:
                box.x, box.y = cross, main
            else:
                box.x, box.y = main, cross
            cross += along(box) + _GAP_WITHIN
            thickest = max(thickest, across(box))
        main += thickest + _GAP_WITHIN
    depth = max(main - _GAP_WITHIN, 0)
    return (widest, depth) if horizontal else (depth, widest)


def _title_band(label: str, width: int) -> int:
    """Height a container's title needs at ``width``, so it can't sit on a child."""
    text = label.strip()
    if not text:
        return _GROUP_PAD
    per_line = max(int(width / _CHAR_WIDTH), 10)
    lines = sum(max(1, math.ceil(len(part) / per_line)) for part in text.split("\n"))
    return 14 + lines * _LINE_HEIGHT


def _scoped_edges(
    pairs: list[tuple[str, str]], members: set[str], parent_of: dict[str, str | None]
) -> list[tuple[str, str]]:
    """Lift each edge to the pair of ``members`` its endpoints live under.

    An edge from a VM inside a spoke to a firewall inside the hub becomes a
    hub↔spoke edge at the top level, which is what orders the containers.
    """

    def ancestor(item: str) -> str | None:
        seen: set[str] = set()
        current: str | None = item
        while current is not None and current not in seen:
            if current in members:
                return current
            seen.add(current)
            current = parent_of.get(current)
        return None

    lifted: list[tuple[str, str]] = []
    for src, dst in pairs:
        a, b = ancestor(src), ancestor(dst)
        if a is not None and b is not None and a != b:
            lifted.append((a, b))
    return lifted


def build_diagram_xml(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    *,
    groups: list[dict[str, Any]] | None = None,
    title: str = "Page-1",
    direction: str = "vertical",
) -> tuple[str, list[str]]:
    """Render a graph as a laid-out ``.drawio`` document.

    Returns ``(xml, notes)`` — ``notes`` reports fuzzy or failed shape lookups.
    """
    if not nodes:
        raise DiagramError("At least one node is required")
    if len(nodes) > _MAX_NODES:
        raise DiagramError(f"Too many nodes ({len(nodes)} > {_MAX_NODES})")
    if direction not in ("vertical", "horizontal"):
        raise DiagramError("direction must be 'vertical' or 'horizontal'")

    notes: list[str] = []
    parent_of: dict[str, str | None] = {}
    boxes: dict[str, _Box] = {}
    group_specs: dict[str, dict[str, Any]] = {}
    node_specs: dict[str, dict[str, Any]] = {}
    node_styles: dict[str, str] = {}

    for spec in groups or []:
        gid = str(spec.get("id") or "").strip()
        if not gid:
            raise DiagramError("Every group needs an id")
        if gid in group_specs:
            raise DiagramError(f"Duplicate group id: {gid}")
        group_specs[gid] = spec
        parent_of[gid] = str(spec.get("group") or "").strip() or None

    order: list[str] = []
    for position, spec in enumerate(nodes):
        nid = str(spec.get("id") or "").strip() or f"n{position + 1}"
        if nid in node_specs or nid in group_specs:
            raise DiagramError(f"Duplicate node id: {nid}")
        node_specs[nid] = spec
        order.append(nid)
        parent_of[nid] = str(spec.get("group") or "").strip() or None

    for owner, parent in parent_of.items():
        if parent is not None and parent not in group_specs:
            raise DiagramError(f"'{owner}' references unknown group '{parent}'")

    pairs: list[tuple[str, str]] = []
    for edge in edges or []:
        src = str(edge.get("source") or "").strip()
        dst = str(edge.get("target") or "").strip()
        for endpoint, role in ((src, "source"), (dst, "target")):
            if endpoint not in node_specs and endpoint not in group_specs:
                raise DiagramError(f"Edge {role} '{endpoint}' is not a known node or group id")
        pairs.append((src, dst))

    for nid, spec in node_specs.items():
        style, size, note = resolve_shape(str(spec.get("shape") or ""))
        if note:
            notes.append(note)
        raw = str(spec.get("style") or "").strip()
        if raw:
            style = raw if raw.endswith(";") else raw + ";"
        elif style is None:
            style = SHAPES["process"]
        color = str(spec.get("color") or "").strip()
        # A catalogue icon is a coloured SVG; forcing a fill on top of it is what
        # turns the Azure library into featureless squares, so only tint boxes.
        if color and "image=" not in style:
            style += _resolve_style(color, COLORS, "")
        node_styles[nid] = style
        default = size or SHAPE_SIZES.get(str(spec.get("shape") or "").lower())
        width, height = default or (_DEFAULT_WIDTH, _DEFAULT_HEIGHT)
        label = str(spec.get("label") or spec.get("value") or "")
        captioned = "verticalLabelPosition=bottom" in style
        boxes[nid] = _Box(
            id=nid,
            width=max(int(spec.get("width") or width), 20),
            height=max(int(spec.get("height") or height), 20),
            label_space=_LABEL_SPACE if captioned else 0,
            # An overflowing caption is only a spacing problem when it renders
            # *outside* the box; a wrapped in-box label needs no extra room.
            label_width=_caption_width(label) if captioned else 0,
        )

    for gid, spec in group_specs.items():
        boxes[gid] = _Box(
            id=gid,
            width=0,
            height=0,
            is_group=True,
            direction=str(spec.get("direction") or direction),
        )

    children_of: dict[str | None, list[str]] = {}
    for item, parent in parent_of.items():
        children_of.setdefault(parent, []).append(item)

    _size_groups(boxes, children_of, group_specs, pairs, parent_of)

    roots = [boxes[i] for i in children_of.get(None, [])]
    width, height = _arrange(
        roots, _scoped_edges(pairs, {b.id for b in roots}, parent_of), direction
    )
    for box in roots:
        box.x += _MARGIN
        box.y += _MARGIN

    cells: list[str] = []
    for box in roots:
        _emit(box, "1", boxes, children_of, group_specs, node_specs, node_styles, cells)
    for position, edge in enumerate(edges or []):
        style = _resolve_style(str(edge.get("style") or ""), EDGE_STYLES, EDGE_STYLES["orthogonal"])
        cells.append(
            f'        <mxCell id="e{position + 1}" value="{_attr(str(edge.get("label") or ""))}" '
            f'style="{_attr(style)}" edge="1" parent="1" '
            f'source="{_attr(pairs[position][0])}" target="{_attr(pairs[position][1])}">\n'
            f'          <mxGeometry relative="1" as="geometry" />\n'
            f"        </mxCell>"
        )
    page = (width + 2 * _MARGIN, height + 2 * _MARGIN)
    return _wrap_model("\n".join(cells), title=title, page=page), notes


def _size_groups(
    boxes: dict[str, _Box],
    children_of: dict[str | None, list[str]],
    group_specs: dict[str, dict[str, Any]],
    pairs: list[tuple[str, str]],
    parent_of: dict[str, str | None],
) -> None:
    """Lay out and size every container, innermost first."""

    def depth(gid: str) -> int:
        level, current = 0, parent_of.get(gid)
        while current is not None:
            level += 1
            current = parent_of.get(current)
        return level

    for gid in sorted(group_specs, key=depth, reverse=True):
        box = boxes[gid]
        label = str(group_specs[gid].get("label") or group_specs[gid].get("value") or "")
        kids = [boxes[i] for i in children_of.get(gid, [])]
        if not kids:
            box.width = max(200, _title_width(label))
            box.height = _title_band(label, box.width - 2 * _GROUP_PAD) + _GROUP_PAD
            continue
        members = {k.id for k in kids}
        content_w, content_h = _arrange(
            kids, _scoped_edges(pairs, members, parent_of), box.direction
        )
        # Widen a narrow container so its title doesn't wrap into a wall of
        # text, then re-centre the children in whatever width we settled on.
        box.width = max(content_w + 2 * _GROUP_PAD, _title_width(label))
        header = _title_band(label, box.width - 2 * _GROUP_PAD)
        offset = _GROUP_PAD + (box.width - 2 * _GROUP_PAD - content_w) // 2
        for kid in kids:
            kid.x += offset
            kid.y += header
        box.height = content_h + header + _GROUP_PAD


def _title_width(label: str) -> int:
    """Width at which ``label`` fits on one line, capped so a long CIDR-laden
    container title doesn't stretch the whole diagram."""
    longest = max((len(part) for part in label.split("\n")), default=0)
    return min(int(longest * _CHAR_WIDTH) + 2 * _GROUP_PAD, _TITLE_MAX_WIDTH)


def _caption_width(label: str) -> int:
    """Slot width a caption under an icon wants, capped so it can't dominate."""
    longest = max((len(part) for part in label.split("\n")), default=0)
    return min(int(longest * _CAPTION_CHAR_WIDTH) + 12, _CAPTION_MAX_WIDTH)


def _emit(
    box: _Box,
    parent: str,
    boxes: dict[str, _Box],
    children_of: dict[str | None, list[str]],
    group_specs: dict[str, dict[str, Any]],
    node_specs: dict[str, dict[str, Any]],
    node_styles: dict[str, str],
    out: list[str],
) -> None:
    """Append ``box`` (and its children) as mxCells; geometry is parent-relative."""
    if box.is_group:
        spec = group_specs[box.id]
        style = GROUP_STYLE + _resolve_style(str(spec.get("color") or "none"), COLORS, "")
        raw = str(spec.get("style") or "").strip()
        if raw:
            style = raw if raw.endswith(";") else raw + ";"
        label = str(spec.get("label") or spec.get("value") or "")
    else:
        spec = node_specs[box.id]
        style = node_styles[box.id]
        label = str(spec.get("label") or spec.get("value") or "")

    out.append(
        f'        <mxCell id="{_attr(box.id)}" value="{_attr(label)}" '
        f'style="{_attr(style)}" vertex="1" parent="{_attr(parent)}">\n'
        f'          <mxGeometry x="{box.draw_x}" y="{box.y}" '
        f'width="{box.width}" height="{box.height}" as="geometry" />\n'
        f"        </mxCell>"
    )
    for child in children_of.get(box.id, []):
        _emit(
            boxes[child],
            box.id,
            boxes,
            children_of,
            group_specs,
            node_specs,
            node_styles,
            out,
        )


def _wrap_model(cells_xml: str, *, title: str, page: tuple[int, int] = (850, 1100)) -> str:
    """Wrap rendered ``<mxCell>`` markup in the ``mxfile``/``mxGraphModel`` shell.

    No ``modified`` timestamp and a *content-free, title-derived* diagram id:
    diagrams live in git-backed workspaces, so a regenerated file has to diff
    cleanly against the previous one instead of churning on every write.
    """
    name = title.strip() or "Page-1"
    diagram_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]
    return (
        '<mxfile host="precursor" agent="precursor" type="device">\n'
        f'  <diagram id="{_attr(diagram_id)}" name="{_attr(name)}">\n'
        '    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{max(page[0], 850)}" pageHeight="{max(page[1], 1100)}" '
        'math="0" shadow="0">\n'
        "      <root>\n"
        '        <mxCell id="0" />\n'
        '        <mxCell id="1" parent="0" />\n'
        f"{cells_xml}\n"
        "      </root>\n"
        "    </mxGraphModel>\n"
        "  </diagram>\n"
        "</mxfile>\n"
    )


def normalize_xml(xml: str, *, title: str = "Page-1") -> str:
    """Validate raw mxGraph markup and wrap a bare model in an ``mxfile``.

    Accepts what a model actually tends to emit — a full ``<mxfile>``, a bare
    ``<mxGraphModel>``, or a loose run of ``<mxCell>`` elements — and always
    returns a document draw.io will open.
    """
    text = (xml or "").strip()
    if not text:
        raise DiagramError("xml is empty")
    if len(text.encode("utf-8")) > _MAX_XML_BYTES:
        raise DiagramError("xml is too large")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        # A bare run of sibling <mxCell> elements has no single root; retry it
        # under a synthetic one before giving up.
        try:
            ET.fromstring(f"<root>{text}</root>")
        except ET.ParseError:
            raise DiagramError(f"xml is not well-formed: {exc}") from exc
        return _wrap_model(text, title=title)
    if root.tag == "mxfile":
        return text if text.endswith("\n") else text + "\n"
    if root.tag == "mxGraphModel":
        name = title.strip() or "Page-1"
        diagram_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]
        return (
            '<mxfile host="precursor" agent="precursor" type="device">\n'
            f'  <diagram id="{_attr(diagram_id)}" name="{_attr(name)}">\n'
            f"{text}\n"
            "  </diagram>\n"
            "</mxfile>\n"
        )
    if root.tag == "mxCell":
        return _wrap_model(text, title=title)
    raise DiagramError(
        f"Unexpected root element <{root.tag}>; expected mxfile, mxGraphModel or mxCell"
    )


def normalize_path(path: str) -> str:
    """Ensure ``path`` ends in a draw.io-recognised suffix."""
    cleaned = (path or "").strip().lstrip("/")
    if not cleaned:
        raise DiagramError("path is required")
    lowered = cleaned.lower()
    if lowered.endswith(_DIAGRAM_SUFFIXES):
        return cleaned
    return f"{cleaned}.drawio"


def _browse_root(ws: Workspace) -> Path:
    """File root for a workspace: ``workspaces_dir/<slug>[/<subdir>]``.

    Mirrors ``workspace_fs_server._browse_root`` — same reason: we don't want to
    import the workspaces router and drag in the LLM/git stack.
    """
    root = Path(get_settings().workspaces_dir) / ws.slug
    if ws.subdir:
        root = root / ws.subdir.strip("/")
    return root


async def _load_workspace(workspace_id: int) -> Workspace | None:
    async with SessionLocal() as session:
        return await session.get(Workspace, workspace_id)


async def _resolve_root(
    workspace_id: int,
) -> tuple[Path | None, Workspace | None, dict[str, Any] | None]:
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return None, None, {"error": f"Workspace {workspace_id} not found"}
    root = _browse_root(ws)
    if not root.exists():
        return None, None, {"error": "Workspace is not ready yet"}
    return root, ws, None


def _persist(root: Path, path: str, content: str, *, overwrite: bool, slug: str) -> dict[str, Any]:
    try:
        if overwrite:
            fs.write_text(root, path, content)
        else:
            fs.create_file(root, path, content)
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": f"File already exists: {path} (pass overwrite=true to replace it)"}
    return with_open_link(
        {"path": path, "written": True, "bytes": len(content.encode("utf-8"))},
        slug,
        path,
    )


@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List workspaces (id, slug, name) you can write diagrams into.

    Call this first to find the ``workspace_id`` the other tools need.
    """
    async with SessionLocal() as session:
        rows = (await session.execute(select(Workspace))).scalars().all()
    return {
        "workspaces": [
            {
                "id": w.id,
                "slug": w.slug,
                "name": w.name,
                "kind": w.kind,
                "ready": w.cloned_at is not None,
            }
            for w in rows
        ]
    }


@mcp.tool()
async def search_shapes(query: str, limit: int = 12) -> dict[str, Any]:
    """Find real Azure product icons by keyword, with their exact style strings.

    **Use this before drawing any cloud architecture.** draw.io's Azure library
    is made of SVG *images*, not ``mxgraph.azure2.*`` stencils — a guessed
    stencil name renders as a blank rectangle, which is how a diagram ends up as
    a grid of featureless boxes. Search returns the ``key`` you can pass as a
    node's ``shape`` (e.g. ``networking/firewalls``) and the ``style`` string if
    you'd rather paste it verbatim.

    Try service names as the user says them: "express route", "firewall",
    "bastion", "key vault", "virtual network gateway", "kubernetes".
    """
    hits = search_catalog(query, limit=max(1, min(limit, 50)))
    return {
        "query": query,
        "count": len(hits),
        "shapes": [
            {
                "key": h["key"],
                "name": h["name"],
                "category": h["category"],
                "style": h["style"],
                "width": h["width"],
                "height": h["height"],
            }
            for h in hits
        ],
        "hint": "Pass 'key' as a node's shape; sizes are applied automatically.",
    }


@mcp.tool()
async def list_shapes() -> dict[str, Any]:
    """List the generic shape, colour and edge presets ``create_diagram`` takes.

    These are the flowchart primitives. For **product icons** (Azure services,
    etc.) use ``search_shapes`` instead — this list deliberately doesn't inline
    the ~700-entry icon catalogue.
    """
    catalog = _catalog()
    return {
        "shapes": sorted(SHAPES),
        "colors": sorted(COLORS),
        "edge_styles": sorted(EDGE_STYLES),
        "icon_catalog": {
            "count": len(catalog),
            "categories": sorted({str(s["category"]) for s in catalog}),
            "search_with": "search_shapes",
        },
        "raw_style_hint": (
            "Any preset field also accepts a full mxGraph style string (anything "
            "containing '='), e.g. 'shape=mxgraph.aws4.lambda;'"
        ),
    }


@mcp.tool()
async def create_diagram(
    workspace_id: int,
    path: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    title: str = "Page-1",
    direction: str = "vertical",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a laid-out ``.drawio`` diagram in a workspace.

    Describe the *graph* and let the server place it — **never compute
    coordinates yourself**. Boxes are laid out in layers along ``direction``
    (``"vertical"`` = top-to-bottom, ``"horizontal"`` = left-to-right) and
    ordered to keep edge crossings down.

    ``nodes``: ``[{"id": "fw", "label": "Azure Firewall",
    "shape": "networking/firewalls", "group": "hub"}]``. ``shape`` takes a
    ``search_shapes`` key (real product icon), a generic preset from
    ``list_shapes``, or a raw mxGraph style. ``group`` puts the node inside a
    container. ``color`` tints plain boxes (it is ignored for icons, which carry
    their own colours). ``width``/``height``/``style`` override the defaults.

    ``groups``: containers, which may nest via their own ``group`` field —
    ``[{"id": "hub", "label": "Hub VNet 10.0.0.0/16", "color": "blue",
    "group": "regionA", "direction": "horizontal"}]``. Use them for regions,
    VNets, subnets and on-prem boundaries; each is sized to fit its children.

    ``edges``: ``[{"source": "spoke1", "target": "hub", "label": "peering",
    "style": "orthogonal"}]``. Endpoints may be nodes *or* groups.

    Returns ``notes`` whenever a ``shape`` was fuzzy-matched or couldn't be
    resolved — **read them**, since an unresolved shape is drawn as a plain box.
    """
    root, ws, failure = await _resolve_root(workspace_id)
    if root is None or ws is None:
        return failure or {"error": "Workspace unavailable"}
    try:
        target = normalize_path(path)
        xml, notes = build_diagram_xml(
            nodes, edges, groups=groups, title=title, direction=direction
        )
    except DiagramError as exc:
        return {"error": str(exc)}
    result = _persist(root, target, xml, overwrite=overwrite, slug=ws.slug)
    if "error" in result:
        return result
    return {
        **result,
        "nodes": len(nodes),
        "edges": len(edges or []),
        "groups": len(groups or []),
        "notes": notes,
    }


@mcp.tool()
async def write_diagram_xml(
    workspace_id: int,
    path: str,
    xml: str,
    title: str = "Page-1",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write raw mxGraph XML to a ``.drawio`` file in a workspace.

    The escape hatch for diagrams ``create_diagram`` can't express (swimlanes,
    hand-tuned waypoints) and the way to save an edited diagram back after
    ``read_diagram``. **Prefer ``create_diagram``** — hand-placed coordinates are
    the usual cause of overlapping labels and unreadable output.

    Accepts a full ``<mxfile>``, a bare ``<mxGraphModel>``, or a run of
    ``<mxCell>`` elements; the wrapper is added for you. Rejects markup that
    isn't well-formed. If you reference Azure icons here, take the style strings
    from ``search_shapes`` — ``shape=mxgraph.azure2.*`` does not exist and
    renders as an empty box.
    """
    root, ws, failure = await _resolve_root(workspace_id)
    if root is None or ws is None:
        return failure or {"error": "Workspace unavailable"}
    try:
        target = normalize_path(path)
        document = normalize_xml(xml, title=title)
    except DiagramError as exc:
        return {"error": str(exc)}
    return _persist(root, target, document, overwrite=overwrite, slug=ws.slug)


@mcp.tool()
async def read_diagram(workspace_id: int, path: str) -> dict[str, Any]:
    """Read a ``.drawio`` file back as XML, so you can edit and rewrite it."""
    root, ws, failure = await _resolve_root(workspace_id)
    if root is None or ws is None:
        return failure or {"error": "Workspace unavailable"}
    try:
        content = fs.read_text(root, path)
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except IsADirectoryError:
        return {"error": f"Not a file: {path}"}
    except UnicodeDecodeError:
        return {"error": f"File is not UTF-8 text (compressed diagram?): {path}"}
    if len(content.encode("utf-8")) > _MAX_XML_BYTES:
        return {"error": f"Diagram is too large to read: {path}"}
    return with_open_link({"path": path, "xml": content}, ws.slug, path)


def main() -> None:
    from precursor.backend.logging_config import configure_subprocess_logging

    configure_subprocess_logging()
    mcp.run()


if __name__ == "__main__":
    main()
