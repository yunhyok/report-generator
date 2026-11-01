import numpy as np
from PIL import Image
import pytest

from report_generator.core.models import (ChannelMapping, Component, PadShape, Port, ReportConfig,
    SpiceModel, SpdData, SurfacePad, TouchstoneData)
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
        captured["background"] = canvas.figure.get_facecolor()
        captured["axes_visible"] = ax.axison
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    path = render.render_placement(spd, config, "Bottom", tmp_path / "bottom.png")
    assert captured["vertices"][:4] == pytest.approx(np.array([[10.5, 19], [10.5, 21], [9.5, 21], [9.5, 19]]))
    assert captured["limits"][0] > captured["limits"][1]
    assert captured["notes"] == ["BOTTOM · mirrored"]
    assert captured["background"] == (0, 0, 0, 1)
    assert not captured["axes_visible"]
    with Image.open(path) as image:
        assert image.height == 990 and image.width > image.height
        assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 0)
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
        captured["background"] = canvas.figure.get_facecolor()
        captured["markers"] = [line.get_marker() for line in ax.lines]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "plot.png")
    assert len(captured["lines"]) == 1
    assert captured["lines"][0][0] == "VDD / 0"
    values = captured["lines"][0][1]
    assert values[0] == pytest.approx(1000 * 2**.5)
    assert np.isnan(values[1])
    assert values[2] == 5000
    assert captured["background"] == (1, 1, 1, 1)
    assert captured["markers"] == ["None"]


@pytest.mark.parametrize("board_type,fonts", [("MLO", (26, 14.5, 14, 15, 15)), ("PCB", (26, 9, 14, 9, 15))])
def test_group_plots_have_no_legend_or_port_count_dependent_height(tmp_path, monkeypatch, board_type, fonts):
    captured = []
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        fig, ax = canvas.figure, canvas.figure.axes[0]
        if isinstance(filename, str):
            captured.append((tuple(fig.get_size_inches()), fig.legends, ax.get_legend(), len(ax.lines), ax.get_position().y0))
            assert (ax.title.get_fontsize(), ax.xaxis.label.get_fontsize(), ax.yaxis.label.get_fontsize(),
                    ax.get_xticklabels()[0].get_fontsize(), ax.get_yticklabels()[0].get_fontsize()) == fonts
        assert ax.get_xscale() == ax.get_yscale() == "log"
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    for count in (1, 32):
        ts = TouchstoneData("synthetic", {}, [Port(i+1, f"P{i+1}") for i in range(count)],
                            np.array([1e3, 1e6, 1e9]), np.tile([[1], [2], [3]], (1, count)))
        config = ReportConfig(mappings=[ChannelMapping(i+1, site=f"SITE{i}", channel=f"RAIL{i % 3}", group="G") for i in range(count)])
        config.settings.board_type = board_type
        render.render_group_plot(ts, config, "G", tmp_path / f"group-{count}.png")
    assert captured[0][0] == captured[1][0]
    for _, figure_legends, axes_legend, _, bottom in captured:
        assert not figure_legends and axes_legend is None
        assert bottom < .16
    assert [row[3] for row in captured] == [1, 32]


def test_cad_placement_context_is_outline_and_cap_colors_follow_reference(tmp_path, monkeypatch):
    config = ReportConfig(mappings=[ChannelMapping(site="0", channel="RAIL", net="RAIL")])
    spd = SpdData("synthetic.spd", {}, models={"SMALL": SpiceModel("SMALL", 100e-9, valid=True), "LARGE": SpiceModel("LARGE", 1e-6, valid=True)},
        components=[Component("U2", "IC", x_mm=0, y_mm=0, width_mm=10, height_mm=10),
                    Component("C2", "SMALL", ["RAIL", "GND"], x_mm=1, y_mm=1, width_mm=1, height_mm=.5, is_cap=True),
                    Component("C3", "LARGE", ["RAIL", "GND"], x_mm=2, y_mm=2, width_mm=1, height_mm=.5, is_cap=True)])
    captured = {}
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        captured["faces"] = [collection.get_facecolors().copy() for collection in canvas.figure.axes[0].collections]
        captured["labels"] = [text.get_text() for legend in canvas.figure.legends for text in legend.get_texts()]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_placement(spd, config, "Top", tmp_path / "cad.png")
    assert any(not len(colors) for colors in captured["faces"])
    filled = np.concatenate([colors for colors in captured["faces"] if len(colors)])
    assert any(np.allclose(color, (1, 0, 0, 1)) for color in filled)
    assert any(np.allclose(color, (146/255, 208/255, 80/255, 1)) for color in filled)
    assert {"100nF (1)", "1uF (1)", "Other components / nets"}.issubset(captured["labels"])


