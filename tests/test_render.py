from functools import wraps

import numpy as np
from PIL import Image
import pytest

from report_generator.core.models import (ChannelMapping, Component, PadShape, Port, ReportConfig,
    SpiceModel, SpdData, SurfacePad, TouchstoneData)
from report_generator.core import render
from report_generator.core.touchstone import (
    PLOT_POINTS_PER_DECADE, interpolate_impedance, plot_frequency_bounds, plot_impedance,
)


@pytest.mark.parametrize("names,title,legend", [
    (("", ""), "G", None),
    (("Alias_A", "Alias_A"), "Alias_A", None),
    (("Alias_A", ""), "G", ["Alias_A (RAW) / SITE0", "RAW / SITE1"]),
    (("_Rail$X$", "Alias_B"), "G", ["_Rail$X$ (RAW) / SITE0", "Alias_B (RAW) / SITE1"]),
])
def test_group_display_names_keep_members_and_show_partial_site_aliases(tmp_path, monkeypatch, names, title, legend):
    config = ReportConfig(mappings=[
        ChannelMapping(index + 1, channel="RAW", site=f"SITE{index}", group="G", display_name=name)
        for index, name in enumerate(names)
    ])
    config.mappings.append(ChannelMapping(1, channel="EXCLUDED", site="SITE0", group="G",
                                          selected=False, display_name="Not eligible"))
    ts = TouchstoneData("synthetic", {}, [Port(1, "P1"), Port(2, "P2")],
                       np.array([1e3, 1e6, 1e9]), np.array([[1, 2]] * 3, dtype=complex))
    captured = {}
    def capture(figure, path):
        ax = figure.axes[0]
        captured["title"] = ax.get_title()
        captured["lines"] = len(ax.lines)
        actual = ax.get_legend()
        captured["legend"] = [text.get_text() for text in actual.get_texts()] if actual else None
        if actual:
            assert all(not text.get_parse_math() for text in actual.get_texts())
        return str(path)
    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "aliases.png")
    assert captured == {"title": title, "lines": 2, "legend": legend}


def test_many_mixed_names_render_outside_axes_and_inside_png(tmp_path, monkeypatch):
    count = 36
    config = ReportConfig(mappings=[
        ChannelMapping(index + 1, channel=f"RAW_RAIL_{index:02}", site=f"SITE{index}",
                       group="Mixed_names", display_name=f"VDD_RAIL_{index:02}")
        for index in range(count)
    ])
    ts = TouchstoneData("synthetic", {}, [Port(i + 1, f"P{i + 1}") for i in range(count)],
                       np.logspace(3, 9, 30),
                       np.logspace(-2, 1, 30)[:, None] * np.arange(1, count + 1)[None, :])
    captured = {}
    original = render.FigureCanvasAgg.print_png
    @wraps(original)
    def capture(canvas, filename, *args, **kwargs):
        canvas.draw()
        ax = canvas.figure.axes[0]
        legend = ax.get_legend()
        box = legend.get_window_extent(canvas.get_renderer())
        bounds = canvas.figure.bbox
        assert box.x0 >= ax.bbox.x1
        assert box.y1 == pytest.approx(ax.bbox.y1)
        assert box.x0 >= bounds.x0 and box.x1 <= bounds.x1
        assert box.y0 >= bounds.y0 and box.y1 <= bounds.y1
        assert ax.bbox.width >= 1000 and ax.bbox.height >= 400
        assert len(ax.lines) == count
        captured["labels"] = [text.get_text() for text in legend.get_texts()]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    path = render.render_group_plot(ts, config, "Mixed_names", tmp_path / "mixed-names.png")
    assert captured["labels"] == [
        f"VDD_RAIL_{i:02} (RAW_RAIL_{i:02}) / SITE{i}" for i in range(count)]
    with Image.open(path) as image:
        assert image.width > 1320 and image.height > 594


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
    spd.components[0].outline_vertices = ((-1, 0), (1, 0), (0, 1))
    render.render_placement(spd, config, "Bottom", tmp_path / "unmirrored.png")
    assert captured["vertices"] == pytest.approx(np.array([[10, 19], [10, 21], [9, 20], [10, 19]]))
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
        captured["frequencies"] = ax.lines[0].get_xdata() * 1e6
        captured["background"] = canvas.figure.get_facecolor()
        captured["markers"] = [line.get_marker() for line in ax.lines]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "plot.png")
    assert len(captured["lines"]) == 1
    assert captured["lines"][0][0] == "VDD / 0"
    values = captured["lines"][0][1]
    assert values[0] == pytest.approx(1000 * 2**.5)
    assert values[-1] == 5000
    assert np.isnan(values[1:-1]).all()
    assert 1e6 in captured["frequencies"]
    assert captured["background"] == (1, 1, 1, 1)
    assert captured["markers"] == ["None"]


