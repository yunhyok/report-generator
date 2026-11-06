import json

import pytest

from report_generator.core.dcr import assign_column, parse_column, parse_dcr_table, read_dcr_sheet, read_range
from report_generator.core.models import ChannelMapping, DcrRow, ReportConfig, source_signature
from report_generator.core.project import load_project, save_project


def test_project_roundtrip_and_changed_source(tmp_path):
    source = tmp_path / "board.txt"
    source.write_text("synthetic")
    config = ReportConfig(mappings=[ChannelMapping(site="SITE0", channel="VDD", net="SPD_VDD_0",
                                                  display_name="VDD_CORE")], dcr=[DcrRow("SITE0", "VDD", 0)])
    project = tmp_path / "test.report.json"
    config.settings.plot_line_width_pt = 3.5
    save_project(project, config, {"spd": str(source)}, {"spd": source_signature(source)})
    restored, _, _, changed = load_project(project)
    assert restored == config and not changed
    source.write_text("changed synthetic")
    restored, _, _, changed = load_project(project)
    assert changed == ["spd"] and not restored.mappings[0].confirmed


def test_existing_projects_keep_ranks_but_explicitly_disabled_ranks_stay_off(tmp_path):
    path = tmp_path / "priority.report.json"
    config = ReportConfig(mappings=[ChannelMapping(site="A", channel="MAIN", priority="2")])
    save_project(path, config, {})
    assert not load_project(path)[0].settings.use_priority
    legacy = json.loads(path.read_text(encoding="utf-8"))
    del legacy["config"]["settings"]["use_priority"]
    del legacy["config"]["settings"]["plot_line_width_pt"]
    del legacy["config"]["channel_settings_source_path"]
    del legacy["config"]["mappings"][0]["display_name"]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    restored = load_project(path)[0]
    assert restored.settings.plot_line_width_pt == 1.0
    assert restored.mappings[0].display_name == ""
    assert restored.settings.use_priority and restored.mappings[0].priority == "2"
    legacy["config"]["mappings"][0]["priority"] = ""
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert not load_project(path)[0].settings.use_priority
    legacy["config"]["settings"]["use_priority"] = "false"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(ValueError, match="사용 여부"):
        load_project(path)


@pytest.mark.parametrize("width", [0.9, 5.1, "2", True, None, float("nan"), float("inf")])
def test_project_rejects_invalid_plot_width(tmp_path, width):
    path = tmp_path / "width.report.json"
    save_project(path, ReportConfig(), {})
    data = json.loads(path.read_text(encoding="utf-8"))
    data["config"]["settings"]["plot_line_width_pt"] = width
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="그래프 선 굵기"):
        load_project(path)


def test_dcr_order_units_and_count():
    rows = [ChannelMapping(site="SITE10", channel="VDD"), ChannelMapping(site="SITE2", channel="VDD")]
    assigned = assign_column(parse_column("0\n0.002", "Ω"), rows)
    assert [(r.site, r.resistance_mohm) for r in assigned] == [("SITE2", 0), ("SITE10", 2)]
    for text in ("nan", "-1", "1\t2", "1\n\n2"):
        with pytest.raises(ValueError):
            parse_column(text)
    with pytest.raises(ValueError, match="행 수"):
        assign_column([1], rows)


