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


@pytest.mark.parametrize("column", [9, 10, 12])
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
def test_priority_cancel_preserves_drafts_and_disabled_save_keeps_edits(window, monkeypatch, cancel):
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
    assert [window.mapping_table.item(row, 10).text() for row in range(2)] == (["0", "2"] if cancel else ["8", "8"])


def test_disabled_priority_accept_unchanged_preserves_legacy_drafts(window, monkeypatch):
    window.config.mappings = [ChannelMapping(site="SITE0", channel="RAIL_A", priority="01"),
                              ChannelMapping(site="SITE1", channel="rail_a", priority="2"),
                              ChannelMapping(site="SITE0", channel="RAIL_B", priority="-1")]
    window.fill_mappings()
    seed_preview(window)

    def unchanged(dialog):
        assert dialog.table.isEnabled()
        assert dialog.clear_button.isEnabled()
        assert not dialog.enabled.isChecked()
        dialog.accept()
        assert dialog.priorities == {}
        return dialog.result()

    monkeypatch.setattr(PriorityDialog, "exec", unchanged)
    window.edit_priorities()
    assert not window.config.settings.use_priority
    assert [window.mapping_table.item(row, 10).text() for row in range(3)] == ["01", "2", "-1"]
    assert window.preview_book == "old-preview.xlsx"


def test_priority_single_click_and_keyboard_entry_without_enabling_application(window, app, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialogButtonBox, QLineEdit

    window.config.mappings = [ChannelMapping(site="SITE0", channel="RAIL_A", priority="0"),
                              ChannelMapping(site="SITE1", channel="rail_a", priority="2", selected=False),
                              ChannelMapping(site="SITE0", channel="RAIL_B"),
                              ChannelMapping(site="SITE0", channel="RAIL_C", priority="-1")]
    window.fill_mappings()
    seed_preview(window)

    def enter(dialog):
        dialog.show()
        app.processEvents()
        rect = dialog.table.visualItemRect(dialog.table.item(0, 1))
        QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        app.processEvents()
        editor = next((editor for editor in dialog.table.findChildren(QLineEdit) if editor.isVisible()), None)
        assert editor is not None and editor.isVisible()
        QTest.keyClicks(editor, "3")
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        app.processEvents()
        assert dialog.table.currentRow() == 1
        assert dialog.table.currentColumn() == 1
        editor = next((editor for editor in dialog.table.findChildren(QLineEdit) if editor.isVisible()), None)
        assert editor is not None and editor.isVisible()
        QTest.keyClicks(editor, "5")
        buttons = dialog.findChild(QDialogButtonBox)
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert not dialog.enabled.isChecked()
        return dialog.result()

    monkeypatch.setattr(PriorityDialog, "exec", enter)
    window.edit_priorities()
    assert not window.config.settings.use_priority
    assert [window.mapping_table.item(row, 10).text() for row in range(4)] == ["3", "3", "5", "-1"]
    assert window.preview_book is None
    assert "초안" in window.status.text()


def test_disabled_priority_validates_only_edited_rows(app):
    dialog = PriorityDialog({"RAIL_A": ["1", "2"], "RAIL_B": ["-1"], "RAIL_C": [""]}, False)
    dialog.table.item(2, 1).setText("0")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert "RAIL_C" in dialog.error_label.text()
    dialog.table.item(2, 1).setText("보조 전원")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.priorities == {"RAIL_C": "보조 전원"}
    dialog.deleteLater()


def test_priority_tab_skips_readonly_cells_and_reaches_footer(app):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    dialog = PriorityDialog({"RAIL_A": [""], "RAIL_B": [""]}, False)
    dialog.show()
    app.processEvents()
    QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=dialog.table.visualItemRect(dialog.table.item(0, 1)).center())
    app.processEvents()

    def tab(key):
        editor = next(editor for editor in dialog.table.findChildren(QLineEdit) if editor.isVisible())
        QTest.keyClick(editor, key)
        app.processEvents()

    tab(Qt.Key.Key_Tab)
    assert dialog.table.currentRow() == 1
    tab(Qt.Key.Key_Backtab)
    assert dialog.table.currentRow() == 0
    tab(Qt.Key.Key_Tab)
    tab(Qt.Key.Key_Tab)
    assert dialog.clear_button.hasFocus()
    dialog.reject()


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
    assert window.channel_template_button.isVisible()
    assert window.channel_import_button.isVisible()
    assert window.channel_template_button.text() == "Excel 양식 저장…"
    assert window.channel_import_button.text() == "Excel 설정 불러오기…"
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
    assert not dialog.table.wordWrap()
    assert dialog.table.columnWidth(2) < dialog.table.columnWidth(1)
    assert dialog.table.item(0, 0).toolTip() == "RAIL_0"
    assert dialog.table.item(0, 2).text() == "미입력"
    assert dialog.table.item(0, 1).background() != dialog.table.item(0, 0).background()
    dialog.reject()