def test_capacity_legend_counts_only_visible_active_caps_on_each_side(tmp_path, monkeypatch):
    def cap(refdes, model="SMALL", status="Automatic", side="Top", net="RAIL", visible=True):
        return Component(refdes, model, [net, "GND"], status=status, side=side,
                         x_mm=int(refdes[1:]) if visible else None, y_mm=0 if visible else None,
                         width_mm=1, height_mm=.5, is_cap=True)
    spd = SpdData("synthetic.spd", {}, models={"SMALL": SpiceModel("SMALL", 100e-9, valid=False)}, components=[
        cap("C2"), cap("C3", status="Enabled"), cap("C4", model="NULL"),
        cap("C5", status="Disabled"), cap("C6", status="Unknown"),
        cap("C7", side="Bottom"), cap("C8", model="NULL", side="Bottom"),
        cap("C9", net="OTHER"), cap("C10", visible=False),
        Component("U2", "IC", x_mm=0, y_mm=0, width_mm=10, height_mm=2),
    ])
    pad_only_cap = cap("C11", visible=False)
    pad_only_cap.pads = [SurfacePad(node, x, 2, "Bottom", PadShape("Circle", .5, .5))
                         for node, x in (("Node11", 11), ("Node12", 12))]
    spd.components.append(pad_only_cap)
    config = ReportConfig(mappings=[ChannelMapping(net="RAIL", site="SITE2", channel="RAIL")], component_overrides={"C5": 1e-6})
    captured = {}
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        captured["labels"] = [text.get_text() for legend in canvas.figure.legends for text in legend.get_texts()]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_placement(spd, config, "Top", tmp_path / "top-counts.png")
    assert {"100nF (2)", "Unknown capacitance (1)", "Other components / nets", "Inactive (2)"}.issubset(captured["labels"])
    assert not any(label.startswith("1uF") for label in captured["labels"])
    assert not any(label.startswith("Other components / nets (") for label in captured["labels"])
    render.render_placement(spd, config, "Bottom", tmp_path / "bottom-counts.png")
    assert {"100nF (2)", "Unknown capacitance (1)"}.issubset(captured["labels"])


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


def test_surface_pad_geometry_without_body_and_bottom_mirror(tmp_path, monkeypatch):
    rectangle = PadShape("Box", 2, 1, ((-1, -.5), (1, -.5), (1, .5), (-1, .5)))
    circle = PadShape("Circle", .5, .5)
    spd = SpdData("synthetic.spd", {}, components=[Component("U2", "IC", pads=[
        SurfacePad("Node1", 10, 20, "Top", rectangle, 90),
        SurfacePad("Node2", 3, 4, "Bottom", circle),
    ])])
    config = ReportConfig()
    captured = {}
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        ax = canvas.figure.axes[0]
        captured["paths"] = [path.vertices.copy() for collection in ax.collections for path in collection.get_paths()]
        captured["limits"] = ax.get_xlim()
        captured["bounds"] = ax.dataLim.bounds
        captured["legend"] = [text.get_text() for legend in canvas.figure.legends for text in legend.get_texts()]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_placement(spd, config, "Top", tmp_path / "pins-top.png")
    expected = np.array([[10.5, 19], [10.5, 21], [9.5, 21], [9.5, 19]])
    assert captured["paths"][-1][:4] == pytest.approx(expected)
    assert "Pin bounds (1)" in captured["legend"]
    render.render_placement(spd, config, "Bottom", tmp_path / "pins-bottom.png")
    assert captured["limits"][0] > captured["limits"][1]
    assert captured["bounds"] == pytest.approx((2.75, 3.75, .5, .5))
    config.settings.bottom_mirror = False
    render.render_placement(spd, config, "Bottom", tmp_path / "pins-bottom-unmirrored.png")
    assert captured["limits"][0] < captured["limits"][1]
