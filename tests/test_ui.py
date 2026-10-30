import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt

from report_generator.core.models import ChannelMapping, Component, DcrRow, SpiceModel, SpdData
from report_generator.ui.window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Discard)
    widget = MainWindow()
    yield widget
    widget.close()


def test_edit_mapping_and_dcr_snapshot(window):
    window.config.mappings = [ChannelMapping(1, "raw", net="VDD/0", site="SITE0", channel="VDD", confirmed=False)]
    window.fill_mappings()
    window.mapping_table.item(0, 9).setText("Power")
    window.config.dcr = [DcrRow("SITE0", "VDD", 0)]
    window.fill_dcr()
    config = window.collect_config()
    assert config.mappings[0].group == "Power"
    assert config.mappings[0].confirmed
    assert config.dcr[0].resistance_mohm == 0


def test_model_and_instance_overrides(window):
    window.spd = SpdData("synthetic.spd", {}, models={"CAP_100N": SpiceModel("CAP_100N", 1e-7, 1e-7, 1e-7, True)}, components=[Component("C1", "CAP_100N", ["VDD", "GND"], is_cap=True)])
    window.fill_caps()
    window.model_table.item(0, 3).setText("1 uF")
    assert window.config.model_overrides["CAP_100N"] == 1e-6
    window.component_table.item(0, 5).setText("2 uF")
    assert window.config.component_overrides["C1"] == 2e-6
    window.component_table.item(0, 5).setText("")
    assert not window.config.component_overrides


def test_filter_bulk_selection_preserves_hidden(window):
    window.config.mappings = [ChannelMapping(site="SITE0", channel="VDD"), ChannelMapping(site="SITE1", channel="VCC")]
    window.fill_mappings()
    window.map_filter.setText("VDD")
    window.bulk_map(0, False)
    assert window.mapping_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert window.mapping_table.item(1, 0).checkState() == Qt.CheckState.Checked


def test_new_sources_clear_old_dcr(window):
    window.spd = SpdData("old.spd", {"size": 1, "mtime_ns": 1})
    window.config.dcr = [DcrRow("SITE0", "OLD", 42)]
    window.action = "load"
    window.on_finished((SpdData("new.spd", {"size": 2, "mtime_ns": 2}), None, [ChannelMapping(site="SITE0", channel="NEW")], []))
    assert not window.config.dcr


def test_changed_path_requires_reload_before_export(window, monkeypatch):
    errors = []
    monkeypatch.setattr(window, "error", lambda value: errors.append(str(value)))
    window.spd_path.setText("new-board.spd")
    window.start_report("preview")
    assert errors and "파일 읽기" in errors[0]
    assert not window.runner.busy


def test_large_progress_and_process_start_recovery(app, monkeypatch):
    from report_generator.jobs import JobRunner
    from multiprocessing.context import SpawnProcess
    runner = JobRunner()
    values = []
    runner.progress.connect(lambda text, current, total: values.append((current, total)))
    runner.progress.emit("test", 2**31, 3_000_000_000)
    assert values == [(2**31, 3_000_000_000)]
    def fail(*args):
        raise OSError("synthetic start failure")
    monkeypatch.setattr(SpawnProcess, "start", fail)
    with pytest.raises(OSError):
        runner.start("load", {})
    assert not runner.busy
