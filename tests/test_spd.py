import pytest

from report_generator.core.models import CancelledError
from report_generator.core.spd import infer_site_channel, read_spd


SAMPLE = """Title Synthetic
.Package $Package
.EndPackage
.PartialCkt CAP_0805M_220N ExtNode = 1 2
* Description :0805M/X7R/2.2uF/25V
* Property : C = 2.2[uF]
C1 1 2 1.8u
.EndPartialCkt
.PartialCkt CAP_NULL ExtNode = 1 2
.EndPartialCkt
.Connect C1 CAP_0805M_220N
1 $Package.Node1!!1::U2_POWER
2 $Package.Node2!!2::GROUND_A
.EndC
.Connect C2 CAP_NULL Usage = 0b1000
1 $Package.Node3!!1::U2_POWER
2 $Package.Node4!!2::GROUND_A
.EndC
.Part CAP_0805M_220N Outline = 1mm 0.5mm
.Component C1 2mm -3mm Rotation = 90 StartLayer = Signal$BOTTOM AttachLayer = BottomAir
.Component C2 "" "" StartLayer = Signal$TOP AttachLayer = TopAir
.Port
Port9_C1::fallback Auto GenFromCktInstance="C1"
+ PositiveTerminal $Package.Node1!!1::U2_POWER
+ NegativeTerminal $Package.Node2!!2::GROUND_A
.EndPort
.NetAlias
U2_POWER POWER_ALT
+ POWER_OTHER
.EndNetAlias
.NetList
GROUND_A -> GroundNets
.EndNetList
"""


def test_assignments_geometry_models_ports_and_aliases(tmp_path):
    path = tmp_path / "synthetic.spd"
    path.write_text(SAMPLE, encoding="utf-8")
    data = read_spd(path)
    model = data.models["CAP_0805M_220N"]
    assert model.valid and model.conflict
    assert model.comment_capacitance_f == pytest.approx(2.2e-6)
    assert model.name_capacitance_f == pytest.approx(220e-9)
    assert not data.models["CAP_NULL"].valid
    c = data.components[0]
    assert (c.refdes, c.model_name, c.nets, c.status) == ("C1", "CAP_0805M_220N", ["U2_POWER", "GROUND_A"], "Automatic")
    assert (c.x_mm, c.y_mm, c.rotation, c.side, c.width_mm, c.height_mm) == (2, -3, 90, "Bottom", 1, .5)
    assert data.components[1].status == "Enabled"
    assert data.components[1].x_mm is None
    assert data.aliases["POWER_ALT"] == ["U2_POWER", "POWER_OTHER"]
    assert "GROUND_A" in data.ground_nets
    assert (data.ports[0].index, data.ports[0].net, data.ports[0].site, data.ports[0].channel) == (9, "U2_POWER", "SITE2", "POWER")


def test_cancel_source_changes_and_incomplete_input(tmp_path):
    path = tmp_path / "synthetic.spd"
    path.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(CancelledError):
        read_spd(path, cancel=lambda: True)
    changed = False
    def progress(*args):
        nonlocal changed
        if not changed:
            changed = True
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n* source edit\n")
    with pytest.raises(ValueError, match="changed"):
        read_spd(path, progress=progress)
    path.write_text(".Connect C1 CAP\n1 $Package.Node1::P\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unterminated"):
        read_spd(path)


def test_no_identifier_replacement_and_site_patterns(tmp_path):
    path = tmp_path / "synthetic.spd"
    path.write_bytes(SAMPLE.replace("POWER_ALT", "전원").encode("cp949"))
    assert "전원" in read_spd(path).aliases
    path.write_bytes(b".Connect C\xff\xff CAP\n.EndC\n")
    with pytest.raises(ValueError, match="decoded"):
        read_spd(path)
    assert infer_site_channel("SITE12-POWER", "U1_0") == ("SITE12", "POWER")
    assert infer_site_channel("POWER/3", "LGA") == ("SITE3", "POWER")


def test_invalid_model_scalar_is_not_automatic_active(tmp_path):
    path = tmp_path / "synthetic.spd"
    path.write_text(SAMPLE.replace("C1 1 2 1.8u", "C1 1 2 nan"), encoding="utf-8")
    assert not read_spd(path).models["CAP_0805M_220N"].valid
