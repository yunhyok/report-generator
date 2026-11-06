import numpy as np
from PIL import Image
import pytest

from report_generator.core.models import (ChannelMapping, Component, PadShape, Port, ReportConfig,
    SpiceModel, SpdData, SurfacePad, TouchstoneData)
from report_generator.core import render
from report_generator.core.capacitance import summarize_caps
from report_generator.core.touchstone import (
    PLOT_POINTS_PER_DECADE, interpolate_impedance, plot_frequency_bounds, plot_impedance,
)


@pytest.mark.parametrize("names,title", [
    (("", ""), "G"),
    (("Alias_A", "Alias_A"), "G"),
    (("Alias_A", ""), "G"),
    (("_Rail$X$", "Alias_B"), "G"),
])
def test_group_display_names_keep_title_and_curves_without_legend(tmp_path, monkeypatch, names, title):
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
        captured.update(title=ax.get_title(), lines=len(ax.lines), axes_legend=ax.get_legend(),
                        figure_legends=figure.legends, right=ax.get_position().x1)
        return str(path)
    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "aliases.png")
    assert captured["title"] == title
    assert captured["lines"] == 2
    assert captured["axes_legend"] is None and not captured["figure_legends"]
    assert captured["right"] > .95


def test_many_mixed_names_use_full_plot_width_without_legend(tmp_path, monkeypatch):
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
    def capture(canvas, filename, *args, **kwargs):
        canvas.draw()
        ax = canvas.figure.axes[0]
        assert ax.get_legend() is None and not canvas.figure.legends
        assert ax.get_position().x1 > .95
        assert ax.bbox.width >= 1100 and ax.bbox.height >= 400
        assert len(ax.lines) == count
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    path = render.render_group_plot(ts, config, "Mixed_names", tmp_path / "mixed-names.png")
    with Image.open(path) as image:
        assert image.size == (1320, 594)


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
        assert ax.get_legend() is None and not canvas.figure.legends
        if isinstance(filename, str):
            assert ax.get_position().x1 > .95
        captured["lines"] = [line.get_ydata().copy() for line in ax.lines]
        captured["frequencies"] = ax.lines[0].get_xdata() * 1e6
        captured["background"] = canvas.figure.get_facecolor()
        captured["markers"] = [line.get_marker() for line in ax.lines]
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "plot.png")
    assert len(captured["lines"]) == 1
    values = captured["lines"][0]
    assert values[0] == pytest.approx(2**.5)
    assert values[-1] == 5
    assert np.isnan(values[1:-1]).all()
    assert 1e6 in captured["frequencies"]
    assert captured["background"] == (1, 1, 1, 1)
    assert captured["markers"] == ["None"]


def test_ranked_mixed_site_channel_group_has_no_right_legend(tmp_path, monkeypatch):
    config = ReportConfig(mappings=[
        ChannelMapping(1, channel="RAIL_A", site="SITE0", group="5순위", display_name="Core Rail"),
        ChannelMapping(2, channel="RAIL_B", site="SITE1", group="5순위"),
    ])
    ts = TouchstoneData("synthetic.s2p", {}, [Port(1, "P1"), Port(2, "P2")],
                        np.array([1e3, 1e6, 1e9]), np.array([[1, 2], [2, 3], [3, 4]], dtype=complex))

    def capture(figure, path):
        ax = figure.axes[0]
        assert ax.get_title() == "5순위"
        assert ax.get_xlabel() == "Frequency [MHz]"
        assert ax.get_ylabel() == "Impedance [Ω]"
        assert len(ax.lines) == 2
        assert ax.get_legend() is None and not figure.legends
        assert ax.get_position().x1 > .95
        return str(path)

    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "5순위", tmp_path / "ranked-mixed.png")


def test_single_explicit_group_keeps_group_title_without_legend(tmp_path, monkeypatch):
    config = ReportConfig(mappings=[
        ChannelMapping(1, channel="RAIL", site="SITE0", group="5순위", display_name="Core Rail"),
    ])
    ts = TouchstoneData("synthetic.s1p", {}, [Port(1, "P1")], np.array([1e3, 1e6]),
                        np.array([[1], [2]], dtype=complex))

    def capture(figure, path):
        ax = figure.axes[0]
        assert ax.get_title() == "5순위"
        assert len(ax.lines) == 1
        assert ax.get_legend() is None and not figure.legends
        return str(path)

    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "5순위", tmp_path / "single-ranked.png")


