import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from PySide6.QtCore import Qt

from report_generator.core.models import ChannelMapping, Component, DcrRow, Port, ReportConfig, SpiceModel, SpdData
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
    assert not config.mappings[0].confirmed
    assert config.dcr[0].resistance_mohm == 0


def test_model_and_instance_overrides(window):
    window.spd = SpdData("synthetic.spd", {}, models={"CAP_100N": SpiceModel("CAP_100N", 1e-7, 1e-7, 1e-7, True)}, components=[Component("C1", "CAP_100N", ["VDD", "GND"], is_cap=True)])
    window.fill_caps()
    window.model_table.item(0, 3).setText("1 uF")
    assert window.config.model_overrides["CAP_100N"] == 1e-6
    window.component_table.item(0, 5).setText("2 uF")
    assert window.config.component_overrides["C1"] == 2e-6
    assert window.component_table.item(0, 4).text() == "2uF"
    window.component_table.item(0, 5).setText("")
    assert not window.config.component_overrides
    assert window.component_table.item(0, 4).text() == "1uF"


@pytest.mark.parametrize("column", [9, 10])
@pytest.mark.parametrize("confirmed", [False, True])
def test_metadata_edit_preserves_confirmation(window, column, confirmed):
    window.config.mappings = [ChannelMapping(net="RAIL/2", site="SITE2", channel="RAIL", confirmed=confirmed, status="기존 연결 상태")]
    window.fill_mappings()
    window.mapping_table.item(0, column).setText("group or priority")
    assert (window.mapping_table.item(0, 2).checkState() == Qt.CheckState.Checked) == confirmed
    assert window.mapping_table.item(0, 11).text() == "기존 연결 상태"


@pytest.mark.parametrize("column,value", [(6, "OTHER/2"), (7, "SITE3"), (8, "OTHER")])
def test_connection_edit_requires_explicit_confirmation(window, column, value):
    window.config.mappings = [ChannelMapping(net="RAIL/2", site="SITE2", channel="RAIL")]
    window.fill_mappings()
    window.mapping_table.item(0, column).setText(value)
    assert window.mapping_table.item(0, 2).checkState() == Qt.CheckState.Unchecked
    assert "확인" in window.mapping_table.item(0, 11).text()
    window.mapping_table.item(0, 2).setCheckState(Qt.CheckState.Checked)
    assert window.collect_config().mappings[0].confirmed


def test_spd_port_edit_updates_net_but_never_confirms_it(window):
    window.spd = SpdData("synthetic.spd", {}, ports=[Port(7, "Port7_U2::U2_RAIL", net="U2_RAIL")])
    window.config.mappings = [ChannelMapping(net="STALE", site="SITE2", channel="RAIL")]
    window.fill_mappings()
    window.mapping_table.item(0, 5).setText(window.spd.ports[0].name)
    assert window.mapping_table.item(0, 6).text() == "U2_RAIL"
    assert window.mapping_table.item(0, 2).checkState() == Qt.CheckState.Unchecked
    window.mapping_table.item(0, 2).setCheckState(Qt.CheckState.Checked)
    window.mapping_table.item(0, 5).setText("not-an-spd-port")
    assert not window.mapping_table.item(0, 6).text()
    assert window.mapping_table.item(0, 2).checkState() == Qt.CheckState.Unchecked
    assert "찾을 수 없" in window.mapping_table.item(0, 11).text()
    window.mapping_table.selectRow(0)
    window.bulk_map(2, True, selected_only=True)
    assert window.mapping_table.item(0, 2).checkState() == Qt.CheckState.Unchecked
    assert "찾을 수 없" in window.mapping_table.item(0, 11).text()


def seed_preview(window):
    window.preview_book = "old-preview.xlsx"
    window.preview_images = [("old", "old.png")]
    window.preview_selector.blockSignals(True)
    window.preview_selector.addItem("old", ("image", "old.png"))
    window.preview_selector.blockSignals(False)
    window.preview_image.setText("old picture")
    window.preview_table.setText("old table")
    window.page_summary.setText("old pages")


def assert_preview_empty(window):
    assert window.preview_book is None
    assert not window.preview_images
    assert window.preview_selector.count() == 0
    assert not window.preview_image.text()
    assert not window.preview_table.toPlainText()
    assert "미리보기" in window.page_summary.text()


def test_loading_sources_clears_previous_preview(window):
    seed_preview(window)
    window.action = "load"
    window.on_finished((SpdData("new.spd", {}), None, [], []))
    assert_preview_empty(window)


