from pathlib import Path

import pytest

from report_generator.core.dcr import assign_column, parse_column, read_range
from report_generator.core.models import ChannelMapping, DcrRow, ReportConfig, source_signature
from report_generator.core.project import load_project, save_project


def test_project_roundtrip_and_changed_source(tmp_path):
    source = tmp_path / "board.txt"
    source.write_text("synthetic")
    config = ReportConfig(mappings=[ChannelMapping(site="SITE0", channel="VDD")], dcr=[DcrRow("SITE0", "VDD", 0)])
    project = tmp_path / "test.report.json"
    save_project(project, config, {"spd": str(source)}, {"spd": source_signature(source)})
    restored, _, _, changed = load_project(project)
    assert restored == config and not changed
    source.write_text("changed synthetic")
    restored, _, _, changed = load_project(project)
    assert changed == ["spd"] and not restored.mappings[0].confirmed


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