def test_range_reports_blank_cell(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    book.active["A1"] = 1
    path = tmp_path / "input.xlsx"
    book.save(path)
    assert read_range(path, "Sheet", "A1:A1") == [1]
    with pytest.raises(ValueError):
        read_range(path, "Sheet", "A1:A2")


def test_keyed_dcr_clipboard_reorders_headers_and_preserves_mapping_names():
    mappings = [ChannelMapping(site="SITE2", channel="VDD"), ChannelMapping(site="SITE1", channel="VDD")]
    text = "측정 결과\n설명\tDCR (Ω)\t채널명\t사이트\t비고\nignore\t.002\tvdd\tsite1\tx\nignore\t0\tVDD\tSITE2\tx"
    assert parse_dcr_table(text, mappings) == [DcrRow("SITE1", "VDD", 2), DcrRow("SITE2", "VDD", 0)]


@pytest.mark.parametrize("text, message", [
    ("Site\tChannel\tDCR\nA\tVDD\t1\na\tvdd\t2", "중복"),
    ("Site\tChannel\tDCR\nX\tVDD\t1", "없는"),
    ("Site\tChannel\tDCR\nA\tVDD\t1", "누락"),
    ("Site\tChannel\tDCR\nA\tVDD\t", "비어"),
    ("Site\tChannel\tDCR\nA\tVDD\t#VALUE!", "숫자"),
    ("Site\tChannel\tDCR\t저항\nA\tVDD\t1\t1", "제목이 중복"),
    ("Site\tChannel\tDCR [MΩ]\nA\tVDD\t1", "단위"),
    ("Site\tChannel\tDCR [mΩ]\nA\tVDD\t1 Ω", "단위"),
])
def test_keyed_dcr_rejects_bad_tables_without_mutating_mappings(text, message):
    mappings = [ChannelMapping(site="A", channel="VDD"), ChannelMapping(site="B", channel="VDD")]
    before = [vars(mapping).copy() for mapping in mappings]
    with pytest.raises(ValueError, match=message):
        parse_dcr_table(text, mappings)
    assert [vars(mapping) for mapping in mappings] == before


@pytest.mark.parametrize("value, expected", [("2 Ω", 2000), ("2 mΩ", 2), ("2 µΩ", .002),
                                             ("2 uΩ", .002), ("2 μΩ", .002), ("2e-3 ohm", 2)])
def test_keyed_dcr_explicit_cell_units_without_header_unit(value, expected):
    assert parse_dcr_table(f"site\tchannel\tdcr\n01\tRAIL\t{value}", []) == [DcrRow("01", "RAIL", expected)]


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "1e309", "1e308 Ω", "TRUE", "1 kΩ"])
def test_keyed_dcr_rejects_invalid_or_overflow_values(value):
    with pytest.raises(ValueError):
        parse_dcr_table(f"site\tchannel\tdcr\nA\tVDD\t{value}", [])


def test_keyed_dcr_only_validates_without_port_mappings():
    from report_generator.core.mapping import validate_mappings
    rows = parse_dcr_table("site\tchannel\tdcr\nA\tVDD\t1", [])
    validate_mappings(ReportConfig(dcr=rows))
    with pytest.raises(ValueError, match="선택"):
        parse_dcr_table("site\tchannel\tdcr\nA\tVDD\t1", [ChannelMapping(site="A", channel="VDD", selected=False)])


