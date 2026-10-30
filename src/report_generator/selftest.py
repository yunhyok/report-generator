"""Synthetic frozen-build smoke test; never reads user data."""
from __future__ import annotations

import json
import traceback
from pathlib import Path


def run(destination: str) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    import os
    from openpyxl import load_workbook
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
                config = ReportConfig(mappings=mappings, dcr=[DcrRow("SITE0", "POWER", 2)])
                window.spd, window.ts, window.config = spd, ts, config
                window.fill_mappings()
                window.fill_caps()
                window.fill_dcr()
                window.tabs.setCurrentIndex(1)
                app.processEvents()
                window.grab().save(str(folder / "ui.png"))
                state["stage"] = "preview"
                runner.start("preview", {"config": config, "spd": spd, "ts": ts, "folder": str(folder)})
            else:
                report, images = result
                assert len(report.sheets) == 6 and len(images) == 2
                book = load_workbook(report.path, read_only=True, data_only=True)
                assert book["PI ALL@100MHz"]["C5"].value == 1000
                assert book["DCR"]["C5"].value == 2
                book.close()
                assert (Path(__file__).parent / "docs" / "help.html").is_file()
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
