import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import openpyxl
import pytest

from report_generator.core.models import (CancelledError, ChannelMapping, Component, DcrRow,
    Port, ReportConfig, SpiceModel, SpdData, TouchstoneData)
from report_generator.core.report import FREQUENCIES, build_report, plan_pages


def data():
    ts = TouchstoneData("synthetic.s2p", {}, [Port(1, "P1"), Port(2, "P2")],
        np.array(FREQUENCIES), np.array([[1 + 1j, 2 + 2j]] * 5))
    config = ReportConfig(mappings=[
        ChannelMapping(1, site="0", channel="VDD", net="VDD_0", group="VDD"),
        ChannelMapping(2, site="1", channel="VDD", net="VDD_1", group="VDD")])
    config.settings.use_priority = True
    return config, ts


def test_sweep_note_shows_report_band_and_plot_includes_upper_decade(tmp_path):
    config, ts = data()
    ts.frequency_hz = np.array([.1, 1e5, 1e8, 1.5e9, 3e9])
    ts.z_diagonal[:3] = np.nan
    result = build_report(config, None, ts, tmp_path / "sweep.xlsx")
    assert "VDD" in result.sheets  # Only the new upper part of the plot band is valid.
    ts.z_diagonal[:] = 1
    result = build_report(config, None, ts, tmp_path / "summary.xlsx")
    book = openpyxl.load_workbook(result.path)
    try:
        for name in ("PI ALL@100MHz", "PI ALL"):
            assert book[name]["A2"].value == "* 1kHz ~ 2GHz : Automatic Frequency Sweep"
    finally:
        book.close()


def test_units_spots_formula_metrics_and_same_chart_source(tmp_path):
    config, ts = data()
    config.settings.board_type = "PCB"
    result = build_report(config, None, ts, tmp_path / "report.xlsx")
    assert result.sheets == ["PI ALL@100MHz", "PI ALL", "VDD"]
    w = openpyxl.load_workbook(result.path)
    assert w["PI ALL"]["E5"].value == pytest.approx(2**.5)
    assert w["PI ALL@100MHz"]["D5"].value == pytest.approx(2**.5)
    assert w["PI ALL@100MHz"]["G5"].value == pytest.approx(2 * 2**.5)
    assert w["PI ALL@100MHz"]["H5"].value == pytest.approx(1.5 * 2**.5)
    assert w["PI ALL@100MHz"]["F5"].value == pytest.approx(1 / 3)
    assert w["PI ALL@100MHz"]["F5"].number_format == "0.0%"
    assert w["VDD"]["S5"].value == pytest.approx(2**.5)
    assert w["VDD"]._images[0].anchor._from.col == 1
    assert w["VDD"]._images[0].anchor._from.row == 3
    assert not w["PI ALL"].sheet_view.showGridLines
    assert w["PI ALL"]["B4"].font.name == "맑은 고딕"
    assert w["PI ALL"]["A1"].font.sz == 24
    assert w["PI ALL"]["A1"].value == "Power Impedance"
    assert w["PI ALL"]["H5"].font.bold
    assert not w["PI ALL"]["G5"].font.bold
    for sheet in w:
        assert sheet.freeze_panes is None
        assert len(sheet.sheet_view.selection) == 1
        assert sheet.sheet_view.selection[0].pane is None
        assert sheet.sheet_view.selection[0].activeCell == "B4"
    with zipfile.ZipFile(result.path) as z:
        assert len([n for n in z.namelist() if n.startswith("xl/media/")]) == 1
        assert not any(n.startswith("xl/charts/") for n in z.namelist())
    w.close()
    config.settings.board_type = "MLO"
    result = build_report(config, None, ts, tmp_path / "mlo.xlsx")
    w = openpyxl.load_workbook(result.path, read_only=True)
    assert w["PI ALL"]["E5"].value == pytest.approx(1000 * 2**.5)
    w.close()


def test_dcr_with_empty_spd_and_literal_formula_strings(tmp_path):
    config = ReportConfig(dcr=[DcrRow("0", "=SUM(1,2)", 12.5)])
    spd = SpdData("synthetic.spd", {}, warnings=["Board outline unavailable."])
    result = build_report(config, spd, None, tmp_path / "dcr.xlsx")
    assert result.sheets == ["DCR", "CAP_배치도면"]
    w = openpyxl.load_workbook(result.path)
    assert w["DCR"]["C5"].data_type == "s"
    assert w["DCR"]["C5"].value == "=SUM(1,2)"
    assert w["DCR"]["D5"].value == 12.5
    assert w["DCR"]["A3"].value is None
    assert not any(isinstance(c.value, str) and c.value.startswith("주의:") for row in w["DCR"] for c in row)
    assert result.warnings == spd.warnings
    w.close()


