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