@pytest.mark.parametrize("width,at_100mhz,factor,unit", [(None, .5, 1000, "mΩ"), (2.5, 1.0, 1, "Ω")])
@pytest.mark.parametrize("count,channel_count", [(4, 1), (4, 2), (14, 1), (14, 2)])
def test_group_plot_solid_width_and_distinct_site_channel_colors(
        tmp_path, monkeypatch, width, at_100mhz, factor, unit, count, channel_count):
    config = ReportConfig(mappings=[
        ChannelMapping(index + 1, channel=f"RAIL{index % channel_count}",
                       site=f"SITE{index // channel_count}", group="G", display_name=f"Name{index}")
        for index in range(count)
    ])
    if width is not None:
        config.settings.plot_line_width_pt = width
    values = np.tile([[1], [np.nan], [at_100mhz], [3]], (1, count)).astype(complex)
    ts = TouchstoneData("synthetic", {}, [Port(i + 1, f"P{i + 1}") for i in range(count)],
                       np.array([1e3, 1e6, 1e8, 1e9]), values)

    def capture(figure, path):
        ax = figure.axes[0]
        assert len(ax.lines) == count
        assert all(line.get_linestyle() == "-" for line in ax.lines)
        assert all(line.get_linewidth() == (1.0 if width is None else width) for line in ax.lines)
        colors = [line.get_color() for line in ax.lines]
        assert len(set(colors)) == count
        assert ax.get_legend() is None and not figure.legends
        if count <= len(render.COLORS):
            assert colors == render.COLORS[:count]
        assert ax.get_ylabel() == f"Impedance [{unit}]"
        for line, mapping in zip(ax.lines, config.mappings):
            frequency, magnitude = plot_impedance(ts, mapping.port_index)
            np.testing.assert_array_equal(line.get_xdata(), frequency / 1e6)
            np.testing.assert_array_equal(line.get_ydata(), magnitude * factor)
            assert np.isnan(line.get_ydata()[frequency == 1e6]).all()
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
        np.testing.assert_array_equal(line.get_ydata(), magnitude)
        if expected[0] < expected[1]:
            assert figure.axes[0].get_xlim() == (expected[0] / 1e6, expected[1] / 1e6)
        return str(path)
    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "G", tmp_path / "coverage.png")


def test_group_plots_have_no_legend_or_port_count_dependent_height(tmp_path, monkeypatch):
    captured = []
    original = render.FigureCanvasAgg.print_png
    def capture(canvas, filename, *args, **kwargs):
        fig, ax = canvas.figure, canvas.figure.axes[0]
        if isinstance(filename, str):
            captured.append((tuple(fig.get_size_inches()), fig.legends, ax.get_legend(), len(ax.lines), ax.get_position().y0))
            assert (ax.title.get_fontsize(), ax.xaxis.label.get_fontsize(), ax.yaxis.label.get_fontsize(),
                    ax.get_xticklabels()[0].get_fontsize(), ax.get_yticklabels()[0].get_fontsize()) == (26, 14.5, 14, 15, 15)
        assert ax.get_xscale() == ax.get_yscale() == "log"
        return original(canvas, filename, *args, **kwargs)
    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    for count in (1, 32):
        ts = TouchstoneData("synthetic", {}, [Port(i+1, f"P{i+1}") for i in range(count)],
                            np.array([1e3, 1e6, 1e9]), np.tile([[1], [2], [3]], (1, count)))
        config = ReportConfig(mappings=[ChannelMapping(i+1, site=f"SITE{i}", channel=f"RAIL{i % 3}", group="G") for i in range(count)])
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
    assert any(np.allclose(color, (185/255, 230/255, 242/255, 1)) for color in filled)
    assert any(np.allclose(color, (1, 1, 0, 1)) for color in filled)
    assert captured["labels"] == ["100nF", "1uF"]


