import numpy as np
import pytest

from report_generator.core.mapping import suggest_mappings, validate_mappings
from report_generator.core.models import ChannelMapping, Component, DcrRow, Port, ReportConfig, SpdData, TouchstoneData


def touchstone(*ports):
    return TouchstoneData("synthetic", {}, list(ports), np.array([1.0]), np.ones((1, len(ports))))


def test_preserves_duplicate_endpoints_and_requires_explicit_confirmation():
    ts = touchstone(Port(1, "LOAD-RAIL_A/0", "Port31_SITE0::RAIL_A/0"),
                    Port(2, "SOURCE-RAIL_A/0", "Port17_LGA::RAIL_A/0"))
    rows = suggest_mappings(None, ts)
    assert len(rows) == 2
    assert rows[0].channel == rows[1].channel == "RAIL_A"
    assert rows[0].site == rows[1].site == "SITE0"
    assert "Port31_SITE0" in rows[0].source_name
    assert all(row.selected and not row.confirmed for row in rows)
    with pytest.raises(ValueError):
        validate_mappings(rows, ts)
    rows[1].selected = False
    rows[0].confirmed = True
    validate_mappings(ReportConfig(mappings=rows), ts)


def test_matches_spd_explicit_fields_and_retains_raw_net():
    spd = SpdData("synthetic", {}, ports=[Port(91, "Port91_U2::U2_RAIL_B", net="U2_RAIL_B", site="SITE2", channel="RAIL_B")])
    ts = touchstone(Port(1, "U2-RAIL_B"))
    row = suggest_mappings(spd, ts)[0]
    assert (row.site, row.channel, row.net, row.port_index) == ("SITE2", "RAIL_B", "U2_RAIL_B", 1)
    assert row.spd_port == spd.ports[0].name
    assert row.group == row.channel
    assert row.confirmed
    validate_mappings([row], ts)


def test_spd_only_cap_nets_and_disabled_ports():
    spd = SpdData("synthetic", {}, ports=[Port(4, "RAIL_A/0", net="RAIL_A/0", enabled=False)],
                  components=[Component("C1", "M1", ["GND", "U3_RAIL_B"], is_cap=True),
                              Component("C2", "M1", ["GND", "U3_RAIL_B"], is_cap=True)])
    rows = suggest_mappings(spd, None)
    assert len(rows) == 2
    assert not rows[0].selected
    assert rows[1].net == "U3_RAIL_B"
    assert rows[1].site == "SITE3"
    assert rows[1].channel == "RAIL_B"
    assert rows[1].port_index is None
    validate_mappings(rows)


@pytest.mark.parametrize("rows", [[], [ChannelMapping(channel="A", site="")],
    [ChannelMapping(channel="A", site="0", confirmed=False)],
    [ChannelMapping(channel="A", site="0", port_index=0)],
    [ChannelMapping(channel="A", site="0", port_index=True)],
    [ChannelMapping(channel="A", site="0"), ChannelMapping(channel="a", site="0")],
    [ChannelMapping(channel="A", site="0", port_index=1), ChannelMapping(channel="B", site="0", port_index=1)]])
def test_rejects_invalid_selected_rows(rows):
    with pytest.raises(ValueError):
        validate_mappings(rows)


def test_checks_index_against_network():
    with pytest.raises(ValueError, match="범위"):
        validate_mappings([ChannelMapping(channel="A", site="0", port_index=2)], touchstone(Port(1, "A/0")))
    validate_mappings([ChannelMapping(channel="A", site="0")], touchstone(Port(1, "A/0")))


def test_normalizes_site_prefix_suffix_and_instance_without_changing_raw_names():
    ts = touchstone(Port(1, "SITE03-RAIL_A"), Port(2, "RAIL_B/03"),
                    Port(3, "RAIL_C", instance="U03"))
    rows = suggest_mappings(None, ts)
    assert [row.site for row in rows] == ["SITE3", "SITE3", "SITE3"]
    assert [row.channel for row in rows] == ["RAIL_A", "RAIL_B", "RAIL_C"]
    assert rows[0].source_name == "SITE03-RAIL_A"