def test_partial_and_summary_flags_and_safe_unique_names(tmp_path):
    config, ts = data()
    config.mappings[0].group = "PI ALL"
    config.mappings[1].group = "a:/?*[]" + "한" * 35
    config.mappings[1].summary = False
    pages, omitted = plan_pages(config, None, ts)
    assert pages[:3] == ["PI ALL@100MHz", "PI ALL", "PI ALL (2)"]
    assert len(pages[-1]) <= 31 and all(c not in pages[-1] for c in ":/?*[]")
    assert "DCR" in omitted
    config.mappings[0].display_name = "A" * 30 + "'middle"
    config.mappings[1].display_name = "😀" * 16
    assert plan_pages(config, None, ts)[0][-2:] == pages[-2:]
    config.mappings[0].group = "A" * 30 + "'middle"
    config.mappings[1].group = "😀" * 16
    assert plan_pages(config, None, ts)[0][-2:] == ["A" * 30, "😀" * 15]
    config.mappings[0].group = config.mappings[1].group = ""
    result = build_report(config, None, ts, tmp_path / "summary.xlsx")
    assert result.sheets == ["PI ALL@100MHz", "PI ALL", "VDD"]
    w = openpyxl.load_workbook(result.path)
    assert w["PI ALL"]["D5"].value == 0
    assert w["PI ALL"]["C6"].value is None
    w.close()


def test_unavailable_frequency_never_extrapolated(tmp_path):
    config, ts = data()
    ts.frequency_hz = np.array([1e6, 1e7])
    ts.z_diagonal = ts.z_diagonal[:2]
    pages, omitted = plan_pages(config, None, ts)
    assert "PI ALL@100MHz" not in pages
    assert "PI ALL@100MHz" in omitted
    result = build_report(config, None, ts, tmp_path / "partial.xlsx")
    w = openpyxl.load_workbook(result.path)
    assert w["PI ALL"]["E5"].value is None
    assert w["PI ALL"]["F5"].value is not None
    assert w["PI ALL"]["H5"].value is None
    w.close()


def test_caps_active_override_and_placement(tmp_path):
    config = ReportConfig(mappings=[ChannelMapping(site="0", channel="VDD", net="VDD_0")])
    config.component_overrides["C1"] = 1e-6
    spd = SpdData("synthetic.spd", {}, models={"cap": SpiceModel("cap", 1e-7, valid=True)},
        components=[Component("C1", "cap", ["VDD_0", "GND"], x_mm=1, y_mm=2,
                              width_mm=1, height_mm=.5, is_cap=True),
                    Component("C2", "cap", ["VDD_0", "GND"], status="Disabled", is_cap=True),
                    Component("C3", "NULL", ["VDD_0", "GND"], status="Enabled", is_cap=True)],
        warnings=["Board outline unavailable.", "보드 외곽선 정보가 없습니다.", "Model nominal requires review."])
    result = build_report(config, spd, None, tmp_path / "caps.xlsx")
    assert result.sheets == ["CAP_수량", "CAP_배치도면"]
    w = openpyxl.load_workbook(result.path)
    assert w["CAP_수량"]["C4"].value == "1uF"
    assert w["CAP_수량"]["C6"].value == 1
    assert w["CAP_수량"]["C8"].value == "Unknown"
    assert w["CAP_수량"]["C10"].value == 1
    assert "B4:B5" in w["CAP_수량"].merged_cells
    assert [image.anchor._from.col for image in w["CAP_배치도면"]._images] == [1, 15]
    cap_notes = " ".join(str(c.value) for row in w["CAP_수량"] for c in row if c.value is not None)
    assert "Model nominal requires review." not in cap_notes and "Board outline unavailable." not in cap_notes
    assert "외곽선" not in cap_notes
    assert result.warnings == spd.warnings
    w.close()


def test_empty_and_invalid_dcr_rejected(tmp_path):
    with pytest.raises(ValueError, match="유효한 데이터"):
        build_report(ReportConfig(), None, None, tmp_path / "empty.xlsx")
    config = ReportConfig(dcr=[DcrRow("0", "VDD", float("nan"))])
    with pytest.raises(ValueError, match="DCR"):
        build_report(config, None, None, tmp_path / "invalid.xlsx")


