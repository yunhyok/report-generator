from copy import deepcopy

import numpy as np
import pytest
from openpyxl import Workbook, load_workbook

from report_generator.core import render, report
from report_generator.core.models import ChannelMapping, DcrRow, Port, ReportConfig, TouchstoneData
from report_generator.core.touchstone import interpolate_impedance, pi_display_units, plot_impedance


def sample(values, frequencies=(1e5, 1e6, 1e7, 1e8, 1e9)):
    values = np.asarray(values, dtype=complex)
    config = ReportConfig(mappings=[
        ChannelMapping(index + 1, channel=f"RAIL{index}", site=f"SITE{index}", group=f"G{index}")
        for index in range(values.shape[1])])
    ts = TouchstoneData("synthetic.sNp", {}, [Port(i + 1, f"P{i + 1}") for i in range(values.shape[1])],
                       np.array(frequencies), values)
    return config, ts


@pytest.mark.parametrize("value,expected", [
    (0, (1000, "mΩ")), (.999999999, (1000, "mΩ")), (1, (1, "Ω")),
    (1.000000001, (1, "Ω")), (-1j, (1, "Ω")), (complex(np.nan, np.nan), (1, "Ω")),
    (complex(np.inf, 0), (1, "Ω")),
])
def test_plot_scale_uses_strict_raw_100mhz_boundary(value, expected):
    config, ts = sample([[value]])
    ts.frequency_hz = np.array([1e8])
    before = ts.z_diagonal.copy()
    assert pi_display_units(ts, config) == expected
    np.testing.assert_equal(ts.z_diagonal, before)


def test_unit_selection_uses_all_selected_confirmed_mapped_ports_across_groups():
    config, ts = sample([[.1, .2, 5, 6, 7]] * 5)
    config.mappings[1].summary = False
    config.mappings[2].selected = False
    config.mappings[3].confirmed = False
    config.mappings[4].port_index = None  # SPD-only row.
    before = deepcopy(config)
    assert pi_display_units(ts, config) == (1000, "mΩ")
    ts.z_diagonal[:, 1] = 1
    assert pi_display_units(ts, config) == (1, "Ω")
    config.mappings[1].group = ""  # Ungrouped measured ports still control the scale.
    assert pi_display_units(ts, config) == (1, "Ω")
    config.mappings[1].group = before.mappings[1].group
    assert config == before


def test_unit_selection_interpolates_complex_z_without_extrapolating():
    config, ts = sample([[.6 + 1j], [.6 - 1j]], frequencies=(5e7, 1.5e8))
    assert interpolate_impedance(ts, 1, 1e8) == (.6, True)
    assert pi_display_units(ts, config) == (1000, "mΩ")
    ts.z_diagonal[1] = np.nan
    assert pi_display_units(ts, config) == (1, "Ω")
    ts.z_diagonal[:] = .1
    ts.frequency_hz = np.array([1e5, 1e6])
    assert pi_display_units(ts, config) == (1, "Ω")
    assert pi_display_units(None, config) == (1, "Ω")
    config.mappings.clear()
    assert pi_display_units(ts, config) == (1, "Ω")


@pytest.mark.parametrize("values,expected", [
    ([], (1000, "mΩ")),
    ([None], (1000, "mΩ")),
    ([1, .999999, None], (1, "Ω")),
    ([1, .999999, 0], (1000, "mΩ")),
    ([1, 2, .999999], (1, "Ω")),
])
def test_half_or_more_of_nonblank_unrounded_values_selects_ohms(values, expected):
    assert report._table_units(values) == expected


def test_100mhz_summary_counts_only_measured_site_cells():
    spots = [
        (ChannelMapping(channel="VDD", site=f"SITE{i}", priority="5"),
         [2, 2, 2, value, 2], [False] * 5)
        for i, value in enumerate((.1, .1, 4))
    ]
    book = Workbook()
    sheet = book.active
    report._write_100(sheet, spots, use_priority=True)
    assert sheet["C4"].value == "Channel / [mΩ]"
    assert [sheet.cell(5, c).value for c in (4, 5, 6, 8, 9)] == pytest.approx([100, 100, 4000, 4000, 1400])
    book.close()


