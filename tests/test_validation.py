import json
import os
from pathlib import Path
import subprocess
import sys

from openpyxl import load_workbook
import pytest

from report_generator.core.models import ChannelMapping, DcrRow, ReportConfig, source_signature
from report_generator.core.project import save_project
from report_generator.validation import inspect_workbook, validate_project


def project(tmp_path):
    spd = tmp_path / "board.spd"
    spd.write_text(""".PartialCkt CAP_100N ExtNode = 1 2
* Property : C = 0.1[uF]
C1 1 2 100n
.EndPartialCkt
.Connect C1 CAP_100N
1 $Package.Node1::SITE0-POWER
2 $Package.Node2::GND
.EndC
.Part CAP_100N Outline = 1mm 0.5mm
.Component C1 1mm 2mm StartLayer = Signal$TOP AttachLayer = TopAir
.Port
Port1_LOAD::SITE0-POWER GenFromCktInstance="LOAD"
+ PositiveTerminal $Package.Node1::SITE0-POWER
+ NegativeTerminal $Package.Node2::GND
.EndPort
""", encoding="utf-8")
    ts = tmp_path / "network.s1p"
    ts.write_text("! Port[1] = SITE0-POWER\n# Hz S RI R 1\n" + "".join(f"{f:g} 0 0\n" for f in (1e3, 1e5, 1e6, 1e7, 1e8, 1e9)))
    config = ReportConfig(mappings=[ChannelMapping(1, spd_port="Port1_LOAD::SITE0-POWER", net="SITE0-POWER", site="SITE0", channel="POWER", group="POWER")], dcr=[DcrRow("SITE0", "POWER", 2.5)])
    path = tmp_path / "project.report.json"
    sources = {"spd": str(spd), "touchstone": str(ts)}
    save_project(path, config, sources, {key: source_signature(value) for key, value in sources.items()})
    return path, spd, ts


def test_shared_pipeline_export_and_reference_comparison(tmp_path):
    path, _, _ = project(tmp_path)
    reference = tmp_path / "reference.xlsx"
    audit = validate_project(path, reference)
    assert audit["status"] == "passed"
    assert {key: value["matches"] for key, value in audit["production_checks"].items()} == {"PI": 5, "PI100": 1, "PI_DETAIL": 5, "CAP": 1, "DCR": 1}
    assert all(not check["missing"] and not check["extra"] for check in audit["production_checks"].values())
    assert sum(sheet["images"] for sheet in audit["layout"]) == 2
    assert all(sheet["fonts"] and sheet["fills"] for sheet in audit["layout"])
    assert any(sheet["bold_cells"] for sheet in audit["layout"])
    values, _ = inspect_workbook(reference)
    item = next(iter(values["PI"].values()))
    book = load_workbook(reference)
    book[item["sheet"]][item["cell"]] = 123
    book.save(reference)
    book.close()
    output = tmp_path / "checked.xlsx"
    audit = validate_project(path, output, reference)
    assert audit["status"] == "passed"  # Reference differences do not make a production failure.
    assert audit["reference_status"] == "differences"
    assert audit["reference_comparison"]["PI"]["differences"] == 1
    assert audit["reference_comparison"]["CAP"]["matches"] == 1
    assert audit["reference_comparison"]["DCR"]["matches"] == 1
    sidecar = json.loads(output.with_suffix(".validation.json").read_text(encoding="utf-8"))
    assert sidecar["status"] == "passed"
    assert "참조와의 차이만으로" in output.with_suffix(".validation.html").read_text(encoding="utf-8")


def test_changed_source_fails_without_replacing_output_and_leaves_diagnostic(tmp_path):
    path, spd, _ = project(tmp_path)
    spd.write_text(spd.read_text() + "\n* changed\n")
    output = tmp_path / "report.xlsx"
    output.write_bytes(b"existing report")
    with pytest.raises(ValueError, match="원본"):
        validate_project(path, output)
    assert output.read_bytes() == b"existing report"
    diagnostic = json.loads(output.with_suffix(".validation.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "error" and "spd" in diagnostic["error"]


def test_malformed_project_can_leave_new_diagnostics_without_overwriting_existing_files(tmp_path):
    path = tmp_path / "invalid.report.json"
    path.write_text("not JSON")
    output = tmp_path / "report.xlsx"
    with pytest.raises(ValueError):
        validate_project(path, output)
    diagnostic = output.with_suffix(".validation.json")
    assert json.loads(diagnostic.read_text(encoding="utf-8"))["status"] == "error"
    diagnostic.write_bytes(b"existing input")
    with pytest.raises(ValueError):
        validate_project(path, output)
    assert diagnostic.read_bytes() == b"existing input"


def test_never_overwrites_source_reference_project_or_dcr_input(tmp_path):
    path, spd, _ = project(tmp_path)
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"input must survive")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sources"]["spd"] = str(source)
    raw["signatures"]["spd"] = source_signature(source)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="덮어쓸"):
        validate_project(path, source)
    assert source.read_bytes() == b"input must survive"
    raw["sources"]["spd"] = str(spd)
    raw["signatures"]["spd"] = source_signature(spd)
    raw["config"]["dcr_source_path"] = str(source)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="덮어쓸"):
        validate_project(path, source)
    raw["config"]["dcr_source_path"] = ""
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="덮어쓸"):
        validate_project(path, source, source)
    # An adjacent JSON diagnostic must not replace the project either.
    collision = tmp_path / "report.validation.json"
    collision.write_bytes(path.read_bytes())
    original = collision.read_bytes()
    with pytest.raises(ValueError, match="덮어쓸"):
        validate_project(collision, tmp_path / "report.xlsx")
    assert collision.read_bytes() == original


