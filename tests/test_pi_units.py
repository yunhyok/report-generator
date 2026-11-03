from copy import deepcopy

import numpy as np
import pytest
from openpyxl import load_workbook

from report_generator.core import render, report
from report_generator.core.models import ChannelMapping, DcrRow, Port, ReportConfig, TouchstoneData
from report_generator.core.touchstone import interpolate_impedance, pi_display_units, plot_impedance


def sample(values, frequencies=(1e5, 1e6, 1e7, 1e8, 1e9)):
    values = np.asarray(values, dtype=complex)
    config = ReportConfig(mappings=[
        ChannelMapping(index + 1, channel=f"RAIL{index}", site=f"SITE{index}", group=f"G{index}")
        for index in range(values.shape[1])])
    config.settings.board_type = "PCB"
    ts = TouchstoneData("synthetic.sNp", {}, [Port(i + 1, f"P{i + 1}") for i in range(values.shape[1])],
                       np.array(frequencies), values)
    return config, ts


@pytest.mark.parametrize("value,expected", [
    (0, (1000, "mΩ")), (.999999999, (1000, "mΩ")), (1, (1, "Ω")),
    (1.000000001, (1, "Ω")), (-1j, (1, "Ω")), (complex(np.nan, np.nan), (1, "Ω")),
    (complex(np.inf, 0), (1, "Ω")),
])
def test_pcb_strict_raw_100mhz_boundary(value, expected):
    config, ts = sample([[value]])
    ts.frequency_hz = np.array([1e8])
    before = ts.z_diagonal.copy()
    assert pi_display_units(ts, config) == expected
    np.testing.assert_equal(ts.z_diagonal, before)
    config.settings.board_type = "MLO"
    assert pi_display_units(ts, config) == (1000, "mΩ")


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


@pytest.mark.parametrize("other_100mhz,factor,unit", [(.4, 1000, "mΩ"), (1, 1, "Ω"), (np.nan, 1, "Ω")])
def test_export_and_group_rendering_share_whole_report_units(tmp_path, monkeypatch, other_100mhz, factor, unit):
    config, ts = sample([[.1, .4]] * 5)
    config.mappings[1].summary = False
    config.dcr = [DcrRow("SITE0", "RAIL0", 12.5)]
    ts.z_diagonal[3, 1] = other_100mhz
    raw = ts.z_diagonal.copy()
    captured = []
    finish = render._finish

    def capture(figure, path):
        ax = figure.axes[0]
        captured.append((ax.get_ylabel(), ax.lines[0].get_ydata().copy()))
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
        assert book["PI ALL@100MHz"]["B4"].value == f"Channel / [{unit}]"
        assert book["PI ALL@100MHz"]["C5"].value == pytest.approx(.1 * factor)
        assert book["PI ALL"]["B4"].value == f"Channel / [{unit}]"
        assert book["PI ALL"]["H5"].value == pytest.approx(.1 * factor)
        assert book["G0"]["P4"].value == f"Channel / [{unit}]"
        assert book["G0"]["U5"].value == pytest.approx(.1 * factor)
        assert book["G1"]["P4"].value == f"Channel / [{unit}]"
        assert book["G1"]["R5"].value == pytest.approx(.4 * factor)
        assert any(cell.value == 12.5 for row in book["DCR"] for cell in row)
    finally:
        book.close()
    assert len(captured) == 2
    for index, (label, values) in enumerate(captured):
        assert label == f"|Zii| [{unit}]"
        np.testing.assert_allclose(values, plot_impedance(ts, index + 1)[1] * factor, equal_nan=True)
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
    assert labels == ["|Zii| [Ω]"]


@pytest.mark.parametrize("board_type", ["PCB", "MLO"])
def test_wide_pi_detail_layout_follows_board_with_milliohm_unit(tmp_path, board_type):
    config, ts = sample([[.1] * 9] * 5)
    config.settings.board_type = board_type
    for mapping in config.mappings:
        mapping.channel = "RAIL"
        mapping.group = "G"
    result = report.build_report(config, None, ts, tmp_path / f"wide-{board_type}.xlsx")
    book = load_workbook(result.path)
    try:
        assert book["PI ALL"]["B4"].value == "RAIL / [mΩ]"
        assert book["PI ALL"]["F6"].value == pytest.approx(100)
        detail = book["G"]
        if board_type == "PCB":
            assert detail["P4"].value == "RAIL / [mΩ]"
            assert "P4:U4" in detail.merged_cells
            assert detail["T6"].value == pytest.approx(100)
        else:
            assert detail["P4"].value == "Channel / [mΩ]"
            assert detail["P5"].value == "RAIL"
            assert detail["U5"].value == pytest.approx(100)
    finally:
        book.close()