@pytest.mark.parametrize("side", ["Top", "Bottom"])
def test_placement_treats_mixed_status_caps_as_populated_without_legend_counts(tmp_path, monkeypatch, side):
    def components(statuses):
        return [
            Component("C1", "SMALL", ["RAIL", "GND"], status=statuses[0], side=side,
                      x_mm=0, y_mm=0, width_mm=1, height_mm=.5, is_cap=True),
            Component("C2", "UNKNOWN", ["RAIL", "GND"], status=statuses[1], side=side,
                      x_mm=2, y_mm=0, is_cap=True),
            Component("C3", "UNKNOWN", ["RAIL", "GND"], status=statuses[2], side=side,
                      pads=[SurfacePad("Node3", 4, 0, side, PadShape("Circle", .5, .5))], is_cap=True),
            Component("U1", "IC", ["OTHER"], side=side, x_mm=6, y_mm=0, width_mm=1, height_mm=1),
        ]

    spd = SpdData("synthetic.spd", {}, models={"SMALL": SpiceModel("SMALL", 100e-9, valid=False)})
    config = ReportConfig(mappings=[ChannelMapping(net="RAIL", site="SITE2", channel="RAIL")])
    original = render.FigureCanvasAgg.print_png
    legends = []

    def capture(canvas, filename, *args, **kwargs):
        legends.append([text.get_text() for legend in canvas.figure.legends for text in legend.get_texts()])
        return original(canvas, filename, *args, **kwargs)

    monkeypatch.setattr(render.FigureCanvasAgg, "print_png", capture)
    for label, statuses in (("all-automatic", ("Automatic",) * 3),
                            ("mixed", ("Enabled", "Disabled", "Unknown"))):
        spd.components = components(statuses)
        path = tmp_path / f"placement-{side.lower()}-{label}.png"
        render.render_placement(spd, config, side, path)
        with Image.open(path) as image:
            if label == "all-automatic":
                expected_pixels = image.convert("RGBA").tobytes()
                expected_size = image.size
            else:
                assert image.size == expected_size
                assert image.convert("RGBA").tobytes() == expected_pixels

    assert legends == [["100nF", "Unknown capacitance", "Unknown dimensions", "Pin bounds"]] * 2
    spd.components = components(("Enabled", "Disabled", "Unknown"))
    assert [(row["capacitance_f"], row["count"]) for row in summarize_caps(spd, config)] == [(100e-9, 1)]


def test_alias_placement_selection_and_invalid_side(tmp_path):
    config = ReportConfig(mappings=[ChannelMapping(site="0", channel="VDD", net="alias")])
    spd = SpdData("synthetic.spd", {}, aliases={"alias": ["VDD"]},
        components=[Component("C1", "C", ["VDD", "GND"], x_mm=1, y_mm=2, is_cap=True)])
    assert len(render.placement_components(spd, config, "Top")) == 1
    assert not render.placement_components(spd, config, "Bottom")
    with pytest.raises(ValueError, match="Top 또는 Bottom"):
        render.render_placement(spd, config, "Left", tmp_path / "no.png")


@pytest.mark.parametrize("side,mirror", [("Top", True), ("Bottom", True), ("Bottom", False)])
@pytest.mark.parametrize("with_outline", [False, True])
def test_empty_placement_face_has_honest_label_and_only_true_outline(
        tmp_path, monkeypatch, side, mirror, with_outline):
    opposite = "Bottom" if side == "Top" else "Top"
    outline = [(10, 20), (40, 20), (40, 35), (10, 35), (10, 20)]
    spd = SpdData("synthetic.spd", {}, components=[
        Component("C1", "C", side=opposite, x_mm=100, y_mm=200,
                  width_mm=2, height_mm=1, is_cap=True,
                  pads=[SurfacePad("Node1", 100, 200, opposite, PadShape("Circle", 1, 1))]),
        Component("C2", "C", side=side, is_cap=True),
    ], outlines=[outline, [(999, 999)]] if with_outline else [])
    config = ReportConfig()
    config.settings.bottom_mirror = mirror
    original = render._finish

    def capture(figure, path):
        ax = figure.axes[0]
        assert not render.placement_components(spd, config, side)
        assert figure.get_facecolor() == ax.get_facecolor() == (0, 0, 0, 1)
        assert not ax.axison and not figure.legends
        assert [text.get_text() for text in figure.texts] == [
            side.upper() + (" · mirrored" if side == "Bottom" and mirror else ""),
            "No displayable component / pad coordinates",
        ]
        assert ax.xaxis_inverted() == (side == "Bottom" and mirror)
        if with_outline:
            assert len(ax.collections) == 1
            np.testing.assert_array_equal(ax.collections[0].get_segments(), [outline])
            assert ax.dataLim.bounds == pytest.approx((10, 20, 30, 15))
        else:
            assert not ax.collections
            assert not np.isfinite(ax.dataLim.bounds).any()
        return original(figure, path)

    monkeypatch.setattr(render, "_finish", capture)
    path = render.render_placement(spd, config, side, tmp_path / "empty-face.png")
    with Image.open(path) as image:
        assert image.height == 990
        assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 0)


