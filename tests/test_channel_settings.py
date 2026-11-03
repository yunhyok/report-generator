from datetime import datetime
import re
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook

from report_generator.core.channel_settings import read_channel_settings, write_channel_template
from report_generator.core.models import ChannelMapping


def mappings():
    return [ChannelMapping(channel=" A ", site="01", priority="9", group="existing"),
            ChannelMapping(channel="a", site="02", priority="9", group="other"),
            ChannelMapping(channel="B", site="01", selected=False),
            ChannelMapping(channel="incomplete", site="")]


def workbook(tmp_path, rows):
    path = tmp_path / "settings.xlsx"
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()
    return path


def test_template_contains_only_distinct_selected_complete_literal_ids(tmp_path):
    rows = mappings() + [ChannelMapping(channel="a", site="01"),
                         ChannelMapping(channel="=1+1", site="0002", priority="7", group="g")]
    path = tmp_path / "template.xlsx"
    write_channel_template(path, rows)
    book = load_workbook(path, data_only=False)
    sheet = book.active
    assert list(sheet.values) == [("채널명", "사이트", "priority", "group"),
                                  ("A", "01", None, None), ("a", "02", None, None),
                                  ("=1+1", "0002", None, None)]
    assert sheet["A4"].data_type == sheet["B4"].data_type == "s"
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:D4"
    assert sheet["A1"].font.bold
    assert sheet.column_dimensions["A"].width > 20
    book.close()
    assert rows[0].priority == "9" and rows[0].group == "existing"


def test_reordered_headers_sparse_updates_and_channel_priority(tmp_path):
    path = workbook(tmp_path, [["unrelated", " GrOUP ", " SITE ", " PRIORITY ", " channel "],
                              ["ignored", " 새 그룹 ", " 01 ", 2.0, " a "],
                              [None, None, "02", "0002", "A"],
                              [None, None, "missing", None, "unknown"]])
    rows = mappings()
    assert read_channel_settings(path, rows) == ({"a": "2"}, {("a", "01"): "새 그룹"})
    assert [(row.priority, row.group) for row in rows[:2]] == [("9", "existing"), ("9", "other")]


@pytest.mark.parametrize("headers,values,expected", [
    (["사이트", "채널명", "priority"], ["01", "A", "별도 구분"], ({"a": "별도 구분"}, {})),
    (["group", "channel", "site"], [3.0, "A", "01"], ({}, {("a", "01"): "3"})),
    (["group", "channel", "site"], [3.5, "A", "01"], ({}, {("a", "01"): "3.5"})),
])
def test_optional_columns_can_be_removed_and_group_numbers_are_labels(tmp_path, headers, values, expected):
    path = workbook(tmp_path, [headers, values])
    assert read_channel_settings(path, mappings()) == expected


@pytest.mark.parametrize("headers,match", [
    (["channel", "site"], "priority 또는 group"),
    (["channel", "priority"], "사이트"),
    (["channel", "채널명", "site", "group"], "중복"),
    (["channel", "site", "group", " GROUP "], "중복"),
])
def test_invalid_headers(tmp_path, headers, match):
    with pytest.raises(ValueError, match=match):
        read_channel_settings(workbook(tmp_path, [headers]), mappings())