@pytest.mark.parametrize("board_type,width,factor,unit", [("MLO", None, 1000, "mΩ"), ("PCB", 2.5, 1, "Ω")])
@pytest.mark.parametrize("count,channel_count", [(4, 1), (4, 2), (14, 1), (14, 2)])
def test_group_plot_solid_width_and_distinct_site_channel_colors(
        tmp_path, monkeypatch, board_type, width, factor, unit, count, channel_count):
    config = ReportConfig(mappings=[
        ChannelMapping(index + 1, channel=f"RAIL{index % channel_count}",
                       site=f"SITE{index // channel_count}", group="G", display_name=f"Name{index}")
        for index in range(count)
    ])
    config.settings.board_type = board_type
    if width is not None:
        config.settings.plot_line_width_pt = width
    values = np.tile([[1], [np.nan], [3]], (1, count)).astype(complex)
    ts = TouchstoneData("synthetic", {}, [Port(i + 1, f"P{i + 1}") for i in range(count)],
                       np.array([1e3, 1e6, 1e9]), values)

    def capture(figure, path):
        ax = figure.axes[0]
        assert len(ax.lines) == count
        assert all(line.get_linestyle() == "-" for line in ax.lines)
        assert all(line.get_linewidth() == (1.0 if width is None else width) for line in ax.lines)
        colors = [line.get_color() for line in ax.lines]
        assert len(set(colors)) == count
        if count <= len(render.COLORS):
            assert colors == render.COLORS[:count]
        assert ax.get_ylabel() == f"|Zii| [{unit}]"
        assert [text.get_text() for text in ax.get_legend().get_texts()] == [
            line.get_label() for line in ax.lines]
        for line, mapping in zip(ax.lines, config.mappings):
            frequency, magnitude = plot_impedance(ts, mapping.port_index)
            np.testing.assert_array_equal(line.get_xdata(), frequency / 1e6)
            np.testing.assert_array_equal(line.get_ydata(), magnitude * factor)
            assert np.isnan(line.get_ydata()[frequency == 1e6]).all()
            assert line.get_label() == f"{mapping.display_name} ({mapping.channel}) / {mapping.site}"
        return str(path)

    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "solid-lines.png")


def test_display_sampling_keeps_raw_extrema_boundaries_and_minimum_log_density():
    ts = TouchstoneData("synthetic", {}, [Port(1, "P1")],
                       np.array([0., 1e4, 1.23e6, 1.24e6, 4e9]),
                       np.array([[1 + 1j], [2 + 2j], [100], [1 + 3j], [4 + 4j]]))
    before_f, before_z = ts.frequency_hz.copy(), ts.z_diagonal.copy()
    before_pi = interpolate_impedance(ts, 1, 1e8)
    frequency, magnitude = plot_impedance(ts, 1)
    assert frequency[0] == 1e3 and frequency[-1] == 2e9
    assert np.max(frequency[1:] / frequency[:-1]) <= 10 ** (1 / PLOT_POINTS_PER_DECADE) * (1 + 1e-12)
    for original_f, original_z in zip(before_f[1:-1], before_z[1:-1, 0]):
        assert magnitude[np.flatnonzero(frequency == original_f)[0]] == abs(original_z)
    assert magnitude[np.flatnonzero(frequency == 1.23e6)[0]] == 100
    for boundary in (1e3, 2e9):
        assert magnitude[np.flatnonzero(frequency == boundary)[0]] == pytest.approx(interpolate_impedance(ts, 1, boundary)[0])
    assert magnitude[5] == pytest.approx(interpolate_impedance(ts, 1, float(frequency[5]))[0])
    np.testing.assert_array_equal(ts.frequency_hz, before_f)
    np.testing.assert_array_equal(ts.z_diagonal, before_z)
    assert interpolate_impedance(ts, 1, 1e8) == before_pi