def test_script_is_headless_and_dcr_only_works(tmp_path):
    path = tmp_path / "dcr.report.json"
    save_project(path, ReportConfig(dcr=[DcrRow("SITE0", "POWER", 0)]), {})
    output = tmp_path / "dcr.xlsx"
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "deliberately-unavailable-platform"
    completed = subprocess.run([sys.executable, "scripts/validate_reports.py", "--validate-project", str(path), "--output", str(output)], cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    audit = json.loads(output.with_suffix(".validation.json").read_text(encoding="utf-8"))
    assert audit["production_checks"]["DCR"]["matches"] == 1
    assert audit["sheets"] == ["DCR"]


def test_renamed_sites_are_compared_without_numeric_site_assumptions(tmp_path):
    path = tmp_path / "dcr.report.json"
    save_project(path, ReportConfig(dcr=[DcrRow("DUT_A", "POWER", 12)]), {})
    audit = validate_project(path, tmp_path / "dcr.xlsx")
    assert audit["status"] == "passed"
    assert audit["production_checks"]["DCR"]["matches"] == 1


def test_cancel_preserves_existing_report(tmp_path):
    path, _, _ = project(tmp_path)
    output = tmp_path / "report.xlsx"
    output.write_bytes(b"previous")
    from report_generator.core.models import CancelledError
    with pytest.raises(CancelledError):
        validate_project(path, output, cancel=lambda: True)
    assert output.read_bytes() == b"previous"
    assert json.loads(output.with_suffix(".validation.json").read_text(encoding="utf-8"))["status"] == "cancelled"


def test_all_merged_cap_capacity_blocks_are_checked(tmp_path):
    path, spd, _ = project(tmp_path)
    spd.write_text(spd.read_text() + """\n.PartialCkt CAP_1U ExtNode = 1 2
* Property : C = 1[uF]
C1 1 2 1u
.EndPartialCkt
.Connect C2 CAP_1U
1 $Package.Node3::SITE0-POWER
2 $Package.Node4::GND
.EndC
""")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["signatures"]["spd"] = source_signature(spd)
    path.write_text(json.dumps(raw), encoding="utf-8")
    audit = validate_project(path, tmp_path / "caps.xlsx")
    assert audit["status"] == "passed"
    assert audit["production_checks"]["CAP"]["matches"] == 2
    values, _ = inspect_workbook(tmp_path / "caps.xlsx")
    assert {key[2] for key in values["CAP"]} == {"1e-07F", "1e-06F"}


def test_six_channel_nine_site_pi_checks_every_row_band(tmp_path):
    from report_generator.core.report import FREQUENCIES
    ts = tmp_path / "wide.s54p"
    rows = ["# Hz S RI R 1"]
    mappings = []
    for site_index in range(9):
        for channel_index in range(6):
            index = len(mappings) + 1
            rows.insert(-1, f"! Port[{index}] = SITE{site_index}-POWER{channel_index}")
            mappings.append(ChannelMapping(index, site=f"SITE{site_index}", channel=f"POWER{channel_index}"))
    rows.extend(f"{frequency:g} " + "0 0 " * (54 * 54) for frequency in FREQUENCIES)
    ts.write_text("\n".join(rows))
    path = tmp_path / "wide.report.json"
    save_project(path, ReportConfig(mappings=mappings), {"touchstone": str(ts)}, {"touchstone": source_signature(ts)})
    audit = validate_project(path, tmp_path / "wide.xlsx")
    assert audit["status"] == "passed"
    assert audit["production_checks"]["PI"]["matches"] == 270
    assert audit["production_checks"]["PI100"]["matches"] == 54


def test_wrapper_help_never_starts_qt(tmp_path):
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "deliberately-unavailable-platform"
    completed = subprocess.run([sys.executable, "scripts/validate_reports.py", "--help"], cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0
    assert "--validate-project" in completed.stdout