def test_accepts_dcr_only_without_port_mappings():
    validate_mappings(ReportConfig(dcr=[DcrRow("SITE0", "RAIL_A", 1.0)]))


def test_priorities_checked_across_selected_sites_only_when_enabled():
    config = ReportConfig(mappings=[ChannelMapping(channel="A", site="S1", priority="1"),
                                    ChannelMapping(channel="A", site="S2", priority="Auxiliary")])
    validate_mappings(config)
    config.settings.use_priority = True
    with pytest.raises(ValueError, match="사이트마다"):
        validate_mappings(config)
    for a, b in [("1", "01"), ("Auxiliary", "Auxiliary"), ("", "")]:
        config.mappings[0].priority, config.mappings[1].priority = a, b
        validate_mappings(config)
    config.mappings[1].selected = False
    config.mappings[1].priority = "0"
    validate_mappings(config)
    for invalid in ("0", "-1", "1.5"):
        config.mappings[0].priority = invalid
        with pytest.raises(ValueError, match="정수"):
            validate_mappings(config)


def test_checks_selected_spd_port_even_after_manual_confirmation():
    spd = SpdData("synthetic.spd", {}, ports=[Port(1, "P1", net="RAIL_A")])
    row = ChannelMapping(spd_port="TYPO", net="RAIL_A", site="SITE0", channel="RAIL_A", confirmed=True)
    with pytest.raises(ValueError, match="SPD 포트"):
        validate_mappings([row], spd=spd)
    row.spd_port = "P1"
    validate_mappings([row], spd=spd)
    row.spd_port = ""
    validate_mappings([row], spd=spd)  # Manually mapped CAP nets need no port.
    row.spd_port = "TYPO"
    row.selected = False
    validate_mappings(ReportConfig(mappings=[row], dcr=[DcrRow("SITE0", "RAIL_A", 1.0)]), spd=spd)


def test_numbered_endpoint_family_and_raw_spd_net_survive_mapping():
    spd = SpdData("synthetic", {}, ports=[
        Port(21, "Port21_LGA1::0_RAIL_A", net="0_RAIL_A", instance="LGA1", site="SITE0", channel="RAIL_A"),
        Port(22, "Port22_LGA1::1_RAIL_A", net="1_RAIL_A", instance="LGA1", site="SITE1", channel="RAIL_A"),
        Port(23, "Port23_C1::0_RAIL_A", net="0_RAIL_A", instance="C1", site="SITE0", channel="RAIL_A"),
        Port(24, "Port24_LGA1::0_RAIL_A", net="0_RAIL_A", instance="LGA1", site="SITE0", channel="RAIL_A"),
    ])
    ts = touchstone(Port(1, "LGA1-RAIL_A/0", "Port21_LGA1::0_RAIL_A"),
                    Port(2, "LGA1-RAIL_A/1", "Port22_LGA1::1_RAIL_A"))
    for source in (None, spd):
        rows = suggest_mappings(source, ts)
        assert [(r.channel, r.site, r.net) for r in rows] == [
            ("LGA1-RAIL_A", "SITE0", "0_RAIL_A"), ("LGA1-RAIL_A", "SITE1", "1_RAIL_A")]
        assert all(r.confirmed and r.selected for r in rows)
        assert rows[0].source_name == "LGA1-RAIL_A/0 | Port21_LGA1::0_RAIL_A"
        if source:
            assert [r.spd_port for r in rows] == [p.name for p in spd.ports[:2]]
        validate_mappings(rows, ts, source)


