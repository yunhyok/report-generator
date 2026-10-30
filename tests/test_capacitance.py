from report_generator.core.capacitance import (
    component_active, format_capacitance, parse_capacitance,
    resolved_capacitance, summarize_caps,
)
from report_generator.core.models import ChannelMapping, Component, ReportConfig, SpiceModel, SpdData
import pytest


@pytest.mark.parametrize("text,value", [
    ("CAP_0805M_220NF", 220e-9), ("CAP_2012-2U_2U", 2e-6),
    ("C = 2.20e-01[uF]", 220e-9), ("0805M/X7R/2.2uF/25V", 2.2e-6),
    ("2.2 µF", 2.2e-6), ("100P", 100e-12), ("1e-6", 1e-6),
    ("0805M", None), ("NULL", None), ("CAP_2012", None), ("-1uF", None),
])
def test_parse(text, value):
    actual = parse_capacitance(text)
    assert actual is None if value is None else actual == pytest.approx(value)


def test_count_activation_overrides_aliases_and_deduplication():
    spd = SpdData("synthetic", {})
    spd.models = {"cap": SpiceModel("cap", capacitance_f=1e-6, valid=True), "empty": SpiceModel("empty")}
    spd.components = [
        Component("C1", "cap", ["P", "GND"], is_cap=True),
        Component("C2", "cap", ["P_ALIAS", "GND"], status="Disabled", is_cap=True),
        Component("C3", "empty", ["P_ALIAS", "GND"], status="Enabled", is_cap=True),
        Component("C4", "empty", ["P", "GND"], is_cap=True),
        Component("C5", "cap", ["P", "GND"], status="Unknown", is_cap=True),
        Component("C6", "cap", ["OTHER", "GND"], is_cap=True),
    ]
    spd.aliases = {"P": ["P_ALIAS"], "P_ALIAS": ["P"]}
    config = ReportConfig(mappings=[ChannelMapping(net="P", site="S", channel="V"), ChannelMapping(net="P_ALIAS", site="S", channel="V")])
    rows = summarize_caps(spd, config)
    assert [(row["capacitance_f"], row["count"]) for row in rows] == [(1e-6, 1), (None, 1)]
    assert rows[0]["refdes"] == ["C1"]
    assert not component_active(spd.components[3], spd)
    config.model_overrides["cap"] = 2e-6
    config.component_overrides["C1"] = 3e-6
    assert resolved_capacitance(spd.components[0], spd, config) == 3e-6
    config.component_overrides["C1"] = float("nan")
    with pytest.raises(ValueError):
        resolved_capacitance(spd.components[0], spd, config)


def test_format():
    assert format_capacitance(100e-9) == "100nF"
    assert format_capacitance(4.7e-6) == "4.7uF"
    assert format_capacitance(None) == "Unknown"