def test_opposite_body_with_target_face_pads_still_draws_only_target_geometry(tmp_path, monkeypatch):
    spd = SpdData("synthetic.spd", {}, components=[
        Component("C1", "C", side="Top", x_mm=1000, y_mm=2000,
                  width_mm=2, height_mm=2, is_cap=True,
                  pads=[SurfacePad("Node1", 3, 4, "Bottom", PadShape("Circle", .5, .5))]),
    ])
    config = ReportConfig()

    def capture(figure, path):
        ax = figure.axes[0]
        assert len(ax.collections) == 1
        assert ax.dataLim.bounds == pytest.approx((2.75, 3.75, .5, .5))
        assert ax.xaxis_inverted()
        assert [text.get_text() for text in figure.texts] == ["BOTTOM · mirrored"]
        assert not any("Pin bounds" in text.get_text()
                       for legend in figure.legends for text in legend.get_texts())
        return str(path)

    monkeypatch.setattr(render, "_finish", capture)
    render.render_placement(spd, config, "Bottom", tmp_path / "opposite-body.png")


@pytest.mark.parametrize("capacitance", [100e-12, 100e-9])
def test_pale_cap_boundaries_survive_pad_overlays_and_excel_downsize(tmp_path, monkeypatch, capacitance):
    box = PadShape("Box", 1, .8, ((-.5, -.4), (.5, -.4), (.5, .4), (-.5, .4)))
    overlay = PadShape("Box", .3, .6, ((-.15, -.3), (.15, -.3), (.15, .3), (-.15, .3)))
    components = [
        Component("C1", "C", side="Top", x_mm=-.5, y_mm=0,
                  width_mm=1, height_mm=.8, is_cap=True,
                  pads=[SurfacePad("overlay", 0, 0, "Top", overlay)]),
        Component("C2", "C", side="Top", x_mm=.5, y_mm=0,
                  width_mm=1, height_mm=.8, is_cap=True),
        Component("C3", "C", is_cap=True,
                  pads=[SurfacePad("box-left", -.5, -1.2, "Top", box)]),
        Component("C4", "C", is_cap=True,
                  pads=[SurfacePad("box-right", .5, -1.2, "Top", box)]),
        Component("C5", "C", is_cap=True,
                  pads=[SurfacePad("circle-left", -.4, -2.4, "Top", PadShape("Circle", .8, .8))]),
        Component("C6", "C", is_cap=True,
                  pads=[SurfacePad("circle-right", .4, -2.4, "Top", PadShape("Circle", .8, .8))]),
        Component("C7", "C", side="Top", x_mm=5, y_mm=0,
                  width_mm=.05, height_mm=.1, is_cap=True),
    ]
    spd = SpdData("synthetic.spd", {}, models={"C": SpiceModel("C", capacitance, valid=True)},
                  components=components)
    config = ReportConfig()
    points = [(0, y) for y in (0, -1.2, -2.4)] + [(.25, y) for y in (0, -1.2, -2.4)] + [(5, 0)]
    captured = {}
    original = render._finish

    def capture(figure, path):
        canvas = render.FigureCanvasAgg(figure)
        canvas.draw()
        ax = figure.axes[0]
        captured["points"] = ax.transData.transform(points)
        pad_collections = [collection for collection in ax.collections
                           if isinstance(collection, render.EllipseCollection) or
                           (len(collection.get_facecolors()) and len(collection.get_edgecolors()))]
        assert len(pad_collections) == 2
        assert all(np.max(collection.get_edgecolors()[:, :3]) < .1 for collection in pad_collections)
        assert ax.collections[-1].get_zorder() > max(collection.get_zorder() for collection in pad_collections)
        return original(figure, path)

    monkeypatch.setattr(render, "_finish", capture)
    path = render.render_placement(spd, config, "Top", tmp_path / "pale-caps.png")
    with Image.open(path) as source:
        assert source.width > 850
        ratio = 850 / source.width
        image = source.convert("RGB").resize((850, round(source.height * ratio)), Image.Resampling.LANCZOS)
        pixels = np.asarray(image)
        locations = [(round(x * ratio), round((source.height - y) * ratio))
                     for x, y in captured["points"]]
    for x, y in locations[:3]:
        assert pixels[y - 1:y + 2, x - 1:x + 2].mean(axis=2).min() < 130
    for x, y in locations[3:]:
        assert pixels[y, x].mean() > 160