def test_export_tables_choose_units_from_printed_values_but_graphs_keep_plot_scale(tmp_path, monkeypatch):
    config, ts = sample([
        [.1, 2, 1],
        [.2, 2, .2],
        [np.nan, 2, .3],
        [.1, 2, .4],
        [1.5, 2, .5],
    ])
    config.mappings[1].summary = False  # This high port remains in its detail group only.
    config.mappings[2].group = config.mappings[1].group
    config.dcr = [DcrRow("SITE0", "RAIL0", 12.5)]
    raw = ts.z_diagonal.copy()
    captured = []
    finish = render._finish

    def capture(figure, path):
        ax = figure.axes[0]
        captured.append((ax.get_ylabel(), [line.get_ydata().copy() for line in ax.lines]))
        return finish(figure, path)

    selections = []
    select = report.pi_display_units

    def select_once(data, settings):
        selections.append(True)
        return select(data, settings)

    monkeypatch.setattr(render, "_finish", capture)
    monkeypatch.setattr(report, "pi_display_units", select_once)
    monkeypatch.setattr(render, "pi_display_units", lambda *_: pytest.fail("export must pass its selected scale"))
    result = report.build_report(config, None, ts, tmp_path / "units.xlsx")
    assert len(selections) == 1
    book = load_workbook(result.path)
    try:
        summary = book["PI ALL@100MHz"]
        assert summary["B4"].value == "Channel / [mΩ]"
        assert [summary.cell(row, 2).value for row in (5, 6)] == ["RAIL0", "RAIL2"]
        assert summary["C5"].value == pytest.approx(100)
        assert summary["D6"].value == pytest.approx(400)

        all_frequencies = book["PI ALL"]
        assert all_frequencies["B4"].value == "Channel / [mΩ]"
        assert [all_frequencies.cell(row, 2).value for row in (5, 6)] == ["RAIL0", "RAIL2"]
        assert all_frequencies["D5"].value == pytest.approx(100)
        assert all_frequencies["F5"].value is None
        assert all_frequencies["H5"].value == pytest.approx(1500)
        assert all_frequencies["D6"].value == pytest.approx(1000)

        group0, group1 = book["G0"], book["G1"]
        assert group0["P4"].value == "Channel / [mΩ]"
        assert group0["V5"].value == pytest.approx(1500)
        assert group1["P4"].value == "Channel / [Ω]"
        assert group1["U5"].value == pytest.approx(2)
        assert group1["U6"].value == pytest.approx(.4)
        assert any(cell.value == 12.5 for row in book["DCR"] for cell in row)
    finally:
        book.close()
    assert len(captured) == 2
    for graph_index, (label, lines) in enumerate(captured):
        assert label == "Impedance [Ω]"
        first_port = 1 if graph_index == 0 else 2
        np.testing.assert_allclose(lines[0], plot_impedance(ts, first_port)[1], equal_nan=True)
    np.testing.assert_equal(ts.z_diagonal, raw)


def test_standalone_group_preview_uses_other_group_and_summary_disabled_port(tmp_path, monkeypatch):
    config, ts = sample([[.1, 2]] * 5)
    config.mappings[1].summary = False
    labels = []

    def capture(figure, path):
        labels.append(figure.axes[0].get_ylabel())
        assert figure.axes[0].lines[0].get_ydata()[0] == pytest.approx(.1)
        return str(path)

    monkeypatch.setattr(render, "_finish", capture)
    render.render_group_plot(ts, config, "G0", tmp_path / "preview.png")
    assert labels == ["Impedance [Ω]"]


def test_wide_pi_table_units_are_independent_per_channel_block(tmp_path):
    values = np.full((5, 18), .1, dtype=complex)
    values[[0, 4], :9] = 2  # Outer frequencies do not vote.
    values[1:3, 9:] = 1  # In RAIL_B, two of the three eligible frequencies vote for ohms.
    config, ts = sample(values)
    for index, mapping in enumerate(config.mappings):
        mapping.channel = "RAIL_A" if index < 9 else "RAIL_B"
        mapping.group = mapping.channel
    result = report.build_report(config, None, ts, tmp_path / "wide.xlsx")
    book = load_workbook(result.path)
    try:
        wide_pi = book["PI ALL"]
        assert wide_pi["B4"].value == "RAIL_A / [mΩ]"
        assert wide_pi["I4"].value == "RAIL_B / [Ω]"
        assert wide_pi["G6"].value == pytest.approx(2000)
        assert wide_pi["N6"].value == pytest.approx(.1)
        summary = book["PI ALL@100MHz"]
        assert summary["B4"].value == "Channel / [mΩ]"
        assert summary["F5"].value == pytest.approx(100)
        assert summary["O6"].value == pytest.approx(100)

        detail_a = book["RAIL_A"]
        detail_b = book["RAIL_B"]
        assert detail_a["P4"].value == "RAIL_A / [mΩ]"
        assert detail_b["P4"].value == "RAIL_B / [Ω]"
        assert "P4:U4" in detail_a.merged_cells
        assert detail_a["T6"].value == pytest.approx(100)
        assert detail_b["T6"].value == pytest.approx(.1)
    finally:
        book.close()
