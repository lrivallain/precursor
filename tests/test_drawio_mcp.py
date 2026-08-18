"""Tests for the built-in draw.io MCP server (layout, XML shape, sandboxing)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.mcp import drawio_server as srv


def _cells(xml: str) -> dict[str, ET.Element]:
    root = ET.fromstring(xml)
    model_root = root.find("./diagram/mxGraphModel/root")
    assert model_root is not None
    return {c.get("id") or "": c for c in model_root.findall("mxCell")}


def _geometry(cell: ET.Element) -> tuple[int, int, int, int]:
    geo = cell.find("mxGeometry")
    assert geo is not None
    return (
        int(geo.get("x") or 0),
        int(geo.get("y") or 0),
        int(geo.get("width") or 0),
        int(geo.get("height") or 0),
    )


def test_builds_a_well_formed_mxfile() -> None:
    xml = srv.build_diagram_xml(
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
    xml = srv.build_diagram_xml(
        [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
    )

    cells = _cells(xml)
    ys = [_geometry(cells[n])[1] for n in ("a", "b", "c")]
    assert ys[0] < ys[1] < ys[2]


def test_horizontal_direction_advances_on_x() -> None:
    xml = srv.build_diagram_xml(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b"}],
        direction="horizontal",
    )

    cells = _cells(xml)
    assert _geometry(cells["a"])[0] < _geometry(cells["b"])[0]
    assert _geometry(cells["a"])[1] == _geometry(cells["b"])[1]


def test_siblings_do_not_overlap() -> None:
    xml = srv.build_diagram_xml(
        [{"id": "root"}, {"id": "x"}, {"id": "y"}],
        [{"source": "root", "target": "x"}, {"source": "root", "target": "y"}],
    )

    cells = _cells(xml)
    left, right = sorted((_geometry(cells["x"]), _geometry(cells["y"])))
    assert left[0] + left[2] <= right[0]


def test_cycles_do_not_hang_or_raise() -> None:
    xml = srv.build_diagram_xml(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    )

    cells = _cells(xml)
    # Both ends of the cycle are still placed, on distinct layers, and both
    # edges survive — the back-edge just points at an earlier layer.
    assert _geometry(cells["a"])[1] != _geometry(cells["b"])[1]
    assert cells["e2"].get("source") == "b" and cells["e2"].get("target") == "a"


def test_presets_and_raw_styles_both_resolve() -> None:
    xml = srv.build_diagram_xml(
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
    xml = srv.build_diagram_xml([{"id": "a", "label": '<b> & "quoted"'}])

    assert _cells(xml)["a"].get("value") == '<b> & "quoted"'


def test_output_is_deterministic() -> None:
    spec: list[dict[str, object]] = [{"id": "a", "label": "A"}]
    assert srv.build_diagram_xml(spec) == srv.build_diagram_xml(spec)


def test_unknown_edge_endpoint_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        srv.build_diagram_xml([{"id": "a"}], [{"source": "a", "target": "ghost"}])


def test_duplicate_node_id_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        srv.build_diagram_xml([{"id": "a"}, {"id": "a"}])


def test_empty_node_list_is_rejected() -> None:
    with pytest.raises(srv.DiagramError):
        srv.build_diagram_xml([])


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
    document = srv.build_diagram_xml([{"id": "a"}])

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


def test_drawio_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        entry = next((s for s in r.json() if s["name"] == "drawio"), None)
        assert entry is not None
        assert entry["builtin"] is True
        assert entry["transport"] == "stdio"


def test_shape_defaults_respect_natural_aspect() -> None:
    xml = srv.build_diagram_xml([{"id": "a", "shape": "actor"}, {"id": "b", "shape": "process"}])

    cells = _cells(xml)
    assert _geometry(cells["a"])[2:] == srv.SHAPE_SIZES["actor"]
    assert _geometry(cells["b"])[2:] == (srv._DEFAULT_WIDTH, srv._DEFAULT_HEIGHT)


def test_explicit_size_overrides_the_shape_default() -> None:
    xml = srv.build_diagram_xml([{"id": "a", "shape": "actor", "width": 200, "height": 90}])

    assert _geometry(_cells(xml)["a"])[2:] == (200, 90)