def channel_workbook(tmp_path, rows):
    from openpyxl import Workbook
    path = tmp_path / "channel-settings.xlsx"
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()
    return path


def test_channel_template_exports_live_selected_rows_despite_filter_and_partial_dcr(window, monkeypatch, tmp_path):
    from openpyxl import load_workbook
    window.config.mappings = [ChannelMapping(channel="OLD", site="S0", group="G", priority="2", display_name="Alias"),
                              ChannelMapping(channel="B", site="S1"),
                              ChannelMapping(channel="OFF", site="S0", selected=False),
                              ChannelMapping(channel="INCOMPLETE")]
    window.fill_mappings()
    window.mapping_table.item(0, 8).setText("LIVE")
    window.map_filter.setText("LIVE")
    window.add_dcr()
    path = tmp_path / "template.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(path.with_suffix("")), ""))
    window.export_channel_template()
    book = load_workbook(path)
    assert list(book.active.values) == [("채널명", "사이트", "priority", "group", "net"),
                                        ("LIVE", "S0", None, None, None), ("B", "S1", None, None, None)]
    book.close()
    assert not window.config.settings.use_priority
    assert window.config.channel_settings_source_path == ""
    assert window.mapping_table.item(0, 9).text() == "G"


def test_channel_import_matches_live_identities_and_propagates_without_confirmation_changes(window, monkeypatch, tmp_path):
    window.config.mappings = [ChannelMapping(channel="OLD", site="S1", priority="8", status="기존 상태"),
                              ChannelMapping(channel="RAIL_A", site="s1", priority="7"),
                              ChannelMapping(channel="rail_a", site="S2", priority="9", selected=False, group="OFF"),
                              ChannelMapping(channel="RAIL_B", site="S0", priority="4", group="OLD")]
    window.fill_mappings()
    window.mapping_table.item(0, 8).setText("RAIL_A")
    window.map_filter.setText("nothing-visible")
    confirmations = [window.mapping_table.item(row, 2).checkState() for row in range(4)]
    statuses = [window.mapping_table.item(row, 11).text() for row in range(4)]
    window.add_dcr()
    old_dcr = [[window.dcr_table.item(0, col).text() for col in range(3)]]
    seed_preview(window)
    path = channel_workbook(tmp_path, [("group", "사이트", "채널명", "priority"),
                                       ("B-new", "S0", "RAIL_B", None),
                                       ("A-new", " s1 ", " rail_a ", 2)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.import_channel_settings()
    assert [window.mapping_table.item(row, 10).text() for row in range(4)] == ["2", "2", "2", "4"]
    assert [window.mapping_table.item(row, 9).text() for row in range(4)] == ["A-new", "A-new", "OFF", "B-new"]
    assert [window.mapping_table.item(row, 2).checkState() for row in range(4)] == confirmations
    assert [window.mapping_table.item(row, 11).text() for row in range(4)] == statuses
    assert [[window.dcr_table.item(0, col).text() for col in range(3)]] == old_dcr
    assert window.config.settings.use_priority
    assert window.config.channel_settings_source_path == str(path.resolve())
    assert "Excel:" in window.priority_status.text()
    assert window.priority_status.toolTip() == str(path.resolve())
    assert_preview_empty(window)


@pytest.mark.parametrize("enabled", [False, True])
def test_channel_group_only_import_preserves_priority_state_and_unchanged_preview(window, monkeypatch, tmp_path, enabled):
    window.config.settings.use_priority = enabled
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="3", group="same", display_name="Alias")]
    window.fill_mappings()
    seed_preview(window)
    path = channel_workbook(tmp_path, [("채널명", "사이트", "group"), ("A", "S0", "same")])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.import_channel_settings()
    assert window.config.settings.use_priority == enabled
    assert window.mapping_table.item(0, 10).text() == "3"
    assert window.mapping_table.item(0, 12).text() == "Alias"
    assert window.preview_book == "old-preview.xlsx"
    assert "기존 값과 같습니다" in window.status.text()
    assert window.config.channel_settings_source_path == str(path.resolve())


