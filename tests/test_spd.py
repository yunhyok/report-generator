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


def test_referenced_surface_pads_rotation_offsets_and_pin_bounds(tmp_path):
    geometry = """  Node1!!1::U2_POWER X = 10mm Y = 20mm Layer = Signal$TOP PadStack = RECT AbsoluteRotation = 90
Node2!!2::GROUND_A X = 12mm Y = 20mm Layer = Signal$TOP PadStack = ROUND
Node3!!1::U2_POWER X = 5mm Y = 7mm Layer = Signal$BOTTOM PadStack = POLY
Node4!!2::GROUND_A X = 100mm Y = 100mm Layer = Signal$INNER PadStack = ROUND
Node999!!9::UNUSED X = 1000mm Y = 1000mm Layer = Signal$TOP PadStack = ROUND
.PadStackDef RECT
.PadDef Signal$TOP
Regular Box 2mm 1mm offSetX = 1mm offSetY = 0mm
Anti Box 200mm 200mm
.EndPadDef
.PadDef Signal$BOTTOM
Regular Square 1mm
.EndPadDef
.EndPadStackDef
.PadStackDef ROUND
.PadDef DefaultLibLayer
Regular Circle 0.25mm
Anti Polygon -10mm -10mm 10mm -10mm
+ 0mm 10mm
.EndPadDef
.EndPadStackDef
.PadStackDef POLY
.PadDef Signal$BOTTOM
Regular Polygon -1mm -1mm 1mm -1mm
+ 0mm 1mm
.EndPadDef
.EndPadStackDef
"""
    path = tmp_path / "synthetic.spd"
    path.write_text(geometry + SAMPLE, encoding="utf-8")
    data = read_spd(path)
    c1, c2 = data.components
    assert len(c1.pads) == 3
    top = next(p for p in c1.pads if p.node.startswith("Node1") and p.side == "Top")
    assert (top.x_mm, top.y_mm, top.rotation) == (10, 21, 90)
    assert top.shape.width_mm == 2 and top.shape.height_mm == 1
    circle = next(p for p in c1.pads if p.shape.kind == "Circle")
    assert circle.shape.width_mm == .5
    assert c1.pin_bounds == pytest.approx((9.5, 19.5, 12.25, 22))
    assert len(c2.pads) == 1 and c2.pads[0].side == "Bottom"
    assert len(c2.pads[0].shape.vertices) == 3
    assert c2.pin_bounds == (4, 6, 6, 8)
    assert c2.x_mm is None


def test_unknown_pad_shape_is_skipped_and_geometry_pass_can_cancel(tmp_path):
    path = tmp_path / "synthetic.spd"
    path.write_text("""Node1!!1::U2_POWER X = 10mm Y = 20mm Layer = Signal$TOP PadStack = CUSTOM
.PadStackDef CUSTOM
.PadDef Signal$TOP
Regular VendorShape proprietary=token
.EndPadDef
.EndPadStackDef
""" + SAMPLE, encoding="utf-8")
    data = read_spd(path)
    assert not data.components[0].pads
    assert data.components[0].pin_bounds == (10, 20, 10, 20)
    assert any("지원하지 않는 형상" in warning for warning in data.warnings)
    cancelled = False
    def progress(stage, *args):
        nonlocal cancelled
        cancelled = stage == "SPD 패드 분석"
    with pytest.raises(CancelledError):
        read_spd(path, progress=progress, cancel=lambda: cancelled)
