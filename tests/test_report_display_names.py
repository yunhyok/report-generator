import threading
from queue import Queue

import numpy as np
from openpyxl import load_workbook

from report_generator.core.models import ChannelMapping, Port, ReportConfig, TouchstoneData, source_signature
from report_generator.core.report import FREQUENCIES, build_report
from report_generator.jobs import run_job
from report_generator.validation import inspect_workbook


def sample():
    config = ReportConfig(mappings=[
        ChannelMapping(1, channel="RAW", site="SITE0", group="Original", net="SPD_NET_0"),
        ChannelMapping(2, channel="RAW", site="SITE1", group="Original", net="SPD_NET_1"),
    ])
    ts = TouchstoneData("synthetic.s2p", {}, [Port(1, "P1"), Port(2, "P2")],
                       np.array(FREQUENCIES), np.array([[1, 2]] * 5, dtype=complex))
    return config, ts


def numbers(path):
    values, _ = inspect_workbook(path)
    return {kind: {key: cell["value"] for key, cell in rows.items()} for kind, rows in values.items()}


def test_common_display_name_changes_presentation_and_preserves_source_values(tmp_path):
    config, ts = sample()
    config.settings.title = "Custom report"
    original = build_report(config, None, ts, tmp_path / "original.xlsx")
    alias = "=Rail_A/B_[literal]" + "x" * 35
    for mapping in config.mappings:
        mapping.display_name = alias
    config.mappings.append(ChannelMapping(1, channel="EXCLUDED", site="SITE0", group="Original",
                                          selected=False, display_name="Do not use"))
    result = build_report(config, None, ts, tmp_path / "aliased.xlsx")
    assert numbers(result.path) == numbers(original.path)
    name = result.sheets[-1]
    assert len(name) <= 31 and not any(char in name for char in "/[]")
    with_book = load_workbook(result.path)
    try:
        assert with_book[name]["A1"].value == f"Custom report · {alias}"
        assert with_book[name]["A1"].data_type == "s"
        assert with_book["PI ALL"]["A1"].value == "Custom report · Power Impedance"
    finally:
        with_book.close()
    assert [(m.channel, m.site, m.net, m.group) for m in config.mappings[:2]] == [
        ("RAW", "SITE0", "SPD_NET_0", "Original"), ("RAW", "SITE1", "SPD_NET_1", "Original")]


def test_split_groups_with_same_alias_keep_distinct_sheets_and_preview_images(tmp_path):
    config, ts = sample()
    for index, mapping in enumerate(config.mappings):
        mapping.group = f"Split {index}"
        mapping.display_name = "Common_alias"
    ts.path = str(tmp_path / "synthetic.s2p")
    (tmp_path / "synthetic.s2p").write_text("synthetic source for signature check", encoding="utf-8")
    ts.signature = source_signature(ts.path)
    queue = Queue()
    run_job("preview", {"config": config, "ts": ts, "folder": str(tmp_path / "preview")},
            queue, threading.Event())
    messages = list(queue.queue)
    assert not [payload for kind, payload in messages if kind == "error"]
    report, images = next(payload for kind, payload in messages if kind == "result")
    assert report.sheets[-2:] == ["Common_alias", "Common_alias (2)"]
    assert [label for label, _ in images] == ["Common_alias · Split 0", "Common_alias · Split 1"]
    assert len({path for _, path in images}) == 2
    book = load_workbook(report.path)
    try:
        for name in report.sheets[-2:]:
            assert book[name]["A1"].value == "Common_alias"
        assert book["Common_alias"]["P5"].value == "Common_alias"
        assert book["Common_alias (2)"]["P5"].value == "Common_alias"
        assert book["Common_alias"]["P5"].comment.text == "RAW"
        assert book["Common_alias (2)"]["P5"].comment.text == "RAW"
        assert book["Common_alias"]["Q5"].value == 0
        assert book["Common_alias (2)"]["Q5"].value == 1
    finally:
        book.close()


def test_partial_site_alias_keeps_group_and_numeric_identity(tmp_path):
    config, ts = sample()
    config.mappings[0].display_name = "Only_SITE0"
    result = build_report(config, None, ts, tmp_path / "mixed.xlsx")
    assert result.sheets[-1] == "Original"
    book = load_workbook(result.path)
    try:
        assert book["Original"]["A1"].value == "Original"
        assert book["Original"]["P5"].value == "Only_SITE0"
        assert book["Original"]["P5"].comment.text == "RAW"
        assert book["Original"]["P6"].value == "RAW"
        assert book["Original"]["P6"].comment is None
        assert book["Original"]["Q5"].value == 0
        assert book["Original"]["Q6"].value == 1
    finally:
        book.close()