@pytest.mark.parametrize("bad_row", [("A", "S0", "0", "bad"),
                                     ("UNKNOWN", "S0", "2", "bad"),
                                     ("A", "S0", "1", "bad")])
def test_channel_import_errors_do_not_partially_apply(window, monkeypatch, tmp_path, bad_row):
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="9", group="old")]
    window.config.channel_settings_source_path = "previous.xlsx"
    window.fill_mappings()
    seed_preview(window)
    path = channel_workbook(tmp_path, [("채널명", "사이트", "priority", "group"),
                                       ("A", "S0", "2", "new"), bad_row])
    errors = []
    monkeypatch.setattr(window, "error", lambda exc: errors.append(str(exc)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.import_channel_settings()
    assert errors
    assert window.mapping_table.item(0, 9).text() == "old"
    assert window.mapping_table.item(0, 10).text() == "9"
    assert not window.config.settings.use_priority
    assert window.config.channel_settings_source_path == "previous.xlsx"
    assert window.preview_book == "old-preview.xlsx"


@pytest.mark.parametrize("source", ["settings", "dcr", "spd", "touchstone", "project", "loaded", "parsed"])
def test_channel_template_cannot_overwrite_inputs(window, monkeypatch, tmp_path, source):
    path = tmp_path / "protected.xlsx"
    path.write_bytes(b"original")
    window.config.mappings = [ChannelMapping(channel="A", site="S0")]
    window.fill_mappings()
    if source == "settings":
        window.config.channel_settings_source_path = str(path)
    elif source == "dcr":
        window.config.dcr_source_path = str(path)
    elif source == "project":
        window.project_path = str(path)
    elif source == "loaded":
        window.loaded_paths["spd"] = str(path)
    elif source == "parsed":
        window.spd = SpdData(str(path), {})
    else:
        (window.spd_path if source == "spd" else window.ts_path).setText(str(path))
    errors = []
    monkeypatch.setattr(window, "error", lambda exc: errors.append(str(exc)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(path), ""))
    window.export_channel_template()
    assert errors and "덮어쓸 수 없습니다" in errors[0]
    assert path.read_bytes() == b"original"


def test_channel_excel_cancel_preserves_settings_and_preview(window, monkeypatch):
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="9", group="old")]
    window.config.channel_settings_source_path = "previous.xlsx"
    window.fill_mappings()
    seed_preview(window)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: ("", ""))
    window.import_channel_settings()
    window.export_channel_template()
    assert window.mapping_table.item(0, 9).text() == "old"
    assert window.mapping_table.item(0, 10).text() == "9"
    assert not window.config.settings.use_priority
    assert window.config.channel_settings_source_path == "previous.xlsx"
    assert window.preview_book == "old-preview.xlsx"


def test_priority_only_channel_import_enables_application_and_preserves_group(window, monkeypatch, tmp_path):
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="2", group="existing", display_name="Alias")]
    window.fill_mappings()
    seed_preview(window)
    path = channel_workbook(tmp_path, [("채널명", "사이트", "priority"), ("A", "S0", "2")])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.import_channel_settings()
    assert window.config.settings.use_priority
    assert window.mapping_table.item(0, 9).text() == "existing"
    assert window.mapping_table.item(0, 12).text() == "Alias"
    assert_preview_empty(window)


@pytest.mark.parametrize("preserve", ["same", "pending", "changed"])
def test_channel_settings_badge_follows_mapping_source_reload(window, preserve):
    window.config.channel_settings_source_path = "settings.xlsx"
    window.spd = SpdData("old.spd", {"size": 1})
    if preserve == "pending":
        window.pending_config = window.config
    window.action = "load"
    source = window.spd if preserve == "same" else SpdData("new.spd", {"size": 2})
    window.on_finished((source, None, [], []))
    assert window.config.channel_settings_source_path == ("" if preserve == "changed" else "settings.xlsx")
    assert ("Excel:" in window.priority_status.text()) == (preserve != "changed")


