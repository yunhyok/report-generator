from copy import deepcopy
import zipfile

import numpy as np
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from report_generator.core import report
from report_generator.core.models import ChannelMapping, Component, DcrRow, Port, ReportConfig, SpiceModel, SpdData, TouchstoneData


def sample(site_count):
    mappings = [ChannelMapping(index + 1, site=f"SITE{site}", channel=channel,
                               net=f"PHYSICAL_{index}", priority=str(channel_index + 1))
                for index, (channel_index, channel, site) in enumerate(
                    (channel_index, channel, site)
                    for channel_index, channel in enumerate(("=RAW_A/[0]", "RAW_B"))
                    for site in range(site_count))]
    config = ReportConfig(mappings=mappings,
                          dcr=[DcrRow(m.site, m.channel, index + .25) for index, m in enumerate(mappings)])
    ts = TouchstoneData("synthetic.sNp", {}, [Port(i + 1, f"P{i + 1}") for i in range(len(mappings))],
                        np.array(report.FREQUENCIES),
                        np.array([[(frequency + 1) * (port + 1) / 1000 for port in range(len(mappings))]
                                  for frequency in range(5)], dtype=complex))
    spd = SpdData("synthetic.spd", {}, models={"cap": SpiceModel("cap", 1e-7, valid=True)},
                  components=[Component(f"C{i}", "cap", [m.net, "GND"], is_cap=True)
                              for i, m in enumerate(mappings)])
    return config, spd, ts


def numeric_cells(sheet):
    return {cell.coordinate: (cell.value, cell.number_format, cell.font.bold)
            for row in sheet for cell in row if isinstance(cell.value, (int, float))}


def check_label(cell, value):
    assert cell.value == value
    assert cell.data_type == "s"
    assert cell.comment is None
    assert bool(cell.alignment.wrap_text) == ("\n" in value)
    if "\n" in value:
        assert cell.parent.row_dimensions[cell.row].height >= 16.5 * len(value.splitlines())


def test_channel_columns_fit_wide_characters_in_raw_and_display_names(tmp_path):
    config, spd, ts = sample(1)
    raw = "ADC_VDD_WIDE_MEMORY_WRITE_MONITOR_VOLTAGE_WINDOW_MAX"
    alias = "메모리컨트롤러_고속데이터버스_송수신부_보조전원_모니터링_피드백라인"
    config.mappings[0].channel = config.dcr[0].channel = raw
    config.mappings[1].display_name = alias
    for index, mapping in enumerate(config.mappings):
        mapping.group = f"Detail {index}"
    result = report.build_report(config, spd, ts, tmp_path / "channel-widths.xlsx")
    book = load_workbook(result.path)
    try:
        for index, (label, minimum_width) in enumerate(((raw, len(raw)), (alias, len(alias) * 1.5))):
            for name in ("PI ALL@100MHz", "PI ALL", "DCR", "CAP_수량", f"Detail {index}"):
                sheet = book[name]
                cell = next(cell for row in sheet for cell in row if cell.value == label)
                check_label(cell, label)
                assert sheet.column_dimensions[cell.column_letter].width >= minimum_width
    finally:
        book.close()


@pytest.mark.parametrize("detail", [False, True])
def test_later_channel_blocks_keep_the_earlier_merged_heading_wide_enough(detail):
    name = "MEMORY_CONTROLLER_WIDE_WRITE_WINDOW_MONITOR_AUXILIARY_POWER_SUPPLY_SENSE"
    spots = [(ChannelMapping(channel=channel, site="SITE0"), [0.1] * 5, [False] * 5)
             for channel in (name, "B", "C", "D", "E")]
    book = Workbook()
    sheet = book.active
    report._write_pi(sheet, spots, wide=True, detail=detail)
    first = sheet["B4"]
    check_label(first, f"{name} / [mΩ]")
    merged = next(r for r in sheet.merged_cells.ranges if first.coordinate in r)
    width = sum(sheet.column_dimensions[get_column_letter(c)].width
                for c in range(merged.min_col, merged.max_col + 1))
    assert width >= len(first.value)
    book.close()