def test_keyed_dcr_excel_scans_full_sheet_and_rejects_uncached_formula(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "측정"
    sheet.append(["DCR 데이터"])
    sheet.append([])
    sheet.append(["비고", "Channel", "DCR [µΩ]", "Site"])
    sheet.append(["=1+1", "VDD", 500, 0])
    path = tmp_path / "keyed.xlsx"
    book.save(path)
    mappings = [ChannelMapping(site="0", channel="VDD")]
    assert read_dcr_sheet(path, "측정", mappings) == [DcrRow("0", "VDD", .5)]
    sheet.cell(4, 3, "=1+1")
    book.save(path)
    with pytest.raises(ValueError, match="4행 3열.*수식의 계산 결과"):
        read_dcr_sheet(path, "측정", mappings)
    with pytest.raises(ValueError, match="시트 이름"):
        read_dcr_sheet(path, "missing", mappings)


def test_dcr_matrix_report_roundtrip_and_clipboard(tmp_path):
    from openpyxl import Workbook
    from report_generator.core.report import _write_dcr
    mappings = [ChannelMapping(site="SITE2", channel="A", display_name="Core"),
                ChannelMapping(site="SITE1", channel="A", display_name="Core"),
                ChannelMapping(site="SITE2", channel="B")]
    rows = [DcrRow("SITE2", "A", 1), DcrRow("SITE1", "A", 2), DcrRow("SITE2", "B", 0)]
    book = Workbook()
    _write_dcr(book.active, rows, ReportConfig(mappings=mappings))
    path = tmp_path / "report.xlsx"
    book.save(path)
    expected = [rows[1], rows[0], rows[2]]
    assert read_dcr_sheet(path, "Sheet", mappings) == expected
    assert parse_dcr_table("Priority\tChannel / [mΩ]\tSITE1\tSITE2\n1\tCore\t2\t1\n\tB\t\t0", mappings) == [
        DcrRow("SITE1", "A", 2), DcrRow("SITE2", "A", 1), DcrRow("SITE2", "B", 0)]


def test_dcr_matrix_dcr_only_and_missing_selected_cells():
    text = "Channel\t01\t02\nRAIL\t0\t2"
    assert parse_dcr_table(text, []) == [DcrRow("01", "RAIL", 0), DcrRow("02", "RAIL", 2)]
    with pytest.raises(ValueError, match="여러 줄"):
        parse_dcr_table('Channel\t01\t02\n"01: Core\n02: Logic"\t0\t2', [])
    mappings = [ChannelMapping(site="01", channel="RAIL"), ChannelMapping(site="02", channel="RAIL")]
    with pytest.raises(ValueError, match="누락"):
        parse_dcr_table("Channel\t01\t02\nRAIL\t0\t", mappings)
    with pytest.raises(ValueError, match="사이트 제목이 중복"):
        parse_dcr_table("Channel\tA\ta\nRAIL\t1\t2", [])


def test_keyed_dcr_rejects_duplicate_or_ambiguous_mappings():
    text = "Site\tChannel\tDCR\nA\tCore\t1"
    mappings = [ChannelMapping(site="A", channel="VDD", display_name="Core"),
                ChannelMapping(site="A", channel="VCC", display_name="Core")]
    with pytest.raises(ValueError, match="모호"):
        parse_dcr_table(text, mappings)
    with pytest.raises(ValueError, match="매핑.*중복"):
        parse_dcr_table(text, [ChannelMapping(site="A", channel="Core"), ChannelMapping(site="a", channel="core")])


def test_dcr_keyed_natural_order_and_identical_mapping_deduplication():
    mappings = [ChannelMapping(site="SITE10", channel="VDD"), ChannelMapping(site="SITE2", channel="VDD"),
                ChannelMapping(site="SITE10", channel="VDD")]
    assert parse_dcr_table("Site\tChannel\tDCR\nSITE10\tVDD\t10\nSITE2\tVDD\t2", mappings) == [
        DcrRow("SITE2", "VDD", 2), DcrRow("SITE10", "VDD", 10)]


def test_dcr_matrix_site_specific_display_names_roundtrip(tmp_path):
    from openpyxl import Workbook
    from report_generator.core.report import _write_dcr
    mappings = [ChannelMapping(site="SITE0", channel="VDD", display_name="Core"),
                ChannelMapping(site="SITE1", channel="VDD", display_name="Logic")]
    rows = [DcrRow("SITE0", "VDD", 1), DcrRow("SITE1", "VDD", 2)]
    book = Workbook()
    _write_dcr(book.active, rows, ReportConfig(mappings=mappings))
    path = tmp_path / "labels.xlsx"
    book.save(path)
    assert read_dcr_sheet(path, "Sheet", mappings) == rows
    assert parse_dcr_table('Channel / [mΩ]\tSITE0\tSITE1\n"SITE0: Core\nSITE1: Logic"\t1\t2', mappings) == rows


def test_dcr_excel_cached_formula_value_and_error(tmp_path):
    from openpyxl import Workbook
    from zipfile import ZipFile
    book = Workbook()
    book.active.append(["Site", "Channel", "DCR"])
    book.active.append(["A", "VDD", "=1+1"])
    path = tmp_path / "cached.xlsx"
    book.save(path)
    with ZipFile(path) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["xl/worksheets/sheet1.xml"] = files["xl/worksheets/sheet1.xml"].replace(b"<f>1+1</f><v></v>", b"<f>1+1</f><v>2</v>")
    with ZipFile(path, "w") as output:
        for name, data in files.items():
            output.writestr(name, data)
    assert read_dcr_sheet(path, "Sheet", []) == [DcrRow("A", "VDD", 2)]
    book.active.cell(2, 1, "#REF!")
    book.save(path)
    with pytest.raises(ValueError, match="오류"):
        read_dcr_sheet(path, "Sheet", [])


def test_dcr_irdrop_name_and_numeric_sites_in_shuffled_excel(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(["메모", "DCR_IRDrop[mΩ]", "SITE", "전압", "Name    ", "전류"])
    sheet.append(["x", 4, 1, 1, "RAIL_B", .1])
    sheet.append(["x", 2, 0, 1, "RAIL_B", .1])
    sheet.append(["x", 3, 1, 1, "RAIL_A", .1])
    sheet.append(["x", 1, 0, 1, "RAIL_A", .1])
    path = tmp_path / "measurements.xlsx"
    book.save(path)
    mappings = [ChannelMapping(site=f"SITE{site}", channel=channel)
                for site in (1, 0) for channel in ("RAIL_B", "RAIL_A")]
    assert read_dcr_sheet(path, "Sheet", mappings) == [DcrRow("SITE0", "RAIL_A", 1),
        DcrRow("SITE0", "RAIL_B", 2), DcrRow("SITE1", "RAIL_A", 3), DcrRow("SITE1", "RAIL_B", 4)]
    assert read_dcr_sheet(path, "Sheet", []) == [DcrRow("1", "RAIL_B", 4), DcrRow("0", "RAIL_B", 2),
                                                 DcrRow("1", "RAIL_A", 3), DcrRow("0", "RAIL_A", 1)]


def test_dcr_site_aliases_prefer_exact_and_reject_ambiguous_numeric_names():
    mappings = [ChannelMapping(site="00", channel="VDD"), ChannelMapping(site="SITE0", channel="VDD")]
    with pytest.raises(ValueError, match="사이트 이름이 모호"):
        parse_dcr_table("Site\tName\tDCR\n0\tVDD\t1", mappings)
    assert parse_dcr_table("Site\tName\tDCR\nSITE0\tVDD\t2\n00\tVDD\t1", mappings) == [
        DcrRow("00", "VDD", 1), DcrRow("SITE0", "VDD", 2)]
    mappings = [ChannelMapping(site="0", channel="VDD"), ChannelMapping(site="SITE0", channel="VDD")]
    assert parse_dcr_table("Site\tName\tDCR\nSITE0\tVDD\t2\n0\tVDD\t1", mappings) == [
        DcrRow("0", "VDD", 1), DcrRow("SITE0", "VDD", 2)]


def test_dcr_irdrop_matches_only_unambiguous_known_numbered_family_prefix():
    text = "Name\tSITE\tDCR_IRDrop[mΩ]\nPOWER\t0\t1"
    mappings = [ChannelMapping(site="SITE0", channel="LGA1-POWER")]
    assert parse_dcr_table(text, mappings) == [DcrRow("SITE0", "LGA1-POWER", 1)]
    with pytest.raises(ValueError, match="모호"):
        parse_dcr_table(text, mappings + [ChannelMapping(site="SITE0", channel="BGA1-POWER")])
    with pytest.raises(ValueError, match="없는"):
        parse_dcr_table(text.replace("DCR_IRDrop", "DCR"), mappings)
    with pytest.raises(ValueError, match="없는"):
        parse_dcr_table(text, [ChannelMapping(site="SITE0", channel="CUSTOM1-POWER")])
    explicit = mappings + [ChannelMapping(site="SITE0", channel="POWER")]
    result = parse_dcr_table(text + "\nLGA1-POWER\t0\t2", explicit)
    assert result == [DcrRow("SITE0", "LGA1-POWER", 2), DcrRow("SITE0", "POWER", 1)]


def test_dcr_raw_channel_has_precedence_over_another_display_name():
    mappings = [ChannelMapping(site="A", channel="VDD", display_name="Core"),
                ChannelMapping(site="A", channel="Core")]
    assert parse_dcr_table("Site\tChannel\tDCR\nA\tCore\t1\nA\tVDD\t2", mappings) == [
        DcrRow("A", "Core", 1), DcrRow("A", "VDD", 2)]


def test_dcr_matrix_does_not_drop_values_without_site_headers():
    with pytest.raises(ValueError, match="사이트 제목이 없는 값"):
        parse_dcr_table("Channel\tA\t\nVDD\t1\t2", [])


def test_dcr_sheet_errors_and_empty_clipboard_are_clear(tmp_path):
    from openpyxl import Workbook
    for text in ("", "Site\tChannel\tDCR", "1\n2"):
        with pytest.raises(ValueError):
            parse_dcr_table(text, [])
    with pytest.raises(ValueError, match="파일을 열 수 없습니다"):
        read_dcr_sheet(tmp_path / "missing.xlsx", "Sheet", [])
    path = tmp_path / "empty.xlsx"
    Workbook().save(path)
    with pytest.raises(ValueError, match="제목"):
        read_dcr_sheet(path, "Sheet", [])


@pytest.mark.parametrize("rows", [
    [["VDD", 1, "=1+1"]],
    [["VDD", 1, None], ["=1+1", "=1+1", "=1+1"]],
    [["VDD", 1, None], [None, "=1+1", None]],
])
def test_dcr_matrix_rejects_uncached_formulas_before_skipping_cells_or_rows(tmp_path, rows):
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["Channel", "SITE0", "SITE1"])
    for row in rows:
        book.active.append(row)
    path = tmp_path / "uncached.xlsx"
    book.save(path)
    with pytest.raises(ValueError, match="수식의 계산 결과"):
        read_dcr_sheet(path, "Sheet", [])


def test_dcr_matrix_keeps_structural_blanks_but_rejects_uncached_site_heading(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["Channel", "SITE0", "SITE1"])
    book.active.append(["VDD", 1, None])
    path = tmp_path / "blank.xlsx"
    book.save(path)
    assert read_dcr_sheet(path, "Sheet", []) == [DcrRow("SITE0", "VDD", 1)]
    book.active.cell(1, 3, "=1+1")
    book.save(path)
    with pytest.raises(ValueError, match="1행 3열.*수식의 계산 결과"):
        read_dcr_sheet(path, "Sheet", [])


@pytest.mark.parametrize("text", ["1,,2", "1,2", "12,34", "1234,567"])
def test_dcr_rejects_malformed_thousands_grouping_in_all_inputs(text):
    with pytest.raises(ValueError, match="쉼표"):
        parse_column(text)
    with pytest.raises(ValueError, match="쉼표"):
        parse_dcr_table(f"Site\tChannel\tDCR\nA\tVDD\t{text} mΩ", [])


def test_dcr_shared_numeric_conversion_checks_scaled_result_and_valid_thousands():
    assert parse_column("1,234.5\n1,234,567") == [1234.5, 1234567]
    with pytest.raises(ValueError, match="유한"):
        parse_column("1e308", "Ω")


def test_dcr_matrix_rejects_swapped_raw_and_display_labels(tmp_path):
    from openpyxl import Workbook
    from report_generator.core.report import _write_dcr
    mappings = [ChannelMapping(site="SITE0", channel="A", display_name="B"),
                ChannelMapping(site="SITE0", channel="B", display_name="A")]
    rows = [DcrRow("SITE0", "A", 1), DcrRow("SITE0", "B", 2)]
    book = Workbook()
    _write_dcr(book.active, rows, ReportConfig(mappings=mappings))
    path = tmp_path / "swapped.xlsx"
    book.save(path)
    with pytest.raises(ValueError, match="표시명이 모호"):
        read_dcr_sheet(path, "Sheet", mappings)
    with pytest.raises(ValueError, match="표시명이 모호"):
        parse_dcr_table("Channel\tSITE0\nB\t1\nA\t2", mappings)
    assert parse_dcr_table("Site\tChannel\tDCR\nSITE0\tA\t1\nSITE0\tB\t2", mappings) == rows