def send_channel_drag(target, mime, actions=Qt.DropAction.CopyAction | Qt.DropAction.MoveAction):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
    enter = QDragEnterEvent(QPoint(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    move = QDragMoveEvent(QPoint(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop = QDropEvent(QPointF(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    for event in (enter, move, drop):
        QApplication.sendEvent(target, event)
    return enter, move, drop


@pytest.mark.parametrize("target", ["page", "table", "button", "search", "group", "editor"])
def test_channel_excel_drag_drop_uses_copy_and_shared_import(window, app, monkeypatch, tmp_path, target):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtWidgets import QLineEdit
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="9", group="old")]
    window.fill_mappings()
    window.group_input.setText("manual draft")
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    targets = {"page": window.channel_settings_page, "table": window.mapping_table.viewport(),
               "button": window.channel_import_button, "search": window.map_filter, "group": window.group_input}
    if target == "editor":
        window.mapping_table.editItem(window.mapping_table.item(0, 9))
        app.processEvents()
        targets[target] = next(editor for editor in window.mapping_table.findChildren(QLineEdit) if editor.isVisible())
    path = channel_workbook(tmp_path, [("채널명", "사이트", "priority", "group"), ("A", "S0", 2, "new")])
    original = path.read_bytes()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: pytest.fail("Drop must not open a file picker"))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    events = send_channel_drag(targets[target], mime)
    assert all(event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction for event in events)
    assert window.config.settings.use_priority
    assert window.mapping_table.item(0, 10).text() == "2"
    assert window.mapping_table.item(0, 9).text() == "new"
    assert window.config.channel_settings_source_path == str(path.resolve())
    assert window.map_filter.text() == ""
    assert window.group_input.text() == "manual draft"
    assert path.read_bytes() == original


@pytest.mark.parametrize("payload", ["multi", "remote", "network", "extension", "missing", "directory", "move_only"])
def test_channel_excel_drop_rejects_unsupported_payload_without_path_insertion(window, app, tmp_path, payload):
    from PySide6.QtCore import QMimeData, QUrl
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="9")]
    window.fill_mappings()
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    path = channel_workbook(tmp_path, [("채널명", "사이트", "priority"), ("A", "S0", 2)])
    urls = [QUrl.fromLocalFile(str(path))]
    if payload == "multi":
        urls *= 2
    elif payload == "remote":
        urls = [QUrl("https://example.com/settings.xlsx")]
    elif payload == "network":
        urls = [QUrl("file://server/share/settings.xlsx")]
    elif payload == "extension":
        path = tmp_path / "settings.txt"
        path.write_text("unsupported")
        urls = [QUrl.fromLocalFile(str(path))]
    elif payload == "missing":
        urls = [QUrl.fromLocalFile(str(tmp_path / "missing.xlsx"))]
    elif payload == "directory":
        path = tmp_path / "folder.xlsx"
        path.mkdir()
        urls = [QUrl.fromLocalFile(str(path))]
    mime = QMimeData()
    mime.setUrls(urls)
    actions = Qt.DropAction.MoveAction if payload == "move_only" else Qt.DropAction.CopyAction
    for target in (window.channel_settings_page, window.map_filter, window.group_input):
        events = send_channel_drag(target, mime, actions)
        assert not any(event.isAccepted() for event in events)
    assert window.map_filter.text() == window.group_input.text() == ""
    assert window.mapping_table.item(0, 10).text() == "9"
    assert not window.config.settings.use_priority
    assert window.config.channel_settings_source_path == ""


@pytest.mark.parametrize("blocked", ["busy", "disabled", "other_tab"])
def test_channel_excel_drop_respects_busy_disabled_and_active_tab(window, app, monkeypatch, tmp_path, blocked):
    from PySide6.QtCore import QMimeData, QUrl
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="9")]
    window.fill_mappings()
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    path = channel_workbook(tmp_path, [("채널명", "사이트", "priority"), ("A", "S0", 2)])
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    with monkeypatch.context() as patch:
        if blocked == "busy":
            patch.setattr(type(window.runner), "busy", property(lambda _: True))
        elif blocked == "disabled":
            window.on_busy(True)
        else:
            window.tabs.setCurrentIndex(0)
        events = send_channel_drag(window.mapping_table.viewport(), mime)
        assert not any(event.isAccepted() for event in events)
        if blocked != "other_tab":
            patch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: pytest.fail("Busy picker must remain closed"))
            window.import_channel_settings()
            window.apply_channel_settings(path)
    window.on_busy(False)
    assert window.mapping_table.item(0, 10).text() == "9"
    assert not window.config.settings.use_priority
    assert window.config.channel_settings_source_path == ""