@pytest.mark.parametrize("board_type", ["MLO", "PCB"])
def test_general_component_geometry_without_mapping_is_valid_partial_report(tmp_path, board_type):
    spd = SpdData("synthetic.spd", {}, components=[
        Component("U1", "chip", x_mm=10, y_mm=20, width_mm=3, height_mm=4),
        Component("J1", "connector", x_mm=30, y_mm=20, side="Bottom")])
    config = ReportConfig()
    config.settings.board_type = board_type
    pages, omitted = plan_pages(config, spd, None)
    assert pages == ["CAP_배치도면"]
    result = build_report(config, spd, None, tmp_path / "geometry.xlsx")
    w = openpyxl.load_workbook(result.path)
    images = w["CAP_배치도면"]._images
    assert [image.anchor._from.col for image in images] == [1, 15]
    w.close()


@pytest.mark.parametrize("board_type,side", [("MLO", "Top"), ("MLO", "Bottom"), ("PCB", "Top"), ("PCB", "Bottom")])
def test_one_sided_geometry_keeps_independent_top_and_bottom_panels(tmp_path, board_type, side):
    spd = SpdData("synthetic.spd", {}, components=[Component("U1", "chip", x_mm=10, y_mm=20,
        width_mm=3, height_mm=4, side=side)])
    config = ReportConfig()
    config.settings.board_type = board_type
    result = build_report(config, spd, None, tmp_path / f"{board_type}-{side}.xlsx")
    w = openpyxl.load_workbook(result.path)
    images = w["CAP_배치도면"]._images
    assert [image.anchor._from.col for image in images] == [1, 15]
    assert images[0] is not images[1]
    assert images[0]._data() != images[1]._data()
    images.pop(0)
    assert len(images) == 1 and images[0].anchor._from.col == 15
    w.close()


@pytest.mark.parametrize("board_type", ["MLO", "PCB"])
def test_empty_spd_still_has_two_placement_panels(tmp_path, board_type):
    config = ReportConfig()
    config.settings.board_type = board_type
    result = build_report(config, SpdData("empty.spd", {}), None, tmp_path / f"empty-{board_type}.xlsx")
    w = openpyxl.load_workbook(result.path)
    assert [image.anchor._from.col for image in w["CAP_배치도면"]._images] == [1, 15]
    assert all(image._data() for image in w["CAP_배치도면"]._images)
    w.close()


def test_no_spd_omits_placement_panels(tmp_path):
    config, ts = data()
    pages, _ = plan_pages(config, None, ts)
    assert "CAP_배치도면" not in pages
    result = build_report(config, None, ts, tmp_path / "no-spd.xlsx")
    w = openpyxl.load_workbook(result.path)
    assert "CAP_배치도면" not in w.sheetnames
    w.close()


