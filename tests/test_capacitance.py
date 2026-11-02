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
    ("CAP_1005_4_7U_4_7U", 4.7e-6), ("CAP_1005_2_2NF_2_2NF", 2.2e-9),
    ("CAP_2012_2U_2U", 2e-6),
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
    assert [(row["capacitance_f"], row["count"]) for row in rows] == [(1e-6, 1), (None, 2)]
    assert rows[0]["refdes"] == ["C1"]
    assert component_active(spd.components[3], spd)
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


@pytest.mark.parametrize("model_name", ["NULL", "INVALID", "UNASSIGNED"])
@pytest.mark.parametrize("status,expected", [("Automatic", True), ("Enabled", True), ("Disabled", False), ("Unknown", False)])
def test_activation_depends_only_on_status(model_name, status, expected):
    spd = SpdData("synthetic", {}, models={"NULL": SpiceModel("NULL"), "INVALID": SpiceModel("INVALID", 2.2e-6, valid=False)})
    component = Component("C2", model_name, status=status, is_cap=True)
    assert component_active(component, spd) is expected


def test_automatic_unknown_capacity_and_disabled_override_preserve_net_mapping():
    spd = SpdData("synthetic", {}, models={"INVALID": SpiceModel("INVALID", 2.2e-6, valid=False)}, components=[
        Component("C2", "NULL", ["POWER_A", "RETURN_A"], is_cap=True),
        Component("C3", "UNASSIGNED", ["POWER_ALIAS", "RETURN_A"], is_cap=True),
        Component("C4", "INVALID", ["POWER_A", "RETURN_A"], is_cap=True),
        Component("C5", "NULL", ["POWER_A", "RETURN_A"], status="Disabled", is_cap=True),
        Component("C6", "INVALID", ["POWER_B", "RETURN_A"], is_cap=True),
        Component("C7", "INVALID", ["POWER_A", "OTHER"], is_cap=True),
        Component("C8", "INVALID", ["POWER_A", "RETURN_A"], status="Unknown", is_cap=True),
    ], aliases={"POWER_A": ["POWER_ALIAS"]})
    config = ReportConfig(mappings=[ChannelMapping(net="POWER_A", site="SITE2", channel="POWER")], component_overrides={"C5": 100e-9, "C8": 100e-9})
    config.settings.ground_nets = ["RETURN_A"]
    rows = summarize_caps(spd, config)
    assert {row["capacitance_f"]: row["count"] for row in rows} == {None: 2, 2.2e-6: 1}
    assert {refdes for row in rows for refdes in row["refdes"]} == {"C2", "C3", "C4"}