def test_opening_source_free_project_clears_previous_preview(window, monkeypatch):
    seed_preview(window)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: ("synthetic.report.json", ""))
    monkeypatch.setattr("report_generator.ui.window.load_project", lambda path: (ReportConfig(), {}, {}, []))
    window.open_project()
    assert_preview_empty(window)


@pytest.mark.parametrize("accepted", [False, True])
def test_dcr_replacement_ignores_incomplete_old_rows_and_preserves_cancel(window, monkeypatch, accepted):
    window.config.mappings = [ChannelMapping(site="SITE2", channel="RAIL")]
    window.fill_mappings()
    old_rows = [DcrRow("SITE1", "OLD", 4.2)]
    window.config.dcr = old_rows.copy()
    window.fill_dcr()
    window.add_dcr()
    old_table = [[window.dcr_table.item(row, col).text() for col in range(3)] for row in range(2)]
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected)
    assert window.confirm_dcr([2.5]) == accepted
    if accepted:
        assert window.config.dcr == [DcrRow("SITE2", "RAIL", 2.5)]
        assert window.dcr_table.rowCount() == 1
    else:
        assert window.config.dcr == old_rows
        assert [[window.dcr_table.item(row, col).text() for col in range(3)] for row in range(2)] == old_table


def test_cap_edit_keeps_existing_editor_item_and_current_selection(window):
    window.spd = SpdData("synthetic.spd", {}, models={"CAP_220N": SpiceModel("CAP_220N", 220e-9, valid=True)}, components=[Component("C2", "CAP_220N", ["RAIL", "GND"], is_cap=True)])
    window.fill_caps()
    editor = window.component_table.item(0, 5)
    window.component_table.setCurrentItem(editor)
    editor.setText("2.2 uF")
    assert window.component_table.item(0, 5) is editor
    assert window.component_table.currentItem() is editor
    assert window.component_table.item(0, 4).text() == "2.2uF"


def test_cap_model_table_shows_only_models_assigned_to_cap_components(window):
    window.spd = SpdData("synthetic.spd", {}, models={
        "CAP_REVIEW": SpiceModel("CAP_REVIEW", valid=True),
        "CAP_NULL": SpiceModel("CAP_NULL"),
        "DUT_OTHER": SpiceModel("DUT_OTHER"),
        "UNASSIGNED": SpiceModel("UNASSIGNED"),
    }, components=[
        Component("C2", "CAP_REVIEW", status="Disabled", is_cap=True),
        Component("C3", "CAP_NULL", status="Enabled", is_cap=True),
        Component("U2", "DUT_OTHER"),
    ])
    window.config.model_overrides = {"DUT_OTHER": 2.2e-6}
    window.fill_caps()
    assert [window.model_table.item(row, 0).text() for row in range(window.model_table.rowCount())] == ["CAP_REVIEW", "CAP_NULL"]
    assert window.component_table.rowCount() == 2
    assert len(window.spd.models) == 4
    assert window.config.model_overrides == {"DUT_OTHER": 2.2e-6}


@pytest.mark.parametrize("values", [[-1], [float("inf")], [1, 2]])
def test_bad_dcr_replacement_preserves_prior_data(window, monkeypatch, values):
    window.config.mappings = [ChannelMapping(site="SITE2", channel="RAIL")]
    window.fill_mappings()
    window.config.dcr = [DcrRow("SITE1", "OLD", 4.2)]
    window.fill_dcr()
    monkeypatch.setattr(QDialog, "exec", lambda self: pytest.fail("Invalid replacement must not reach confirmation"))
    with pytest.raises(ValueError):
        window.confirm_dcr(values)
    assert window.config.dcr == [DcrRow("SITE1", "OLD", 4.2)]
    assert window.dcr_table.item(0, 2).text() == "4.2"


def test_report_validation_receives_loaded_spd(window, monkeypatch):
    window.spd = SpdData("synthetic.spd", {})
    window.config.mappings = [ChannelMapping(site="SITE2", channel="RAIL", net="U2_RAIL")]
    window.fill_mappings()
    received = []
    monkeypatch.setattr("report_generator.core.mapping.validate_mappings", lambda config, ts, spd: received.append(spd))
    monkeypatch.setattr(window.runner, "start", lambda *args: None)
    window.start_report("preview")
    assert received == [window.spd]


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