@pytest.mark.parametrize("feature_width", [.29, .42, .55])
@pytest.mark.parametrize("rotation", [0, 37])
def test_two_to_four_pixel_cap_features_keep_fill_and_separators(
        tmp_path, monkeypatch, feature_width, rotation):
    angle = np.deg2rad(rotation)
    direction = np.array([np.cos(angle), np.sin(angle)])
    vertical = np.array([-np.sin(angle), np.cos(angle)])
    box = PadShape("Box", feature_width, 1.2,
                   ((-feature_width/2, -.6), (feature_width/2, -.6),
                    (feature_width/2, .6), (-feature_width/2, .6)))
    small = PadShape("Circle", feature_width * .8, feature_width * .8)
    components = [Component("U1", "IC", x_mm=-50, y_mm=0, width_mm=1, height_mm=1),
                  Component("U2", "IC", x_mm=50, y_mm=0, width_mm=1, height_mm=1)]
    samples = []
    for row, y in enumerate((10, 0, -10)):
        center = np.array([0, y])
        for sign in (-1, 1):
            position = center + sign * feature_width/2 * direction
            samples.append(position)
            if row == 0:
                pads = [SurfacePad(f"internal-{sign}-{i}", *(position + offset * vertical),
                                   "Top", small) for i, offset in enumerate(np.linspace(-.4, .4, 7))]
                component = Component(f"C{len(components)}", "C", x_mm=position[0], y_mm=position[1],
                                      width_mm=feature_width, height_mm=1.2, rotation=rotation,
                                      is_cap=True, pads=pads)
            else:
                shape = box if row == 1 else PadShape("Circle", feature_width, feature_width)
                component = Component(f"C{len(components)}", "C", is_cap=True, pads=[
                    SurfacePad(f"pad-{row}-{sign}", *position, "Top", shape, rotation)])
            components.append(component)
    spd = SpdData("synthetic.spd", {}, components=components,
                  models={"C": SpiceModel("C", 100e-9, valid=True)},
                  outlines=[[(-50, -30), (50, -30), (50, 30), (-50, 30), (-50, -30)]])
    config = ReportConfig()
    captured = {}
    original = render._finish

    def capture(figure, path):
        canvas = render.FigureCanvasAgg(figure)
        canvas.draw()
        ax = figure.axes[0]
        captured["points"] = ax.transData.transform(samples)
        captured["seams"] = ax.transData.transform([(0, 10), (0, 0)])
        captured["width"] = feature_width * abs(ax.transData.get_matrix()[0, 0])
        return original(figure, path)

    monkeypatch.setattr(render, "_finish", capture)
    path = render.render_placement(spd, config, "Top", tmp_path / "tiny-caps.png")
    with Image.open(path) as source:
        ratio = 850 / source.width
        pixels = np.asarray(source.convert("RGB").resize(
            (850, round(source.height * ratio)), Image.Resampling.LANCZOS))
        assert 2 <= captured["width"] * ratio <= 4
        locations = [(round(x * ratio), round((source.height - y) * ratio))
                     for x, y in captured["points"]]
        seams = [(round(x * ratio), round((source.height - y) * ratio))
                 for x, y in captured["seams"]]
    for x, y in locations:
        patch = pixels[y-1:y+2, x-1:x+2].astype(float)
        assert patch.mean(axis=2).max() > 140
        assert (patch[:, :, 2] - patch[:, :, 0]).max() > 20
    for x, y in seams:
        assert pixels[y-1:y+2, x-1:x+2].mean(axis=2).min() < 180


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
    assert "Pin bounds" in captured["legend"]
    render.render_placement(spd, config, "Bottom", tmp_path / "pins-bottom.png")
    assert captured["limits"][0] > captured["limits"][1]
    assert captured["bounds"] == pytest.approx((2.75, 3.75, .5, .5))
    config.settings.bottom_mirror = False
    render.render_placement(spd, config, "Bottom", tmp_path / "pins-bottom-unmirrored.png")
    assert captured["limits"][0] < captured["limits"][1]