@pytest.mark.parametrize("rows,match", [
    ([["A", "01", None, None]], "적용할"),
    ([["A", "01", "  ", "  "]], "적용할"),
    ([[None, "01", 1, None]], "2행.*모두"),
    ([["A", None, None, "g"]], "2행.*모두"),
    ([["B", "01", 1, None]], "2행.*선택된 매핑에 없는"),
    ([["A", "unknown", 1, None]], "2행.*선택된 매핑에 없는"),
    ([["A", "01", 1, None], ["a", "01", None, "g"]], "2, 3행.*중복"),
    ([["A", "01", 1, None], ["a", "02", 2, None]], "2, 3행.*priority가 다릅니다"),
    ([["A", "01", "별도", None], ["a", "02", "다른 구분", None]], "2, 3행.*priority가 다릅니다"),
    ([["A", "01", 0, None]], "2행 priority"),
    ([["A", "01", 1.5, None]], "2행 priority"),
    ([["A", "01", True, None]], "2행 priority"),
    ([["A", "01", None, False]], "2행 group"),
    ([["A", "01", None, datetime(2026, 1, 1)]], "2행 group"),
    ([["A", "01", "=1+1", None]], "2행 priority.*수식"),
    ([["A", "01", None, "=1+1"]], "2행 group.*수식"),
    ([["=1+1", "01", None, None]], "2행 channel.*수식"),
    ([["A", "=1+1", None, None]], "2행 site.*수식"),
])
def test_rejects_invalid_rows_without_mutating_current_settings(tmp_path, rows, match):
    path = workbook(tmp_path, [["channel", "site", "priority", "group"], *rows])
    current = mappings()
    with pytest.raises(ValueError, match=match):
        read_channel_settings(path, current)
    assert current[0].priority == "9" and current[0].group == "existing"


def test_literal_formula_like_identifiers_round_trip_when_cells_are_text(tmp_path):
    rows = [ChannelMapping(channel="=rail", site="001")]
    path = tmp_path / "literal.xlsx"
    write_channel_template(path, rows)
    book = load_workbook(path)
    book.active["C2"] = 1
    book.save(path)
    book.close()
    assert read_channel_settings(path, rows) == ({"=rail": "1"}, {})


def test_failed_atomic_save_keeps_original_and_cleans_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.xlsx"
    path.write_bytes(b"original")

    def fail_replace(*args):
        raise PermissionError("locked")

    monkeypatch.setattr("report_generator.core.channel_settings.os.replace", fail_replace)
    with pytest.raises(ValueError, match="Excel에서 파일을 닫고"):
        write_channel_template(path, mappings())
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_format_missing_file_and_empty_selection_errors(tmp_path):
    for operation in (read_channel_settings, write_channel_template):
        with pytest.raises(ValueError, match=".xlsx"):
            operation(tmp_path / "wrong.xls", mappings())
    with pytest.raises(ValueError, match="열 수 없습니다"):
        read_channel_settings(tmp_path / "missing.xlsx", mappings())
    with pytest.raises(ValueError, match="하나 이상 선택"):
        write_channel_template(tmp_path / "empty.xlsx", [])


def test_oversized_sparse_sheet_is_rejected_before_iteration(tmp_path):
    path = workbook(tmp_path, [["channel", "site", "group"]])
    book = load_workbook(path)
    book.active["A100001"] = "unused"
    book.save(path)
    book.close()
    with pytest.raises(ValueError, match="시트가 너무 큽니다"):
        read_channel_settings(path, mappings())


@pytest.mark.parametrize("dimensions", ["missing", "understated"])
def test_streams_actual_cells_when_dimensions_are_missing_or_understated(tmp_path, dimensions):
    path = tmp_path / "third-party.xlsx"
    book = Workbook(write_only=True)
    sheet = book.create_sheet()
    sheet.append(["group", "site", "priority", "channel"])
    sheet.append(["그룹", "01", 2, "A"])
    book.save(path)
    book.close()
    if dimensions == "understated":
        with ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        xml = members["xl/worksheets/sheet1.xml"]
        members["xl/worksheets/sheet1.xml"] = xml.replace(b"<sheetPr>", b'<dimension ref="A1:A1"/><sheetPr>', 1)
        with ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
    assert read_channel_settings(path, mappings()) == ({"a": "2"}, {("a", "01"): "그룹"})


def test_understated_dimensions_cannot_bypass_streaming_row_limit(tmp_path):
    path = workbook(tmp_path, [["channel", "site", "group"], ["A", "01", "g"]])
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    xml = members["xl/worksheets/sheet1.xml"]
    xml = re.sub(rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1:A1"/>', xml)
    members["xl/worksheets/sheet1.xml"] = xml.replace(b'<row r="2">', b'<row r="100001">')
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="시트가 너무 큽니다"):
        read_channel_settings(path, mappings())
