"""Synthetic frozen-build smoke test; never reads user data."""
from __future__ import annotations

import json
import traceback
from pathlib import Path


def run(destination: str) -> int:
    from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QFontDatabase
    import os
    from openpyxl import load_workbook
    from .core.channel_settings import write_channel_template
    from .core.dcr import read_dcr_sheet
    from .core.models import DcrRow, ReportConfig
    from .jobs import JobRunner
    from .ui.window import MainWindow

    folder = Path(destination).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    spd_path = folder / "synthetic.spd"
    ts_path = folder / "synthetic.s1p"
    spd_path.write_text(""".PartialCkt CAP_100N ExtNode = 1 2
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
    ts_path.write_text("! Port[1] = SITE0-POWER\n# Hz S RI R 1\n" + "".join(f"{f:g} 0 0\n" for f in (1e3, 1e5, 1e6, 1e7, 1e8, 1e9)), encoding="ascii")
    app = QApplication.instance() or QApplication([])
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Malgun Gothic", 10))
    window = MainWindow()
    window.resize(1000, 720)
    window.show()
    runner = JobRunner()
    state = {"stage": "load", "ok": False}

    def fail(message):
        state["error"] = str(message)
        if runner.busy:
            runner.cleanup(force=True)
        window.saved_state = window.state_text()
        app.quit()

    def complete(result):
        try:
            if state["stage"] == "load":
                spd, ts, mappings, errors = result
                assert not errors and len(mappings) == 1 and mappings[0].confirmed
                template = folder / "synthetic-channel-settings.xlsx"
                write_channel_template(template, mappings)
                edited = load_workbook(template)
                edited.active["C2"] = 1
                edited.active["D2"] = "Load rails"
                edited.active["E2"] = "VDD_CORE_IO"
                measurements = edited.create_sheet("DCR")
                measurements.append(["Name", "SITE", "NominalCurrent(A)", "NominalVoltage(V)", "ActualVoltage(V)", "DCR_IRDrop[mΩ]"])
                measurements.append(["VDD_CORE_IO", 0, 0.5, 1, 0.999, 2])
                edited.save(template)
                edited.close()
                config = ReportConfig(mappings=mappings)
                window.spd, window.ts, window.config = spd, ts, config
                window.fill_mappings()
                window.fill_caps()
                window.fill_dcr()
                window.tabs.setCurrentIndex(1)
                app.processEvents()
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(template))])
                target = window.mapping_table.viewport()
                for event_type in (QDragEnterEvent, QDropEvent):
                    event = event_type(QPoint(4, 4), Qt.DropAction.CopyAction, mime,
                                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                    QApplication.sendEvent(target, event)
                    assert event.isAccepted()
                config = window.collect_config()
                assert config.settings.use_priority and config.mappings[0].group == "Load rails"
                mapping = config.mappings[0]
                assert (mapping.display_name, mapping.net, mapping.channel) == ("VDD_CORE_IO", "SITE0-POWER", "POWER")
                config.dcr = read_dcr_sheet(template, "DCR", config.mappings)
                assert config.dcr == [DcrRow("SITE0", "POWER", 2)]
                window.fill_dcr()
                window.grab().save(str(folder / "ui.png"))
                state["stage"] = "preview"
                runner.start("preview", {"config": config, "spd": spd, "ts": ts, "folder": str(folder)})
            else:
                report, images = result
                assert len(report.sheets) == 6 and len(images) == 3
                assert [label for label, _ in images[:2]] == ["CAP Top", "CAP Bottom"]
                book = load_workbook(report.path, data_only=True)
                pi = book["PI ALL@100MHz"]
                detail = book["Load rails"]
                assert detail["A1"].value is None
                assert len(detail._images) == 1
                assert (detail._images[0].anchor._from.col, detail._images[0].anchor._from.row) == (1, 1)
                assert [detail.cell(4, column).value for column in (16, 17, 18)] == ["Priority", "Channel / [mΩ]", "SITE"]
                assert [(image.anchor._from.col, image.anchor._from.row)
                        for image in book["CAP_배치도면"]._images] == [(1, 3), (15, 3)]
                assert any(label == "Load rails" for label, _ in images)
                priority_column = next(cell.column for cell in pi[4] if cell.value == "Priority")
                assert pi.cell(5, priority_column).value == 1
                assert pi["C5"].value == "VDD_CORE_IO" and pi["C5"].comment is None
                assert pi["A2"].value == "* 1kHz ~ 1GHz : Automatic Frequency Sweep"
                maximum_column = next(cell.column for cell in pi[4] if cell.value == "MAX.")
                assert pi.cell(5, maximum_column).value == 1000
                dcr = book["DCR"]
                site_column = next(cell.column for cell in dcr[4] if cell.value == "SITE0")
                assert dcr.cell(5, site_column).value == 2
                assert all(sheet.freeze_panes is None for sheet in book)
                book.close()
                window.tabs.setCurrentIndex(3)
                window.preview_book = report.path
                window.preview_selector.addItem("표 · DCR", ("table", "DCR"))
                assert "VDD_CORE_IO" in window.preview_table.toPlainText()
                window.grab().save(str(folder / "dcr-preview.png"))
                window.open_help()
                assert window.help_window.isVisible()
                assert "채널 순위 설정" in window.help_window.browser.toPlainText()
                window.help_window.close()
                assert not window.windowIcon().isNull()
                state["ok"] = True
                state["sheets"] = report.sheets
                state["images"] = len(images)
                window.saved_state = window.state_text()
                app.quit()
        except Exception:
            fail(traceback.format_exc())

    runner.finished.connect(complete)
    runner.failed.connect(fail)
    QTimer.singleShot(0, lambda: runner.start("load", {"spd": str(spd_path), "touchstone": str(ts_path)}))
    QTimer.singleShot(120_000, lambda: fail("self-test timeout"))
    app.exec()
    window.saved_state = window.state_text()
    window.close()
    (folder / "selftest-result.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if state["ok"] else 1