def test_channel_drop_reports_same_invalid_workbook_error_as_button(window, app, monkeypatch, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    window.config.mappings = [ChannelMapping(channel="A", site="S0", priority="9", group="old")]
    window.fill_mappings()
    seed_preview(window)
    path = channel_workbook(tmp_path, [("채널명", "사이트", "priority"), ("A", "S0", 0)])
    errors = []
    monkeypatch.setattr(window, "error", lambda exc: errors.append(str(exc)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.import_channel_settings()
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    send_channel_drag(window.mapping_table.viewport(), mime)
    assert len(errors) == 2 and errors[0] == errors[1]
    assert window.mapping_table.item(0, 10).text() == "9"
    assert window.mapping_table.item(0, 9).text() == "old"
    assert window.preview_book == "old-preview.xlsx"
    assert window.config.channel_settings_source_path == ""


def test_channel_search_still_accepts_normal_text_drag(window, app):
    from PySide6.QtCore import QMimeData
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    mime = QMimeData()
    mime.setText("RAIL")
    events = send_channel_drag(window.map_filter, mime, Qt.DropAction.CopyAction)
    assert events[0].isAccepted() and events[-1].isAccepted()
    assert window.map_filter.text() == "RAIL"
    assert not window.config.settings.use_priority


def test_channel_drop_commits_active_identity_editor_before_matching(window, app, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtWidgets import QLineEdit
    window.config.mappings = [ChannelMapping(channel="OLD", site="S0", group="old")]
    window.fill_mappings()
    window.tabs.setCurrentIndex(1)
    window.show()
    app.processEvents()
    window.mapping_table.editItem(window.mapping_table.item(0, 8))
    app.processEvents()
    editor = next(editor for editor in window.mapping_table.findChildren(QLineEdit) if editor.isVisible())
    editor.setText("A")
    assert window.mapping_table.item(0, 8).text() == "OLD"
    path = channel_workbook(tmp_path, [("채널명", "사이트", "group"), ("A", "S0", "new")])
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    send_channel_drag(editor, mime)
    assert window.mapping_table.item(0, 8).text() == "A"
    assert window.mapping_table.item(0, 9).text() == "new"
    assert window.config.channel_settings_source_path == str(path.resolve())


@pytest.mark.parametrize("route", ["picker", "drop"])
@pytest.mark.parametrize("enabled", [False, True])
def test_net_only_import_changes_display_name_without_physical_or_rank_changes(window, app, monkeypatch, tmp_path, route, enabled):
    from dataclasses import asdict
    from PySide6.QtCore import QMimeData, QUrl
    window.config.settings.use_priority = enabled
    window.config.mappings = [ChannelMapping(1, "source", "SPD_PORT", "U0_VDD", "01", "VDD", group="G", priority="3", status="기존 상태", confirmed=False),
                              ChannelMapping(2, "duplicate", "SPD_PORT", "U0_VDD", "01", "vdd", group="G", priority="3"),
                              ChannelMapping(site="01", channel="VDD", selected=False, display_name="excluded"),
                              ChannelMapping(site="02", channel="VDD", display_name="other site")]
    window.config.dcr = [DcrRow("01", "VDD", 4.2)]
    old_dcr = window.config.dcr
    window.fill_mappings()
    window.fill_dcr()
    window.add_dcr()
    dcr_text = [[window.dcr_table.item(row, col).text() for col in range(3)] for row in range(2)]
    before = [asdict(mapping) for mapping in window.collect_mappings()]
    seed_preview(window)
    alias = "DUT3-POWER/VRM::VDD"
    path = channel_workbook(tmp_path, [("net", "site", "channel"), (alias, " 01 ", " vdd ")])
    if route == "picker":
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
        window.import_channel_settings()
    else:
        window.tabs.setCurrentIndex(1)
        window.show()
        app.processEvents()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        send_channel_drag(window.mapping_table.viewport(), mime)
    mappings = window.collect_mappings()
    assert [mapping.display_name for mapping in mappings] == [alias, alias, "excluded", "other site"]
    for previous, current in zip(before, mappings):
        current = asdict(current)
        current["display_name"] = previous["display_name"]
        assert current == previous
    assert window.config.settings.use_priority == enabled
    assert window.config.dcr is old_dcr
    assert [[window.dcr_table.item(row, col).text() for col in range(3)] for row in range(2)] == dcr_text
    assert "표시명 1개" in window.status.text()
    assert window.config.channel_settings_source_path == str(path.resolve())
    assert_preview_empty(window)


def test_report_display_name_is_editable_clearable_searchable_and_preserves_raw_mapping(window):
    window.config.mappings = [ChannelMapping(net="U0_PHYSICAL_NET", channel="RAW_CHANNEL", site="SITE0", group="G", priority="2", status="사용자 확인")]
    window.fill_mappings()
    assert window.mapping_table.horizontalHeaderItem(12).text() == "보고서 표시명"
    assert "실제 연결" in window.mapping_table.horizontalHeaderItem(6).text()
    assert window.mapping_table.horizontalHeader().visualIndex(12) == 7
    assert window.mapping_table.item(0, 12).flags() & Qt.ItemFlag.ItemIsEditable
    seed_preview(window)
    window.mapping_table.item(0, 12).setText("DISPLAY_ALIAS")
    mapping = window.collect_config().mappings[0]
    assert (mapping.net, mapping.channel, mapping.site, mapping.group, mapping.priority, mapping.confirmed, mapping.status) == ("U0_PHYSICAL_NET", "RAW_CHANNEL", "SITE0", "G", "2", True, "사용자 확인")
    assert mapping.display_name == "DISPLAY_ALIAS"
    assert_preview_empty(window)
    window.map_filter.setText("display_alias")
    assert not window.mapping_table.isRowHidden(0)
    window.map_filter.setText("physical_net")
    assert not window.mapping_table.isRowHidden(0)
    seed_preview(window)
    window.mapping_table.item(0, 12).setText("")
    assert window.collect_config().mappings[0].display_name == ""
    assert window.mapping_table.item(0, 6).text() == "U0_PHYSICAL_NET"
    assert window.mapping_table.item(0, 2).checkState() == Qt.CheckState.Checked
    assert_preview_empty(window)


def test_report_display_name_roundtrips_through_gui_project_save_open(window, monkeypatch, tmp_path):
    window.config.mappings = [ChannelMapping(net="PHYSICAL", channel="CHANNEL", site="SITE0")]
    window.fill_mappings()
    window.mapping_table.item(0, 12).setText("Report name")
    path = tmp_path / "display.report.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(path), ""))
    assert window.save_project()
    window.new_project()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.open_project()
    assert window.mapping_table.item(0, 12).text() == "Report name"
    mapping = window.collect_config().mappings[0]
    assert (mapping.net, mapping.channel, mapping.site, mapping.display_name) == ("PHYSICAL", "CHANNEL", "SITE0", "Report name")


def test_impedance_line_width_control_invalidates_preview_and_roundtrips(window, monkeypatch, tmp_path):
    control = window.plot_line_width
    assert control.accessibleName() == "그래프 선 굵기"
    assert (control.minimum(), control.maximum(), control.decimals(), control.singleStep(), control.suffix()) == (1.0, 5.0, 1, 0.5, " pt")
    control.lineEdit().setText("2.5")
    assert window.collect_config().settings.plot_line_width_pt == 2.5
    seed_preview(window)
    control.setValue(3.0)
    assert_preview_empty(window)
    control.setValue(2.5)

    path = tmp_path / "line-width.report.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(path), ""))
    assert window.save_project()
    window.new_project()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))
    window.open_project()
    assert window.plot_line_width.value() == 2.5
    assert window.collect_config().settings.plot_line_width_pt == 2.5
