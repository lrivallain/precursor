"""Built-in MCP server: draw.io diagram authoring (sandboxed).

Runs as a stdio subprocess (like ``workspace_fs_server``) and writes native
``.drawio`` files — plain mxGraph XML — into a Workspace working tree. Every
path is routed through :func:`workspace_fs.safe_join`, so nothing outside
``workspaces_dir/<slug>`` is ever reachable, and the result lands in a
git-backed tree the user can commit from the Workspace UI.

The point of the high-level ``create_diagram`` tool is **layout**: models write
plausible mxGraph markup but pick terrible ``x``/``y`` coordinates, so shapes
overlap and edges cross. Here the model supplies a graph (nodes + edges) and the
server derives a layered layout from it.

Tools:
- ``list_workspaces()`` — discover workspace ids to write into.
- ``list_shapes()`` — the shape/colour/edge presets, so styles aren't invented.
- ``create_diagram(workspace_id, path, nodes, edges=None, ...)`` — auto-layout.
- ``write_diagram_xml(workspace_id, path, xml, ...)` — raw mxGraph escape hatch.
- ``read_diagram(workspace_id, path)` — read a diagram back for editing.

Output is **deterministic**: no timestamps or random ids, so regenerating a
diagram produces an empty git diff when nothing changed.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import Workspace
from precursor.backend.services import workspace_fs as fs

mcp = FastMCP("drawio")

# Suffixes we accept as "already a diagram file"; anything else gets `.drawio`
# appended so the file opens in draw.io / the VS Code extension by double-click.
_DIAGRAM_SUFFIXES = (".drawio", ".xml", ".drawio.xml")
_MAX_NODES = 500
_MAX_XML_BYTES = 2_000_000

_DEFAULT_WIDTH = 160
_DEFAULT_HEIGHT = 60
# Gaps between sibling nodes in a layer, and between layers.
_GAP_WITHIN = 40
_GAP_BETWEEN = 70
_MARGIN = 40

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
    "database": (
        "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;"
    ),
    "cloud": "ellipse;shape=cloud;whiteSpace=wrap;html=1;",
    "note": "shape=note;whiteSpace=wrap;html=1;backgroundOutline=1;darkOpacity=0.05;size=14;",
    "actor": (
        "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;"
    ),
    "component": ("shape=component;align=left;spacingLeft=36;whiteSpace=wrap;html=1;dropTarget=0;"),
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

EDGE_STYLES: dict[str, str] = {
    "orthogonal": "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;",
    "rounded": "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;",
    "curved": "edgeStyle=orthogonalEdgeStyle;curved=1;rounded=0;html=1;",
    "straight": "html=1;",
    "dashed": "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;",
    "bidirectional": (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;startArrow=classic;startFill=1;"
    ),
    "plain": "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=none;",
}


class DiagramError(ValueError):
    """Raised when a caller-supplied graph spec can't be turned into a diagram."""


def _attr(value: str) -> str:
    """Escape ``value`` for use inside a double-quoted XML attribute."""
    return escape(value, {'"': "&quot;", "\n": "&#10;"})


def _resolve_style(preset: str | None, table: dict[str, str], fallback: str) -> str:
    """Look ``preset`` up in ``table``, or pass it through when it's raw mxGraph.

    A value containing ``=`` is already a style string (``shape=cylinder3;…``),
    so we hand it straight to draw.io instead of failing on an unknown key —
    that keeps the full mxGraph vocabulary (AWS/Azure shape libraries included)
    reachable without enumerating it here.
    """
    key = (preset or "").strip()
    if not key:
        return fallback
    if "=" in key:
        return key if key.endswith(";") else key + ";"
    return table.get(key.lower(), fallback)


def _node_style(node: dict[str, Any]) -> str:
    raw = str(node.get("style") or "").strip()
    if raw:
        return raw if raw.endswith(";") else raw + ";"
    style = _resolve_style(node.get("shape"), SHAPES, SHAPES["process"])
    color = str(node.get("color") or "").strip()
    if color:
        style += _resolve_style(color, COLORS, "")
    return style


