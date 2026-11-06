from copy import deepcopy
from queue import Queue
from threading import Event

import numpy as np
import openpyxl
import pytest

from report_generator import jobs
from report_generator.core import render
from report_generator.core.models import ChannelMapping, Port, ReportConfig, TouchstoneData, source_signature
from report_generator.core.report import FREQUENCIES, plot_groups
from report_generator.validation import _expected, compare_values, inspect_workbook


CHANNELS = ["Unnamed", "Third", "AuxA", "FirstB", "FirstA", "AuxB", "Other"]
PRIORITIES = ["", "10", "Auxiliary", "2", "2", "Auxiliary", "Other group"]
ORDERED = ["FirstB", "FirstA", "Third", "AuxA", "AuxB", "Other", "Unnamed"]


def sample(tmp_path, blank):
    source = tmp_path / "source.s14p"
    source.write_text("synthetic source", encoding="utf-8")
    mappings = [ChannelMapping(index + 1, site=f"SITE{site}", channel=channel,
                               group="" if blank else channel, priority=PRIORITIES[CHANNELS.index(channel)])
                for index, (site, channel) in enumerate((site, channel) for site in range(2) for channel in CHANNELS)]
    config = ReportConfig(mappings=mappings)
    config.settings.use_priority = True
    config.settings.board_type = "PCB"
    ts = TouchstoneData(str(source), source_signature(source), [Port(i + 1, f"P{i + 1}") for i in range(14)],
                        np.array(FREQUENCIES), np.tile(np.arange(1, 15), (5, 1)).astype(complex))
    return config, ts


@pytest.mark.parametrize("blank", [False, True])
def test_default_channel_groups_follow_priority_with_stable_ties_and_sections(tmp_path, blank):
    config, ts = sample(tmp_path, blank)
    original = deepcopy(config)
    groups = plot_groups(config, ts)
    assert list(groups) == ORDERED
    assert all([member.site for member in members] == ["SITE0", "SITE1"] for members in groups.values())
    assert config == original
    config.settings.use_priority = False
    config.mappings[0].priority = "-1"  # Disabled priority drafts are not parsed.
    assert list(plot_groups(config, ts)) == CHANNELS
    config.settings.use_priority = True
    for mapping in config.mappings:
        mapping.priority = ""
    assert list(plot_groups(config, ts)) == CHANNELS
    assert plot_groups(config, None) == {}


def test_explicit_groups_keep_members_and_positions_among_ranked_channel_groups(tmp_path):
    config, ts = sample(tmp_path, True)
    for mapping in config.mappings:
        if mapping.channel in {"Third", "AuxA"}:
            mapping.group = "Overlay"
        elif mapping.channel == "AuxB":
            mapping.group = "Renamed single channel"
    groups = plot_groups(config, ts)
    assert list(groups) == ["FirstB", "Overlay", "FirstA", "Other", "Renamed single channel", "Unnamed"]
    assert [(m.channel, m.site) for m in groups["Overlay"]] == [
        ("Third", "SITE0"), ("AuxA", "SITE0"), ("Third", "SITE1"), ("AuxA", "SITE1")]
    # A custom group using another channel's name remains one explicit group.
    for mapping in config.mappings:
        if mapping.channel == "Other":
            mapping.group = "FirstB"
    groups = plot_groups(config, ts)
    assert list(groups) == ["FirstA", "Overlay", "FirstB", "Unnamed", "Renamed single channel"]
    assert {m.channel for m in groups["FirstB"]} == {"FirstB", "Other"}


def test_named_section_order_includes_channels_in_explicit_groups(tmp_path):
    config, ts = sample(tmp_path, False)
    for mapping in config.mappings:
        if mapping.channel == "Third":
            mapping.priority = "Other group"
            mapping.group = "Overlay"
    assert list(plot_groups(config, ts)) == ["FirstB", "Overlay", "FirstA", "Other", "AuxA", "AuxB", "Unnamed"]


@pytest.mark.parametrize("blank", [False, True])
def test_preview_and_export_plot_and_detail_sheet_order_match(tmp_path, monkeypatch, blank):
    config, ts = sample(tmp_path, blank)
    for mapping in config.mappings:
        mapping.display_name = f"Alias {CHANNELS.index(mapping.channel)}"
    config.mappings.extend([
        ChannelMapping(1, site="hidden", channel="Hidden", priority="invalid", selected=False),
        ChannelMapping(1, site="pending", channel="Pending", priority="invalid", selected=False, confirmed=False)])
    titles = []
    finish = render._finish

    def capture(figure, path):
        titles.append(figure.axes[0].get_title())
        return finish(figure, path)

    monkeypatch.setattr(render, "_finish", capture)
    messages = Queue()
    jobs.run_job("preview", {"config": config, "ts": ts, "folder": str(tmp_path / "preview")}, messages, Event())
    results = list(messages.queue)
    assert not [payload for kind, payload in results if kind == "error"]
    report, images = next(payload for kind, payload in results if kind == "result")
    aliases = [f"Alias {CHANNELS.index(channel)}" for channel in ORDERED]
    assert report.sheets == ["PI ALL@100MHz", "PI ALL", *aliases]
    assert [name for name, _ in images] == [f"{alias} · {channel}" for alias, channel in zip(aliases, ORDERED)]
    assert titles == aliases * 2  # Embedded Excel plots and separate preview plots.
    book = openpyxl.load_workbook(report.path, data_only=True)
    try:
        assert book.sheetnames == report.sheets
        for alias, channel in zip(aliases, ORDERED):
            sheet = book[alias]
            assert len(sheet._images) == 1
            assert sheet["Q5"].value == alias
            assert sheet["Q6"].value == alias
            assert sheet["S5"].value == CHANNELS.index(channel) + 1
            assert sheet["S6"].value == CHANNELS.index(channel) + 8
    finally:
        book.close()
    actual, _ = inspect_workbook(report.path)
    expected = _expected(config, None, ts, report.omitted)
    detail = compare_values(actual["PI_DETAIL"], expected["PI_DETAIL"])
    assert detail["matches"] == 70
    assert detail["missing"] == detail["extra"] == detail["differences"] == 0