def test_cancel_and_failed_save_preserve_existing_file(tmp_path, monkeypatch):
    config, ts = data()
    for mapping in config.mappings:
        mapping.group = ""
    target = tmp_path / "existing.xlsx"
    target.write_bytes(b"existing report")
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 3
    with pytest.raises(CancelledError):
        build_report(config, None, ts, target, cancel=cancel)
    assert target.read_bytes() == b"existing report"
    def fail_save(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("report_generator.core.report.Workbook.save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        build_report(config, None, ts, target)
    assert target.read_bytes() == b"existing report"
    assert list(tmp_path.iterdir()) == [target]


def test_output_cannot_replace_source(tmp_path):
    config, ts = data()
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")
    ts.path = str(source)
    with pytest.raises(ValueError, match="입력 파일"):
        build_report(config, None, ts, source)
    assert source.read_bytes() == b"source"


def test_unfrozen_placement_has_no_frozen_pane_selections(tmp_path):
    spd = SpdData("synthetic.spd", {}, components=[
        Component("U1", "chip", x_mm=10, y_mm=20, width_mm=3, height_mm=4)])
    result = build_report(ReportConfig(), spd, None, tmp_path / "placement.xlsx")
    with zipfile.ZipFile(result.path) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    view = root.find("s:sheetViews/s:sheetView", ns)
    assert view.find("s:pane", ns) is None
    selections = view.findall("s:selection", ns)
    assert len(selections) == 1
    assert selections[0].get("pane") is None
    assert selections[0].get("activeCell") == "B4"


@pytest.mark.parametrize("source_field", ["dcr_source_path", "channel_settings_source_path"])
def test_output_cannot_replace_imported_workbook(tmp_path, source_field):
    source = tmp_path / "input.xlsx"
    source.write_bytes(b"original input")
    config = ReportConfig(dcr=[DcrRow("SITE0", "VDD", 1.5)], **{source_field: str(source)})
    with pytest.raises(ValueError, match="덮어쓸"):
        build_report(config, None, None, source)
    assert source.read_bytes() == b"original input"


def test_interpolated_values_are_numeric_and_marked_in_every_pi_table(tmp_path):
    config, ts = data()
    ts.frequency_hz = np.array([1e3, 1e9])
    ts.z_diagonal = ts.z_diagonal[:2]
    result = build_report(config, None, ts, tmp_path / "interpolated.xlsx")
    workbook = openpyxl.load_workbook(result.path, data_only=True)
    try:
        summary = workbook["PI ALL@100MHz"]
        assert summary["D5"].number_format == '0.000"*"'
        assert isinstance(summary["D5"].value, float)
        assert summary["G5"].number_format == "0.000_ "
        assert workbook["PI ALL"]["E5"].number_format == '0.000"*"'
        assert workbook["PI ALL"]["H5"].number_format == '0.000"*"'
        assert workbook["PI ALL"]["I5"].number_format == "0.000_ "
        assert workbook["VDD"]["S5"].number_format == '0.000"*"'
        assert workbook["VDD"]["W5"].number_format == "0.000_ "
    finally:
        workbook.close()


def test_wide_reference_blocks_cap_headers_and_display_precision(tmp_path):
    mappings = [ChannelMapping(index + 1, site=f"SITE{site}", channel=f"RAIL{channel}",
                               net=f"N{index}", priority="7", group=f"RAIL{channel}" if channel < 2 else "")
                for index, (channel, site) in enumerate((c, s) for c in range(5) for s in range(9))]
    config = ReportConfig(mappings=mappings)
    ts = TouchstoneData("synthetic.s45p", {}, [Port(i + 1, f"P{i + 1}") for i in range(45)],
                        np.array(FREQUENCIES), np.full((5, 45), 1.123456 + 1j))
    spd = SpdData("synthetic.spd", {}, models={"cap": SpiceModel("cap", 1e-7, valid=True)},
                  components=[Component(f"C{i}", "cap", [f"N{i}", "GND"], is_cap=True) for i in range(45)])
    result = build_report(config, spd, ts, tmp_path / "wide.xlsx")
    book = openpyxl.load_workbook(result.path)
    try:
        pi = book["PI ALL"]
        assert pi["B4"].value == "RAIL0 / [mΩ]"
        assert pi["B4"].fill.fgColor.rgb == "00E2EFDA"
        assert "B4:H4" in pi.merged_cells and "W4:AC4" in pi.merged_cells
        assert "B17:H17" in pi.merged_cells
        assert pi["B6"].value == 0
        assert pi["C6"].value == pytest.approx(abs(1.123456 + 1j) * 1000)
        assert pi["C6"].number_format == "0.0_ "
        assert pi["F6"].font.bold and not pi["E6"].font.bold
        assert book["PI ALL@100MHz"]["B5"].font.bold
        assert book["RAIL1"]["P4"].fill.fgColor.rgb == "00D9E2F3"
        assert book["RAIL1"]["R5"].number_format == "0.0_ "
        caps = book["CAP_수량"]
        assert "B4:B5" in caps.merged_cells and "C4:K4" in caps.merged_cells
        assert caps["B6"].font.sz == 12 and caps["C6"].font.sz == 11
        assert caps.row_dimensions[6].height == 17.25
        assert all(sheet.freeze_panes is None for sheet in book)
    finally:
        book.close()


def test_missing_site_and_channel_values_remain_visible(tmp_path):
    config, ts = data()
    config.mappings[1].channel = "UNAVAILABLE"
    config.mappings[0].group = config.mappings[1].group = ""
    ts.z_diagonal[:, 1] = complex(np.nan, np.nan)
    result = build_report(config, None, ts, tmp_path / "missing.xlsx")
    book = openpyxl.load_workbook(result.path)
    try:
        summary = book["PI ALL@100MHz"]
        assert summary["E4"].value == "1"
        assert summary["C6"].value == "UNAVAILABLE"
        assert summary["E6"].value is None
        assert book["PI ALL"]["C6"].value == "UNAVAILABLE"
    finally:
        book.close()


def priority_data(site_count):
    names = ["Unnamed", "Third", "AuxA", "FirstB", "FirstA", "AuxB", "Other"]
    ranks = ["", "10", "Auxiliary", "2", "2", "Auxiliary", "Other group"]
    mappings = [ChannelMapping(index + 1, site=f"SITE{site}", channel=names[channel],
                               net=f"N{index}", priority=ranks[channel], group="Overlay")
                for index, (channel, site) in enumerate((c, s) for c in range(len(names)) for s in range(site_count))]
    config = ReportConfig(mappings=mappings,
        dcr=[DcrRow(m.site, m.channel, index + .25) for index, m in enumerate(mappings)])
    config.settings.board_type = "PCB"
    config.settings.product = "Synthetic metadata"
    values = np.array([[(frequency + 1) * (site + 1) * (channel + 1)
                        for channel in range(len(names)) for site in range(site_count)] for frequency in range(5)], dtype=complex)
    ts = TouchstoneData("synthetic.sNp", {}, [Port(i + 1, f"P{i + 1}") for i in range(len(mappings))],
                        np.array(FREQUENCIES), values, warnings=["Synthetic conversion warning"])
    spd = SpdData("synthetic.spd", {}, models={"cap": SpiceModel("cap", 1e-7, valid=True)},
                  components=[Component(f"C{i}", "cap", [f"N{i}", "GND"], is_cap=True) for i in range(len(mappings))],
                  warnings=["Synthetic model warning"])
    return config, spd, ts


def test_priority_sections_optional_notes_removed_and_dcr_sorted(tmp_path):
    config, spd, ts = priority_data(2)
    disabled = build_report(config, spd, ts, tmp_path / "off.xlsx")
    config.settings.use_priority = True
    enabled = build_report(config, spd, ts, tmp_path / "on.xlsx")
    off = openpyxl.load_workbook(disabled.path)
    on = openpyxl.load_workbook(enabled.path)
    try:
        for book in (off, on):
            for sheet in book:
                assert sheet["A3"].value is None
                assert sheet.freeze_panes is None
                assert not any(isinstance(c.value, str) and any(term in c.value for term in
                    ("Synthetic metadata", "Synthetic model warning", "Synthetic conversion warning", "주의:", "보간값", "활성 부품만 집계"))
                    for row in sheet for c in row)
        assert off["PI ALL@100MHz"]["B4"].value.startswith("Channel")
        assert [off["PI ALL@100MHz"].cell(r, 2).value for r in range(5, 12)] == [m.channel for m in config.mappings[::2]]
        assert all(c.value != "Priority" for name in ("PI ALL@100MHz", "PI ALL", "CAP_수량", "Overlay") for row in off[name] for c in row)
        summary = on["PI ALL@100MHz"]
        assert [summary.cell(r, 3).value for r in (5, 6, 7, 9, 10, 12, 14)] == ["FirstB", "FirstA", "Third", "AuxA", "AuxB", "Other", "Unnamed"]
        assert [summary.cell(r, 2).value for r in (5, 6, 7)] == [2, 2, 10]
        assert all(summary.cell(8, c).value is None for c in range(2, 9))
        assert summary["B9"].value == "Auxiliary" and "B9:B10" in summary.merged_cells
        assert summary["F5"].value == pytest.approx(1 / 3)
        assert summary["G5"].value == 32 and summary["H5"].value == 24
        assert on["PI ALL"]["C5"].value == "FirstB" and on["PI ALL"]["C12"].value == "AuxA"
        assert on["PI ALL"]["J5"].value == pytest.approx(1 / 3)
        assert on["CAP_수량"]["C6"].value == "FirstB" and on["CAP_수량"]["B10"].value == "Auxiliary"
        assert on["Overlay"]["Q5"].value == "FirstB" and on["Overlay"]["Q12"].value == "AuxA"
        assert [off["DCR"].cell(row, 3).value for row in range(5, 12)] == [m.channel for m in config.mappings[::2]]
        ordered = ["FirstB", "FirstA", "Third", "AuxA", "AuxB", "Other", "Unnamed"]
        assert [on["DCR"].cell(row, 3).value for row in range(5, 12)] == ordered
        values = {(row.channel, row.site): row.resistance_mohm for row in config.dcr}
        for row, channel in enumerate(ordered, 5):
            assert [on["DCR"].cell(row, column).value for column in (4, 5)] == [
                values[channel, site] for site in ("SITE0", "SITE1")]
        assert disabled.warnings == enabled.warnings == [*spd.warnings, *ts.warnings]
    finally:
        off.close()
        on.close()


@pytest.mark.parametrize("site_count", [2, 9])
@pytest.mark.parametrize("priority_mode", ["enabled", "disabled", "empty"])
def test_dcr_priority_export_and_preview_preserve_sites_values_and_aliases(tmp_path, site_count, priority_mode):
    from queue import Queue
    from threading import Event

    from report_generator.jobs import run_job

    config, _, _ = priority_data(site_count)
    config.settings.use_priority = priority_mode != "disabled"
    incoming = ["AuxB", "Unnamed", "FirstA", "Other", "Third", "AuxA", "FirstB"]
    sites = [f"SITE{site}" for site in reversed(range(site_count))]
    values = {(row.channel, row.site): row.resistance_mohm for row in config.dcr}
    config.dcr = [DcrRow(site, channel, values[channel, site]) for site in sites for channel in incoming]
    for mapping in config.mappings:
        mapping.display_name = f"Alias {incoming.index(mapping.channel)}"
        if priority_mode == "empty":
            mapping.priority = ""
    export = build_report(config, None, None, tmp_path / "dcr.xlsx")
    messages = Queue()
    run_job("preview", {"config": config, "folder": str(tmp_path / "preview")}, messages, Event())
    results = list(messages.queue)
    assert not [payload for kind, payload in results if kind == "error"]
    preview, images = next(payload for kind, payload in results if kind == "result")
    assert images == []
    assert export.sheets == preview.sheets == ["DCR"]
    expected = (["FirstA", "FirstB", "Third", "AuxB", "AuxA", "Other", "Unnamed"]
                if priority_mode == "enabled" else incoming)
    channel_column = 3 if site_count <= 8 else 2
    site_column = channel_column + 1
    for result in (export, preview):
        book = openpyxl.load_workbook(result.path)
        try:
            sheet = book["DCR"]
            assert [sheet.cell(4, site_column + offset).value for offset in range(site_count)] == sites
            for row, channel in enumerate(expected, 5):
                cell = sheet.cell(row, channel_column)
                assert cell.value == f"Alias {incoming.index(channel)}"
                assert cell.comment is None
                assert [sheet.cell(row, site_column + offset).value for offset in range(site_count)] == [
                    values[channel, site] for site in sites]
        finally:
            book.close()


def test_wide_priority_bands_preserve_raw_channel_identity_and_every_site(tmp_path):
    from report_generator.validation import inspect_workbook
    config, spd, ts = priority_data(9)
    config.settings.use_priority = True
    channel_keys = {}
    result = build_report(config, spd, ts, tmp_path / "wide-priority.xlsx", channel_keys=channel_keys)
    book = openpyxl.load_workbook(result.path)
    try:
        pi = book["PI ALL"]
        assert pi["B4"].value == "Priority" and pi["E4"].value == 2
        assert [pi.cell(5, col).value for col in (2, 9, 16)] == ["FirstB / [Ω]", "FirstA / [Ω]", "Third / [Ω]"]
        assert pi["B18"].value == "Auxiliary"
        assert pi["B19"].value == "AuxA / [Ω]" and pi["I19"].value == "AuxB / [Ω]"
        assert pi["B46"].value == "Unnamed / [Ω]"
        assert pi["H7"].value == pytest.approx(.8)
        assert book["CAP_수량"]["B4"].value == "Priority"
        assert book["CAP_수량"]["C6"].font.sz == 12
    finally:
        book.close()
    actual, _ = inspect_workbook(result.path, channel_keys=channel_keys)
    expected = {(mapping.channel, mapping.site, frequency) for mapping in config.mappings for frequency in
                ("100kHz", "1MHz", "10MHz", "100MHz", "1GHz")}
    assert set(actual["PI"]) == expected
    assert set(actual["PI_DETAIL"]) == expected
    assert len(actual["PI100"]) == len(actual["CAP"]) == len(actual["DCR"]) == 63


def test_single_named_group_has_no_needless_leading_gap(tmp_path):
    config, ts = data()
    for mapping in config.mappings:
        mapping.priority = "Auxiliary"
    result = build_report(config, None, ts, tmp_path / "single.xlsx")
    book = openpyxl.load_workbook(result.path)
    try:
        assert book["PI ALL@100MHz"]["C5"].value == "VDD"
        assert book["PI ALL@100MHz"]["B5"].value == "Auxiliary"
        assert book["VDD"]["Q5"].value == "VDD" and book["VDD"]["Q6"].value == "VDD"
    finally:
        book.close()