def assign_layers(node_ids: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Group ``node_ids`` into layers so every edge points at a later layer.

    Longest-path layering over a Kahn traversal. Cycles are tolerated rather
    than rejected — a state machine or a retry loop is a perfectly reasonable
    thing to diagram — by parking whatever the traversal couldn't reach just
    after its deepest resolved predecessor.
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


def build_diagram_xml(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    *,
    title: str = "Page-1",
    direction: str = "vertical",
) -> str:
    """Render ``nodes``/``edges`` as a complete, laid-out ``.drawio`` document."""
    if not nodes:
        raise DiagramError("At least one node is required")
    if len(nodes) > _MAX_NODES:
        raise DiagramError(f"Too many nodes ({len(nodes)} > {_MAX_NODES})")
    if direction not in ("vertical", "horizontal"):
        raise DiagramError("direction must be 'vertical' or 'horizontal'")

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for position, node in enumerate(nodes):
        node_id = str(node.get("id") or "").strip() or f"n{position + 1}"
        if node_id in by_id:
            raise DiagramError(f"Duplicate node id: {node_id}")
        by_id[node_id] = node
        order.append(node_id)

    pairs: list[tuple[str, str]] = []
    for edge in edges or []:
        src = str(edge.get("source") or "").strip()
        dst = str(edge.get("target") or "").strip()
        if src not in by_id:
            raise DiagramError(f"Edge source '{src}' is not a known node id")
        if dst not in by_id:
            raise DiagramError(f"Edge target '{dst}' is not a known node id")
        pairs.append((src, dst))

    layers = assign_layers(order, pairs)
    geometry = _layout(layers, by_id, direction)

    cells: list[str] = []
    for node_id in order:
        node = by_id[node_id]
        x, y, width, height = geometry[node_id]
        label = _attr(str(node.get("label") or node.get("value") or ""))
        cells.append(
            f'        <mxCell id="{_attr(node_id)}" value="{label}" '
            f'style="{_attr(_node_style(node))}" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{width}" height="{height}" '
            f'as="geometry" />\n'
            f"        </mxCell>"
        )

    for position, edge in enumerate(edges or []):
        style = _resolve_style(str(edge.get("style") or ""), EDGE_STYLES, EDGE_STYLES["orthogonal"])
        label = _attr(str(edge.get("label") or ""))
        cells.append(
            f'        <mxCell id="e{position + 1}" value="{label}" style="{_attr(style)}" '
            f'edge="1" parent="1" source="{_attr(pairs[position][0])}" '
            f'target="{_attr(pairs[position][1])}">\n'
            f'          <mxGeometry relative="1" as="geometry" />\n'
            f"        </mxCell>"
        )

    return _wrap_model("\n".join(cells), title=title)


def _layout(
    layers: list[list[str]],
    by_id: dict[str, dict[str, Any]],
    direction: str,
) -> dict[str, tuple[int, int, int, int]]:
    """Place every node, centring each layer against the widest one."""

    def size(node_id: str) -> tuple[int, int]:
        node = by_id[node_id]
        shape = str(node.get("shape") or "").strip().lower()
        default = SHAPE_SIZES.get(shape, (_DEFAULT_WIDTH, _DEFAULT_HEIGHT))
        return (
            max(int(node.get("width") or default[0]), 20),
            max(int(node.get("height") or default[1]), 20),
        )

    # "Cross" is the axis nodes spread along within a layer; "main" is the axis
    # layers advance along. Swapping the two is the whole of `direction`.
    spans: list[int] = []
    for layer in layers:
        sizes = [size(n) for n in layer]
        extent = sum(w if direction == "vertical" else h for w, h in sizes)
        spans.append(extent + _GAP_WITHIN * max(len(layer) - 1, 0))
    widest = max(spans, default=0)

    geometry: dict[str, tuple[int, int, int, int]] = {}
    main = _MARGIN
    for index, layer in enumerate(layers):
        cross = _MARGIN + (widest - spans[index]) // 2
        thickest = 0
        for node_id in layer:
            width, height = size(node_id)
            if direction == "vertical":
                geometry[node_id] = (cross, main, width, height)
                cross += width + _GAP_WITHIN
                thickest = max(thickest, height)
            else:
                geometry[node_id] = (main, cross, width, height)
                cross += height + _GAP_WITHIN
                thickest = max(thickest, width)
        main += thickest + _GAP_BETWEEN
    return geometry


def _wrap_model(cells_xml: str, *, title: str) -> str:
    """Wrap rendered ``<mxCell>`` markup in the ``mxfile``/``mxGraphModel`` shell.

    No ``modified`` timestamp and a *content-free, title-derived* diagram id:
    diagrams live in git-backed workspaces, so a regenerated file has to diff
    cleanly against the previous one instead of churning on every write.
    """
    page = title.strip() or "Page-1"
    diagram_id = hashlib.sha1(page.encode("utf-8")).hexdigest()[:20]
    return (
        '<mxfile host="precursor" agent="precursor" type="device">\n'
        f'  <diagram id="{_attr(diagram_id)}" name="{_attr(page)}">\n'
        '    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        'pageWidth="850" pageHeight="1100" math="0" shadow="0">\n'
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
        page = title.strip() or "Page-1"
        diagram_id = hashlib.sha1(page.encode("utf-8")).hexdigest()[:20]
        return (
            '<mxfile host="precursor" agent="precursor" type="device">\n'
            f'  <diagram id="{_attr(diagram_id)}" name="{_attr(page)}">\n'
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


async def _resolve_root(workspace_id: int) -> tuple[Path | None, dict[str, Any] | None]:
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return None, {"error": f"Workspace {workspace_id} not found"}
    root = _browse_root(ws)
    if not root.exists():
        return None, {"error": "Workspace is not ready yet"}
    return root, None


def _persist(root: Path, path: str, content: str, *, overwrite: bool) -> dict[str, Any]:
    try:
        if overwrite:
            fs.write_text(root, path, content)
        else:
            fs.create_file(root, path, content)
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": f"File already exists: {path} (pass overwrite=true to replace it)"}
    return {"path": path, "written": True, "bytes": len(content.encode("utf-8"))}


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
async def list_shapes() -> dict[str, Any]:
    """List the shape, colour and edge presets ``create_diagram`` understands.

    Call this before building a diagram so you use real preset names. Any field
    that takes a preset also accepts a raw mxGraph style string (anything
    containing ``=``), which is how you reach the AWS/Azure/UML shape libraries.
    """
    return {
        "shapes": sorted(SHAPES),
        "colors": sorted(COLORS),
        "edge_styles": sorted(EDGE_STYLES),
        "raw_style_hint": (
            "Pass a full mxGraph style instead of a preset name to use any draw.io "
            "shape, e.g. 'sketch=0;points=[[0,0,0]];shape=mxgraph.aws4.lambda;'"
        ),
    }


@mcp.tool()
async def create_diagram(
    workspace_id: int,
    path: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    title: str = "Page-1",
    direction: str = "vertical",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a laid-out ``.drawio`` diagram in a workspace.

    Describe the *graph* and let the server place it — do not compute
    coordinates yourself. Nodes are laid out in layers along ``direction``
    (``"vertical"`` = top-to-bottom, ``"horizontal"`` = left-to-right), ordered
    to keep edge crossings down.

    ``nodes``: ``[{"id": "api", "label": "API", "shape": "process",
    "color": "blue"}]`` — ``id`` is what edges reference; ``shape``/``color``
    are presets from ``list_shapes`` (or raw mxGraph styles). Optional
    ``width``/``height`` and a raw ``style`` override are supported.

    ``edges``: ``[{"source": "api", "target": "db", "label": "reads",
    "style": "orthogonal"}]``.

    ``path`` gains a ``.drawio`` suffix if it doesn't have one. Existing files
    are kept unless ``overwrite=True``.
    """
    root, failure = await _resolve_root(workspace_id)
    if root is None:
        return failure or {"error": "Workspace unavailable"}
    try:
        target = normalize_path(path)
        xml = build_diagram_xml(nodes, edges, title=title, direction=direction)
    except DiagramError as exc:
        return {"error": str(exc)}
    result = _persist(root, target, xml, overwrite=overwrite)
    if "error" in result:
        return result
    return {**result, "nodes": len(nodes), "edges": len(edges or [])}


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
    absolute placement, hand-tuned waypoints) and the way to save an edited
    diagram back after ``read_diagram``. Accepts a full ``<mxfile>``, a bare
    ``<mxGraphModel>``, or a run of ``<mxCell>`` elements — the wrapper is added
    for you. Rejects markup that isn't well-formed.
    """
    root, failure = await _resolve_root(workspace_id)
    if root is None:
        return failure or {"error": "Workspace unavailable"}
    try:
        target = normalize_path(path)
        document = normalize_xml(xml, title=title)
    except DiagramError as exc:
        return {"error": str(exc)}
    return _persist(root, target, document, overwrite=overwrite)


@mcp.tool()
async def read_diagram(workspace_id: int, path: str) -> dict[str, Any]:
    """Read a ``.drawio`` file back as XML, so you can edit and rewrite it."""
    root, failure = await _resolve_root(workspace_id)
    if root is None:
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
    return {"path": path, "xml": content}


def main() -> None:
    from precursor.backend.logging_config import configure_subprocess_logging

    configure_subprocess_logging()
    mcp.run()


if __name__ == "__main__":
    main()