def test_display_sampling_does_not_interpolate_across_zero_or_invalid_source_points():
    ts = TouchstoneData("synthetic", {}, [Port(1, "P1")],
                       np.array([100., 2e3, 3e3, 4e3, 5e3, 1e4, 3e9]),
                       np.array([[np.nan], [1], [0], [2], [np.nan], [3], [4]], dtype=complex))
    frequency, magnitude = plot_impedance(ts, 1)
    assert np.isnan(magnitude[0])  # The 1kHz boundary is bracketed by an invalid sample.
    assert magnitude[frequency == 2e3][0] == 1
    assert magnitude[frequency == 4e3][0] == 2
    gaps = ((frequency > 2e3) & (frequency < 4e3)) | ((frequency > 4e3) & (frequency < 1e4))
    assert np.isnan(magnitude[gaps]).all()
    assert np.isnan(magnitude[frequency == 3e3][0])
    continuous = np.isfinite(magnitude[:-1]) & np.isfinite(magnitude[1:])
    assert np.max((frequency[1:] / frequency[:-1])[continuous]) <= 10 ** (1 / 30) * (1 + 1e-12)
    ts.z_diagonal[-1, 0] = 0
    _, invalid_upper = plot_impedance(ts, 1)
    assert np.isnan(invalid_upper[-1])  # A zero beyond 2GHz also blocks boundary interpolation.


@pytest.mark.parametrize("count", [31, 121, 1001])
def test_dense_display_decade_retains_exact_original_points(count):
    frequency = np.geomspace(1e3, 1e4, count)
    z = (np.arange(count) + 1j * np.arange(count, 0, -1))[:, None]
    ts = TouchstoneData("synthetic", {}, [Port(1, "P1")], frequency, z)
    plotted_f, plotted_y = plot_impedance(ts, 1)
    np.testing.assert_array_equal(plotted_f, frequency)
    np.testing.assert_array_equal(plotted_y, np.abs(z[:, 0]))


@pytest.mark.parametrize("coverage,expected", [
    ([100., 4e9], (1e3, 2e9)), ([5e3, 8e8], (5e3, 8e8)),
    ([2e3], (2e3, 2e3)), ([0., 500.], None), ([3e9, 4e9], None),
])
def test_plot_source_coverage_and_sparse_bracketing(tmp_path, monkeypatch, coverage, expected):
    ts = TouchstoneData("synthetic", {}, [Port(1, "P1")], np.array(coverage),
                       np.ones((len(coverage), 1), dtype=complex))
    assert plot_frequency_bounds(ts.frequency_hz) == expected
    frequency, magnitude = plot_impedance(ts, 1)
    if expected is None:
        assert not frequency.size and not magnitude.size
        return
    assert (frequency[0], frequency[-1]) == expected
    assert np.all(magnitude == 1)
    config = ReportConfig(mappings=[ChannelMapping(1, site="SITE0", channel="RAW", group="G")])
    def capture(figure, path):
        line = figure.axes[0].lines[0]
        np.testing.assert_array_equal(line.get_xdata(), frequency / 1e6)
        np.testing.assert_array_equal(line.get_ydata(), magnitude * 1000)
        if expected[0] < expected[1]:
            assert figure.axes[0].get_xlim() == (expected[0] / 1e6, expected[1] / 1e6)
        return str(path)
    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "coverage.png")


@pytest.mark.parametrize("board_type,fonts", [("MLO", (26, 14.5, 14, 15, 15)), ("PCB", (26, 9, 14, 10, 15))])
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
