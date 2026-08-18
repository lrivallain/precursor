"""Tests for the built-in draw.io MCP server (layout, icons, sandboxing)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.mcp import drawio_server as srv

Geometry = tuple[int, int, int, int]


def _xml(*args: Any, **kwargs: Any) -> str:
    return srv.build_diagram_xml(*args, **kwargs)[0]


def _cells(xml: str) -> dict[str, ET.Element]:
    root = ET.fromstring(xml)
    model_root = root.find("./diagram/mxGraphModel/root")
    assert model_root is not None
    return {c.get("id") or "": c for c in model_root.findall("mxCell")}


def _geometry(cell: ET.Element) -> Geometry:
    geo = cell.find("mxGeometry")
    assert geo is not None
    return (
        int(geo.get("x") or 0),
        int(geo.get("y") or 0),
        int(geo.get("width") or 0),
        int(geo.get("height") or 0),
    )


def _assert_readable(xml: str) -> None:
    """No two siblings overlap and every child fits inside its container.

    This is the whole promise of server-side layout, so it's asserted as an
    invariant rather than spot-checked per scenario.
    """
    cells = {k: v for k, v in _cells(xml).items() if v.get("vertex") == "1"}
    boxes = {k: (_geometry(v), v.get("parent") or "") for k, v in cells.items()}

    siblings: dict[str, list[str]] = {}
    for key, (_, parent) in boxes.items():
        siblings.setdefault(parent, []).append(key)

    for parent, kids in siblings.items():
        for a, b in combinations(kids, 2):
            (ax, ay, aw, ah), _ = boxes[a]
            (bx, by, bw, bh), _ = boxes[b]
            overlaps = ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
            assert not overlaps, f"{a} overlaps {b} inside {parent}"

    for key, ((x, y, w, h), parent) in boxes.items():
        if parent not in boxes:
            continue
        (_, _, pw, ph), _ = boxes[parent]
        assert x >= 0 and y >= 0, f"{key} sits outside {parent}"
        assert x + w <= pw and y + h <= ph, f"{key} overflows {parent}"


def test_builds_a_well_formed_mxfile() -> None:
    xml = _xml(
        [{"id": "a", "label": "Start"}, {"id": "b", "label": "End"}],
        [{"source": "a", "target": "b", "label": "next"}],
    )

    cells = _cells(xml)
    assert cells["a"].get("value") == "Start"
    assert cells["a"].get("vertex") == "1"
    assert cells["e1"].get("edge") == "1"
    assert cells["e1"].get("source") == "a"
    assert cells["e1"].get("target") == "b"
    # The two implicit draw.io root cells must always be present.
    assert cells["0"] is not None and cells["1"].get("parent") == "0"


def test_layers_follow_edge_direction() -> None:
    xml = _xml(
        [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    )

    cells = _cells(xml)
    ys = [_geometry(cells[n])[1] for n in ("a", "b", "c")]
    assert ys[0] < ys[1] < ys[2]


def test_horizontal_direction_advances_on_x() -> None:
    xml = _xml(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b"}],
        direction="horizontal",
    )

    cells = _cells(xml)
    assert _geometry(cells["a"])[0] < _geometry(cells["b"])[0]
    assert _geometry(cells["a"])[1] == _geometry(cells["b"])[1]


def test_unconnected_boxes_flow_along_the_requested_direction() -> None:
    """Regression: layering put edgeless siblings *perpendicular* to `direction`,
    so a subnet asked to lay out horizontally came out as a vertical stack."""
    row = _cells(_xml([{"id": "a"}, {"id": "b"}], direction="horizontal"))
    assert _geometry(row["a"])[0] < _geometry(row["b"])[0]
    assert _geometry(row["a"])[1] == _geometry(row["b"])[1]

    column = _cells(_xml([{"id": "a"}, {"id": "b"}], direction="vertical"))
    assert _geometry(column["a"])[1] < _geometry(column["b"])[1]
    assert _geometry(column["a"])[0] == _geometry(column["b"])[0]


def test_many_unconnected_boxes_wrap_instead_of_running_away() -> None:
    xml = _xml([{"id": f"n{i}"} for i in range(9)], direction="horizontal")

    rows = {_geometry(_cells(xml)[f"n{i}"])[1] for i in range(9)}
    assert len(rows) > 1
    _assert_readable(xml)


def test_siblings_do_not_overlap() -> None:
    xml = _xml(
        [{"id": "root"}, {"id": "x"}, {"id": "y"}],
        [{"source": "root", "target": "x"}, {"source": "root", "target": "y"}],
    )

    _assert_readable(xml)


def test_cycles_do_not_hang_or_raise() -> None:
    xml = _xml(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    )

    cells = _cells(xml)
    # Both ends of the cycle are still placed, on distinct layers, and both
    # edges survive — the back-edge just points at an earlier layer.
    assert _geometry(cells["a"])[1] != _geometry(cells["b"])[1]
    assert cells["e2"].get("source") == "b" and cells["e2"].get("target") == "a"


def test_presets_and_raw_styles_both_resolve() -> None:
    xml = _xml(
        [
            {"id": "a", "shape": "decision", "color": "green"},
            {"id": "b", "shape": "shape=mxgraph.aws4.lambda;"},
        ]
    )

    cells = _cells(xml)
    assert "rhombus" in (cells["a"].get("style") or "")
    assert "#d5e8d4" in (cells["a"].get("style") or "")
    assert "mxgraph.aws4.lambda" in (cells["b"].get("style") or "")


def test_labels_are_xml_escaped() -> None:
    xml = _xml([{"id": "a", "label": '<b> & "quoted"'}])

    assert _cells(xml)["a"].get("value") == '<b> & "quoted"'


def test_output_is_deterministic() -> None:
    spec: list[dict[str, Any]] = [{"id": "a", "label": "A"}]
    assert _xml(spec) == _xml(spec)


def test_edge_labels_get_a_background() -> None:
    """An unbacked label lying across a container border is unreadable."""
    xml = _xml(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b", "label": "peering"}],
    )

    assert "labelBackgroundColor" in (_cells(xml)["e1"].get("style") or "")


def test_unknown_edge_endpoint_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        _xml([{"id": "a"}], [{"source": "a", "target": "ghost"}])


def test_duplicate_node_id_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        _xml([{"id": "a"}, {"id": "a"}])


def test_empty_node_list_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        _xml([])


# --- containers -------------------------------------------------------------


def test_nodes_are_nested_inside_their_group() -> None:
    xml = _xml(
        [{"id": "vm", "group": "vnet"}],
        groups=[{"id": "vnet", "label": "Hub VNet"}],
    )

    cells = _cells(xml)
    assert cells["vm"].get("parent") == "vnet"
    assert cells["vnet"].get("parent") == "1"
    _assert_readable(xml)


def test_groups_nest_and_contain_their_children() -> None:
    xml = _xml(
        [
            {"id": "fw", "group": "fwsub"},
            {"id": "gw", "group": "gwsub"},
            {"id": "app", "group": "spoke"},
        ],
        [{"source": "gw", "target": "fw"}, {"source": "fw", "target": "spoke"}],
        groups=[
            {"id": "hub", "label": "Hub VNet 10.0.0.0/16"},
            {"id": "gwsub", "label": "GatewaySubnet", "group": "hub"},
            {"id": "fwsub", "label": "AzureFirewallSubnet", "group": "hub"},
            {"id": "spoke", "label": "Spoke 1"},
        ],
    )

    cells = _cells(xml)
    assert cells["gwsub"].get("parent") == "hub"
    assert cells["fw"].get("parent") == "fwsub"
    _assert_readable(xml)


def test_group_grows_to_clear_a_long_title() -> None:
    """Regression: a fixed header let a wrapping title sit on the first child."""
    short = _cells(_xml([{"id": "a", "group": "g"}], groups=[{"id": "g", "label": "Hub"}]))
    long = _cells(
        _xml(
            [{"id": "a", "group": "g"}],
            groups=[{"id": "g", "label": "Spoke 1 — Production Landing Zone 10.1.0.0/16"}],
        )
    )

    assert _geometry(long["a"])[1] >= _geometry(short["a"])[1]
    assert _geometry(long["g"])[2] > _geometry(short["g"])[2]


def test_edges_between_groups_order_the_containers() -> None:
    xml = _xml(
        [{"id": "a", "group": "left"}, {"id": "b", "group": "right"}],
        [{"source": "a", "target": "b"}],
        groups=[{"id": "left"}, {"id": "right"}],
        direction="horizontal",
    )

    cells = _cells(xml)
    assert _geometry(cells["left"])[0] < _geometry(cells["right"])[0]


def test_unknown_group_reference_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        _xml([{"id": "a", "group": "nope"}])


# --- icon catalogue ---------------------------------------------------------


def test_catalog_is_vendored_and_uses_image_styles() -> None:
    """The whole point: draw.io's Azure library is images, not stencils.

    ``shape=mxgraph.azure2.*`` resolves to nothing and renders as a blank box,
    which is exactly the failure this catalogue exists to prevent.
    """
    shapes = srv._catalog()

    assert len(shapes) > 500
    for shape in shapes:
        assert shape["style"].startswith("image;"), shape["key"]
        assert "image=img/lib/azure2/" in shape["style"]
        assert "mxgraph.azure2" not in shape["style"]
        assert shape["width"] > 0 and shape["height"] > 0


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("firewall", "networking/firewalls"),
        ("express route", "networking/expressroute-circuits"),
        ("bastion", "networking/bastions"),
        ("key vault", "security/key-vaults"),
        ("storage account", "storage/storage-accounts"),
    ],
)
def test_search_finds_the_obvious_service(query: str, expected: str) -> None:
    assert srv.search_catalog(query, limit=1)[0]["key"] == expected


def test_catalog_key_resolves_to_a_real_icon() -> None:
    xml = _xml([{"id": "fw", "label": "Firewall", "shape": "networking/firewalls"}])

    style = _cells(xml)["fw"].get("style") or ""
    assert "image=img/lib/azure2/networking/Firewalls.svg" in style


def test_fuzzy_shape_match_is_reported() -> None:
    _, notes = srv.build_diagram_xml([{"id": "a", "shape": "azure firewall"}])

    assert any("networking/firewalls" in note for note in notes)


def test_unresolved_shape_is_reported_rather_than_silently_boxed() -> None:
    _, notes = srv.build_diagram_xml([{"id": "a", "shape": "zzzz nonexistent zzzz"}])

    assert any("Unknown shape" in note for note in notes)


def test_color_does_not_paint_over_an_icon() -> None:
    """Forcing a fill on an SVG icon is what turned Azure services into squares."""
    xml = _xml(
        [
            {"id": "icon", "shape": "networking/firewalls", "color": "blue"},
            {"id": "box", "shape": "process", "color": "blue"},
        ]
    )

    cells = _cells(xml)
    assert "fillColor" not in (cells["icon"].get("style") or "")
    assert "fillColor=#dae8fc" in (cells["box"].get("style") or "")


def test_icon_captions_reserve_horizontal_room() -> None:
    """A long caption under a 60px icon must not collide with its neighbour."""
    xml = _xml(
        [
            {"id": "a", "shape": "networking/firewalls", "label": "A"},
            {
                "id": "b",
                "shape": "networking/firewalls",
                "label": "Customer Edge Routers (HA pair)",
            },
            {"id": "c", "shape": "networking/firewalls", "label": "C"},
        ],
        direction="horizontal",
    )

    cells = _cells(xml)
    gap = _geometry(cells["c"])[0] - (_geometry(cells["b"])[0] + _geometry(cells["b"])[2])
    assert gap > srv._GAP_WITHIN


# --- raw XML + files --------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("flow", "flow.drawio"),
        ("docs/flow.drawio", "docs/flow.drawio"),
        ("/leading.xml", "leading.xml"),
    ],
)
def test_normalize_path_adds_a_diagram_suffix(given: str, expected: str) -> None:
    assert srv.normalize_path(given) == expected


def test_normalize_xml_wraps_a_bare_model() -> None:
    wrapped = srv.normalize_xml("<mxGraphModel><root /></mxGraphModel>")

    assert ET.fromstring(wrapped).tag == "mxfile"


def test_normalize_xml_wraps_loose_cells() -> None:
    wrapped = srv.normalize_xml('<mxCell id="0" /><mxCell id="1" parent="0" />')

    assert "0" in _cells(wrapped)


def test_normalize_xml_passes_an_mxfile_through() -> None:
    document = _xml([{"id": "a"}])

    assert srv.normalize_xml(document) == document


def test_normalize_xml_rejects_malformed_markup() -> None:
    with pytest.raises(srv.DiagramError):
        srv.normalize_xml("<mxGraphModel><root>")


def test_persist_refuses_to_escape_the_workspace(tmp_path: Path) -> None:
    result = srv._persist(tmp_path, "../escape.drawio", "<mxfile />", overwrite=False)

    assert "error" in result
    assert not (tmp_path.parent / "escape.drawio").exists()


def test_persist_will_not_clobber_without_overwrite(tmp_path: Path) -> None:
    (tmp_path / "a.drawio").write_text("original", encoding="utf-8")

    blocked = srv._persist(tmp_path, "a.drawio", "<mxfile />", overwrite=False)
    assert "error" in blocked
    assert (tmp_path / "a.drawio").read_text(encoding="utf-8") == "original"

    allowed = srv._persist(tmp_path, "a.drawio", "<mxfile />", overwrite=True)
    assert allowed["written"] is True
    assert (tmp_path / "a.drawio").read_text(encoding="utf-8") == "<mxfile />"


def test_shape_defaults_respect_natural_aspect() -> None:
    xml = _xml([{"id": "a", "shape": "actor"}, {"id": "b", "shape": "process"}])

    cells = _cells(xml)
    assert _geometry(cells["a"])[2:] == srv.SHAPE_SIZES["actor"]
    assert _geometry(cells["b"])[2:] == (srv._DEFAULT_WIDTH, srv._DEFAULT_HEIGHT)


def test_explicit_size_overrides_the_shape_default() -> None:
    xml = _xml([{"id": "a", "shape": "actor", "width": 200, "height": 90}])

    assert _geometry(_cells(xml)["a"])[2:] == (200, 90)


def test_drawio_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        entry = next((s for s in r.json() if s["name"] == "drawio"), None)
        assert entry is not None
        assert entry["builtin"] is True
        assert entry["transport"] == "stdio"