@pytest.mark.parametrize("site_count", [2, 9])
@pytest.mark.parametrize("use_priority", [False, True])
@pytest.mark.parametrize("aliases", ["common", "mixed"])
def test_all_table_labels_use_display_names_without_changing_numeric_identity(tmp_path, site_count, use_priority, aliases):
    config, spd, ts = sample(site_count)
    config.settings.use_priority = use_priority
    original_config = deepcopy(config)
    baseline = report.build_report(config, spd, ts, tmp_path / "baseline.xlsx")
    for mapping in config.mappings:
        mapping.display_name = "=Common Display" if aliases == "common" else (
            f"Alias {mapping.channel}" if mapping.site == "SITE0" else "")
    result = report.build_report(config, spd, ts, tmp_path / "aliased.xlsx")
    with zipfile.ZipFile(result.path) as archive:
        assert not any(name.startswith("xl/comments/") for name in archive.namelist())
    original, changed = load_workbook(baseline.path), load_workbook(result.path)
    try:
        assert changed.sheetnames[:5] == original.sheetnames[:5]
        assert changed.sheetnames[5:] == (["=Common Display", "=Common Display (2)"]
                                        if aliases == "common" else original.sheetnames[5:])
        for sheet, previous in zip(changed, original):
            assert numeric_cells(sheet) == numeric_cells(previous)
            assert str(sheet.merged_cells) == str(previous.merged_cells)
            assert (sheet.max_row, sheet.max_column) == (previous.max_row, previous.max_column)
        for index, channel in enumerate(("=RAW_A/[0]", "RAW_B")):
            display = "=Common Display" if aliases == "common" else "\n".join(
                f"SITE{site}: " + (f"Alias {channel}" if site == 0 else channel) for site in range(site_count))
            check_label(changed["PI ALL@100MHz"].cell(5 + index, 2 + use_priority), display)
            check_label(changed["CAP_수량"].cell(6 + index, 2 + use_priority), display)
            check_label(changed["DCR"].cell(5 + index, 3 if site_count <= 8 else 2), display)
            if site_count <= 8:
                for site in range(site_count):
                    per_site = "=Common Display" if aliases == "common" else f"Alias {channel}" if site == 0 else channel
                    check_label(changed["PI ALL"].cell(5 + index * site_count + site, 2 + use_priority), per_site)
            else:
                cell = changed["PI ALL"].cell(4 + use_priority, 2 + index * 7)
                check_label(cell, f"{display} / [mΩ]")
                baseline_cell = original["PI ALL"].cell(4 + use_priority, 2 + index * 7)
                assert baseline_cell.value == f"{channel} / [mΩ]"
                assert baseline_cell.comment is None
            for sheet in changed:
                assert all(dim.height is None or dim.height <= 409 for dim in sheet.row_dimensions.values())
        for before, after in zip(original_config.mappings, config.mappings):
            after = deepcopy(after)
            after.display_name = ""
            assert after == before
        assert config.dcr == original_config.dcr
    finally:
        original.close()
        changed.close()


@pytest.mark.parametrize("site_count", [2, 9])
@pytest.mark.parametrize("use_priority", [False, True])
def test_detail_table_uses_per_site_names_or_one_reversible_merged_heading(tmp_path, site_count, use_priority):
    config, _, ts = sample(site_count)
    config.mappings = config.mappings[:site_count]
    baseline_spots = deepcopy(report._spot_rows(config, ts))
    config.mappings[0].display_name = "=Literal alias"
    spots = report._spot_rows(config, ts)
    book = Workbook()
    baseline = book.active
    sheet = book.create_sheet("Aliased")
    for target, values in ((baseline, baseline_spots), (sheet, spots)):
        report._write_pi(target, values, column=16, detail=True,
                         wide=site_count > 8, use_priority=use_priority)
    assert numeric_cells(sheet) == numeric_cells(baseline)
    assert str(sheet.merged_cells) == str(baseline.merged_cells)
    path = tmp_path / "detail.xlsx"
    book.save(path)
    book.close()
    book = load_workbook(path)
    try:
        sheet = book["Aliased"]
        channel = config.mappings[0].channel
        if site_count > 8:
            display = "\n".join(f"SITE{site}: " + ("=Literal alias" if site == 0 else channel)
                                 for site in range(site_count))
            cell = sheet.cell(4 + use_priority, 16)
            check_label(cell, f"{display} / [mΩ]")
            assert f"P{4 + use_priority}:U{4 + use_priority}" in sheet.merged_cells
        else:
            check_label(sheet.cell(5, 16 + use_priority), "=Literal alias")
            check_label(sheet.cell(6, 16 + use_priority), channel)
    finally:
        book.close()


@pytest.mark.parametrize("kind", ["100", "pi", "wide_pi", "cap", "dcr"])
def test_long_display_names_are_literal_wrapped_and_excel_row_height_safe(kind):
    mapping = ChannelMapping(1, channel="RAW", site="SITE0", display_name="A" * 9000)
    config = ReportConfig(mappings=[mapping], dcr=[DcrRow("SITE0", "RAW", 1)])
    spots = [(mapping, [1, 2, 3, 4, 5], [False] * 5)]
    book = Workbook()
    sheet = book.active
    if kind == "100":
        report._write_100(sheet, spots)
        cell = sheet["B5"]
    elif kind in ("pi", "wide_pi"):
        report._write_pi(sheet, spots, wide=kind == "wide_pi")
        cell = sheet["B4" if kind == "wide_pi" else "B5"]
    elif kind == "cap":
        report._write_caps(sheet, [{"channel": "RAW", "site": "SITE0", "capacitance_f": 1e-7,
                                    "label": "100nF", "count": 1}], None, config)
        cell = sheet["B6"]
    else:
        report._write_dcr(sheet, config.dcr, config)
        cell = sheet["C5"]
    assert cell.data_type == "s"
    assert cell.comment is None
    assert cell.alignment.wrap_text
    assert 16.5 < sheet.row_dimensions[cell.row].height <= 409
    if kind != "wide_pi":
        assert sheet.column_dimensions[cell.column_letter].width == 255
    book.close()


def test_dcr_alias_resolves_by_original_channel_site_and_ignores_excluded_mappings():
    config = ReportConfig(mappings=[ChannelMapping(channel="RAW", site="SITE0", display_name="Display"),
                                   ChannelMapping(channel="RAW", site="SITE1", display_name="Ignore", selected=False)],
                          dcr=[DcrRow("SITE0", "RAW", 1), DcrRow("SITE1", "RAW", 2),
                               DcrRow("SITE0", "STANDALONE", 3)])
    book = Workbook()
    report._write_dcr(book.active, config.dcr, config)
    check_label(book.active["C5"], "SITE0: Display\nSITE1: RAW")
    check_label(book.active["C6"], "STANDALONE")
    assert [book.active.cell(5, col).value for col in (4, 5)] == [1, 2]
    book.close()