def test_numbered_family_is_not_site_and_description_can_supply_missing_site():
    ts = touchstone(Port(1, "LGA1-RAIL_A", "Port21_LGA1::0_RAIL_A"),
                    Port(2, "LGA2-RAIL_B"), Port(3, "070_RAIL_C"))
    rows = suggest_mappings(None, ts)
    assert (rows[0].channel, rows[0].site, rows[0].net, rows[0].confirmed) == (
        "LGA1-RAIL_A", "SITE0", "0_RAIL_A", True)
    assert (rows[1].channel, rows[1].site, rows[1].confirmed) == ("LGA2-RAIL_B", "", False)
    assert (rows[2].channel, rows[2].site, rows[2].confirmed) == ("070_RAIL_C", "", False)
    fallback = suggest_mappings(None, touchstone(Port(1, "Port21_LGA1::0_RAIL_A")))[0]
    assert (fallback.channel, fallback.site, fallback.net, fallback.confirmed) == (
        "LGA1-RAIL_A", "SITE0", "0_RAIL_A", True)


@pytest.mark.parametrize("label,description", [
    ("LGA1-RAIL_A/1", "Port21_LGA1::0_RAIL_A"),
    ("LGA1-RAIL_B/0", "Port21_LGA1::0_RAIL_A"),
    ("LGA1-RAIL_A/0", "Port21_LGA2::0_RAIL_A"),
])
def test_conflicting_endpoint_label_and_description_need_confirmation(label, description):
    row = suggest_mappings(None, touchstone(Port(1, label, description)))[0]
    assert not row.confirmed


def test_duplicate_numbered_family_endpoints_still_require_confirmation():
    ts = touchstone(Port(1, "LGA1-RAIL_A/0", "Port21_LGA1::0_RAIL_A"),
                    Port(2, "LGA1-RAIL_A/0", "Port22_LGA1::0_RAIL_A"))
    assert all(not r.confirmed for r in suggest_mappings(None, ts))


def test_spd_only_does_not_add_cap_fallback_for_explicit_port_net():
    spd = SpdData("synthetic", {}, ports=[
        Port(21, "Port21_LGA1::0_RAIL_A", net="0_RAIL_A", instance="LGA1", site="SITE0", channel="RAIL_A"),
        Port(22, "Port22_LGA1::1_RAIL_A", net="1_RAIL_A", instance="LGA1", site="SITE1", channel="RAIL_A", enabled=False),
    ], components=[Component("C1", "CAP", ["GND", "0_RAIL_A"], is_cap=True),
                   Component("C2", "CAP", ["GND", "1_RAIL_A"], is_cap=True)])
    rows = suggest_mappings(spd, None)
    assert len(rows) == 2
    assert [(r.channel, r.selected, r.confirmed) for r in rows] == [
        ("LGA1-RAIL_A", True, True), ("LGA1-RAIL_A", False, True)]
    validate_mappings(rows, spd=spd)


def test_specific_spd_description_does_not_match_another_unique_port():
    spd = SpdData("synthetic", {}, ports=[
        Port(21, "Port21_LGA1::0_RAIL_A", net="0_RAIL_A", instance="LGA1", site="SITE0", channel="RAIL_A")])
    row = suggest_mappings(spd, touchstone(Port(1, "LGA1-RAIL_A/0", "Port99_LGA1::0_RAIL_A")))[0]
    assert not row.confirmed
    assert row.spd_port == ""


def test_spd_positive_terminal_overrides_fallback_name_and_exact_ts_descriptor():
    spd = SpdData("synthetic", {}, ports=[
        Port(21, "Port21_LGA1::fallback", net="0_RAIL_A", instance="LGA1", site="SITE0", channel="RAIL_A")])
    row = suggest_mappings(spd, None)[0]
    assert (row.net, row.site, row.channel, row.confirmed) == ("0_RAIL_A", "SITE0", "LGA1-RAIL_A", True)
    ts = touchstone(Port(1, "LGA1-RAIL_A/0", "Port21_LGA1::fallback"))
    row = suggest_mappings(spd, ts)[0]
    assert (row.net, row.site, row.channel, row.confirmed) == ("0_RAIL_A", "SITE0", "LGA1-RAIL_A", True)
    assert row.spd_port == spd.ports[0].name
    ts.ports[0].description = "Port21_LGA1::0_RAIL_A"
    assert not suggest_mappings(spd, ts)[0].confirmed
