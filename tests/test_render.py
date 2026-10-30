import numpy as np
from PIL import Image
import pytest

from report_generator.core.models import (ChannelMapping, Component, Port, ReportConfig,
    SpiceModel, SpdData, TouchstoneData)
from report_generator.core import render


def test_geometry_rotation_bottom_mirror_and_unknown_dimensions(tmp_path, monkeypatch):
    config = ReportConfig(mappings=[ChannelMapping(site="0", channel="VDD", net="VDD")])
    spd = SpdData("synthetic.spd", {}, models={"C": SpiceModel("C", 1e-7, valid=True)},
        components=[Component("C1", "C", ["VDD", "GND"], x_mm=10, y_mm=20, rotation=90,
                              side="Bottom", width_mm=2, height_mm=1, is_cap=True),
                    Component("C2", "C", ["VDD", "GND"], x_mm=15, y_mm=20,
                              side="Bottom", is_cap=True)])
    captured = {}
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        ax = canvas.figure.axes[0]
        captured["vertices"] = ax.collections[0].get_paths()[0].vertices.copy()
        captured["limits"] = ax.get_xlim()
        captured["notes"] = [text.get_text() for text in canvas.figure.texts]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    path = render.render_placement(spd, config, "Bottom", tmp_path / "bottom.png")
    assert captured["vertices"][:4] == pytest.approx(np.array([[10.5, 19], [10.5, 21], [9.5, 21], [9.5, 19]]))
    assert captured["limits"][0] > captured["limits"][1]
    assert any("outline unavailable" in note for note in captured["notes"])
    with Image.open(path) as image:
        assert image.size == (1210, 990)
    config.settings.bottom_mirror = False
    render.render_placement(spd, config, "Bottom", tmp_path / "unmirrored.png")
    assert captured["limits"][0] < captured["limits"][1]


def test_group_plot_selected_ports_units_and_gaps(tmp_path, monkeypatch):
    ts = TouchstoneData("synthetic.s2p", {}, [Port(1, "P1"), Port(2, "P2")],
        np.array([1e5, 1e6, 1e7]), np.array([[1 + 1j, 7], [complex(np.nan, np.nan), 8], [3 + 4j, 9]]))
    config = ReportConfig(mappings=[ChannelMapping(1, site="0", channel="VDD", group="G"),
                                    ChannelMapping(2, site="1", channel="VDD", group="", selected=False)])
    captured = {}
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        ax = canvas.figure.axes[0]
        captured["lines"] = [(line.get_label(), line.get_ydata().copy()) for line in ax.lines]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "plot.png")
    assert len(captured["lines"]) == 1
    assert captured["lines"][0][0] == "VDD / 0"
    values = captured["lines"][0][1]
    assert values[0] == pytest.approx(1000 * 2**.5)
    assert np.isnan(values[1])
    assert values[2] == 5000


def test_alias_placement_selection_and_empty_errors(tmp_path):
    config = ReportConfig(mappings=[ChannelMapping(site="0", channel="VDD", net="alias")])
    spd = SpdData("synthetic.spd", {}, aliases={"alias": ["VDD"]},
        components=[Component("C1", "C", ["VDD", "GND"], x_mm=1, y_mm=2, is_cap=True)])
    assert len(render.placement_components(spd, config, "Top")) == 1
    with pytest.raises(ValueError, match="좌표"):
        render.render_placement(spd, config, "Bottom", tmp_path / "no.png")


def test_source_labels_are_literal_matplotlib_text(tmp_path):
    label = r"$\unsupported$"
    config = ReportConfig(mappings=[ChannelMapping(1, site="0", channel=label, group=label)])
    ts = TouchstoneData("synthetic.s1p", {}, [Port(1, "P1")], np.array([1e6]), np.array([[1 + 2j]]))
    path = render.render_group_plot(ts, config, label, tmp_path / "literal.png")
    with Image.open(path) as image:
        assert image.width > 0
