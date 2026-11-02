import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from PySide6.QtCore import Qt

from report_generator.core.models import ChannelMapping, Component, DcrRow, Port, ReportConfig, SpiceModel, SpdData
from report_generator.ui.window import MainWindow, PriorityDialog


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


def test_partial_load_shows_failure_in_status_and_log(window):
    window.action = "load"
    window.on_finished((SpdData("test.spd", {}), None, [], ["touchstone: invalid header"]))
    assert "읽지 못했습니다" in window.status.text()
    assert "touchstone: invalid header" in window.log.toPlainText()


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


def test_optional_priority_propagates_per_channel_without_changing_mappings_or_dcr(window, monkeypatch):
    window.config.mappings = [
        ChannelMapping(site="SITE0", channel="RAIL_A", priority="2", group="Graph", status="기존 상태"),
        ChannelMapping(site="SITE1", channel="RAIL_A", priority="2", confirmed=False),
        ChannelMapping(site="SITE0", channel="RAIL_A", priority="9", selected=False),
        ChannelMapping(site="SITE0", channel="RAIL_B"),
        ChannelMapping(site="SITE0", channel="RAIL_C", priority="7", selected=False),
    ]
    window.config.dcr = [DcrRow("SITE0", "RAIL_A", 3.2)]
    window.config.model_overrides = {"CAP_100N": 1e-7}
    old_dcr = window.config.dcr
    window.fill_mappings()
    window.fill_dcr()
    confirmations = [window.mapping_table.item(row, 2).checkState() for row in range(5)]

    def prepare(dialog):
        assert [dialog.table.item(row, 0).text() for row in range(2)] == ["RAIL_A", "RAIL_B"]
        assert dialog.table.item(0, 1).data(Qt.ItemDataRole.UserRole)
        dialog.enabled.setChecked(True)
        dialog.table.item(0, 1).setText("1")
        dialog.table.item(1, 1).setText("1")
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(PriorityDialog, "exec", prepare)
    window.edit_priorities()
    assert window.config.settings.use_priority
    assert [window.mapping_table.item(row, 10).text() for row in range(5)] == ["1", "1", "1", "1", "7"]
    assert [window.mapping_table.item(row, 2).checkState() for row in range(5)] == confirmations
    assert window.mapping_table.item(0, 11).text() == "기존 상태"
    assert window.mapping_table.item(0, 9).text() == "Graph"
    assert window.config.dcr is old_dcr
    assert window.config.model_overrides == {"CAP_100N": 1e-7}
    assert window.dcr_table.item(0, 2).text() == "3.2"
    assert window.mapping_table.isColumnHidden(10)
    assert not window.mapping_table.item(0, 10).flags() & Qt.ItemFlag.ItemIsEditable


@pytest.mark.parametrize("cancel", [False, True])
def test_priority_skip_or_cancel_preserves_existing_drafts(window, monkeypatch, cancel):
    window.config.settings.use_priority = True
    window.config.mappings = [ChannelMapping(site="SITE0", channel="RAIL", priority="0"),
                              ChannelMapping(site="SITE1", channel="RAIL", priority="2")]
    window.fill_mappings()

    def skip(dialog):
        dialog.enabled.setChecked(True)
        dialog.table.item(0, 1).setText("8")
        if cancel:
            dialog.reject()
        else:
            dialog.enabled.setChecked(False)
            dialog.accept()
        return dialog.result()

    monkeypatch.setattr(PriorityDialog, "exec", skip)
    window.edit_priorities()
    assert window.config.settings.use_priority == cancel
    assert [window.mapping_table.item(row, 10).text() for row in range(2)] == ["0", "2"]


def test_priority_dialog_repairs_conflicts_invalid_numbers_and_named_sections(app):
    dialog = PriorityDialog({"RAIL_A": ["1", "2"], "RAIL_B": ["0"], "RAIL_C": [""]}, True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert "사이트별" in dialog.error_label.text()
    dialog.table.item(0, 1).setText("보조 전원")
    for invalid in ("0", "-1", "1.5"):
        dialog.table.item(1, 1).setText(invalid)
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert "RAIL_B" in dialog.error_label.text()
    dialog.table.item(1, 1).setText("01")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.priorities == {"RAIL_A": "보조 전원", "RAIL_B": "1", "RAIL_C": ""}
    dialog.deleteLater()


def test_priority_conflict_can_be_explicitly_cleared(app):
    dialog = PriorityDialog({"RAIL": ["1", "2"]}, True)
    dialog.table.selectRow(0)
    dialog.clear_selected()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.priorities == {"RAIL": ""}
    dialog.deleteLater()


def test_skipping_disabled_priority_preserves_existing_preview(window, monkeypatch):
    window.config.mappings = [ChannelMapping(site="SITE0", channel="RAIL", priority="2")]
    window.fill_mappings()
    seed_preview(window)

    def skip(dialog):
        assert not dialog.enabled.isChecked()
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(PriorityDialog, "exec", skip)
    window.edit_priorities()
    assert not window.config.settings.use_priority
    assert window.mapping_table.item(0, 10).text() == "2"
    assert window.preview_book == "old-preview.xlsx"


def test_priority_state_save_open_new_and_same_source_reload(window, monkeypatch, tmp_path):
    window.config.settings.use_priority = True
    window.config.mappings = [ChannelMapping(site="SITE0", channel="RAIL", priority="2")]
    window.fill_mappings()
    path = tmp_path / "priority.report.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(path), ""))
    assert window.save_project()
    window.new_project()
    assert not window.config.settings.use_priority
    assert "사용 안 함" in window.priority_status.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.open_project()
    assert window.config.settings.use_priority
    assert "사용 중" in window.priority_status.text()
    assert window.mapping_table.item(0, 10).text() == "2"
    source = SpdData("same.spd", {"size": 1, "mtime_ns": 1})
    window.spd = source
    window.action = "load"
    window.on_finished((source, None, [], []))
    assert window.config.settings.use_priority
    assert window.mapping_table.item(0, 10).text() == "2"
    assert window.collect_config().settings.use_priority


def test_priority_editor_can_open_with_invalid_old_dcr_and_correct_legacy_rank(window, monkeypatch):
    window.config.settings.use_priority = True
    window.config.mappings = [ChannelMapping(site="SITE0", channel="RAIL", priority="-1")]
    window.fill_mappings()
    window.add_dcr()
    old_dcr_text = [window.dcr_table.item(0, col).text() for col in range(3)]

    def repair(dialog):
        dialog.table.item(0, 1).setText("3")
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(PriorityDialog, "exec", repair)
    window.edit_priorities()
    assert window.mapping_table.item(0, 10).text() == "3"
    assert [window.dcr_table.item(0, col).text() for col in range(3)] == old_dcr_text


def test_priority_entry_and_dialog_fit_small_window(window, app):
    from PySide6.QtGui import QFont
    window.resize(720, 460)
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    assert window.height() == 460
    assert window.priority_button.isVisible()
    assert window.priority_button.text() == "채널 순위 설정…"
    assert window.mapping_table.height() >= 70
    assert window.priority_button.geometry().bottom() < window.mapping_table.geometry().top()
    dialog = PriorityDialog({f"RAIL_{index}": [""] for index in range(100)}, False, window)
    dialog.setFont(QFont("Malgun Gothic", 13))
    dialog.resize(440, 400)
    dialog.show()
    app.processEvents()
    assert dialog.table.height() >= 70
    assert dialog.table.verticalScrollBar().maximum() > 0
    assert dialog.table.geometry().bottom() < dialog.clear_button.geometry().top()
    dialog.reject()
