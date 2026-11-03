import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import subprocess
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from report_generator.ui.help import DOCS, HELP_PATH, HelpWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def help_window(app):
    window = HelpWindow()
    window.resize(640, 480)
    window.show()
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def click_link(browser, target, label):
    cursor = browser.document().find(label)
    assert not cursor.isNull()
    cursor.setPosition((cursor.selectionStart() + cursor.selectionEnd()) // 2)
    browser.setTextCursor(cursor)
    browser.ensureCursorVisible()
    QApplication.processEvents()
    position = browser.cursorRect(cursor).center()
    assert browser.anchorAt(position) == target
    QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QApplication.processEvents()


def test_shipped_manual_toc_and_all_sections(help_window, app):
    browser = help_window.browser
    manual = HELP_PATH.read_text(encoding="utf-8")
    sections = re.findall(r'<a name="([^"]+)">', manual)
    assert len(sections) >= 12
    text = browser.toPlainText()
    assert "인터넷 없이 읽습니다" in text
    assert "화면 입력 없이 생성하고 검증하기" in text
    assert "THIRD_PARTY_NOTICES.txt" in text
    click_link(browser, "#mapping", "3. 채널·그룹 연결")
    assert browser.source().fragment() == "mapping"
    assert browser.verticalScrollBar().value() > 0
    for section in sections[1:]:
        help_window.navigate(QUrl(f"#{section}"))
        app.processEvents()
        assert browser.source().fragment() == section
        assert browser.verticalScrollBar().value() > 0
    help_window.home_button.click()
    assert browser.source().fragment() == "contents"
    assert browser.verticalScrollBar().value() > 0
    help_window.navigate(QUrl("#top"))
    assert browser.verticalScrollBar().value() == 0


def test_notices_and_return_stay_inside_viewer(help_window, app):
    browser = help_window.browser
    click_link(browser, "THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt")
    assert browser.source() == QUrl.fromLocalFile(str(DOCS / "THIRD_PARTY_NOTICES.txt"))
    assert "Third-party notices" in browser.toPlainText()
    assert browser.toPlainText().strip() == (DOCS / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8").strip()
    assert help_window.back_button.isEnabled()
    help_window.back_button.click()
    assert browser.source() == QUrl.fromLocalFile(str(HELP_PATH))
    help_window.forward_button.click()
    assert "Third-party notices" in browser.toPlainText()
    help_window.home_button.click()
    assert browser.source().fragment() == "contents"
    assert "입력 파일에서 Excel 보고서까지" in browser.toPlainText()


def test_navigation_blocks_network_and_other_local_files(help_window, tmp_path):
    browser = help_window.browser
    original = browser.source()
    unrelated = tmp_path / "unrelated.html"
    unrelated.write_text("PRIVATE", encoding="utf-8")
    for url in (QUrl("https://example.com/"), QUrl.fromLocalFile(str(unrelated))):
        help_window.navigate(url)
        assert browser.source() == original
        assert not browser.loadResource(1, url)
    assert "동봉된 설명서" in help_window.message.text()


def test_manual_formats_and_small_window(help_window, app):
    browser = help_window.browser
    for needle in ("입력 파일에서 Excel 보고서까지", "한 열을 채널에 순서대로 연결하기", "다중 포트 S 행렬", "--validate-project"):
        cursor = browser.document().find(needle)
        assert not cursor.isNull()
        assert cursor.charFormat().foreground().color() in (QColor("#eceef1"), QColor("#ffffff"))
    help_window.resize(360, 320)
    app.processEvents()
    assert browser.viewport().width() > 250
    assert browser.viewport().height() > 140
    assert help_window.home_button.geometry().right() < help_window.width()
    browser.moveCursor(QTextCursor.MoveOperation.End)
    browser.ensureCursorVisible()
    assert browser.verticalScrollBar().value() > 0


def test_menu_f1_reuses_internal_window(app, monkeypatch):
    from report_generator.ui.window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        action = next(action for menu in window.menuBar().actions() if menu.menu()
                      for action in menu.menu().actions() if action.text() == "사용 설명서")
        assert action.shortcut().toString() == "F1"
        action.trigger()
        viewer = window.help_window
        assert isinstance(viewer, HelpWindow) and viewer.isVisible()
        viewer.close()
        window.activateWindow()
        window.setFocus()
        app.processEvents()
        QTest.keyClick(window, Qt.Key.Key_F1)
        app.processEvents()
        assert window.help_window is viewer and viewer.isVisible()
        viewer.showMinimized()
        app.processEvents()
        window.open_help()
        assert not viewer.isMinimized() and viewer.isVisible()
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_standalone_flag_uses_normal_entry_point():
    code = '''
import sys
from PySide6 import QtWidgets
class CheckedApplication(QtWidgets.QApplication):
    def exec(self):
        from report_generator.ui.help import HelpWindow
        windows = self.topLevelWidgets()
        assert len(windows) == 1 and isinstance(windows[0], HelpWindow)
        assert windows[0].isVisible()
        assert "report_generator.ui.window" not in sys.modules
        assert "입력 파일에서 Excel 보고서까지" in windows[0].browser.toPlainText()
        windows[0].close()
        return 0
QtWidgets.QApplication = CheckedApplication
sys.argv = ["spd-report-generator", "--show-help"]
from report_generator.app import main
raise SystemExit(main())
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            encoding="utf-8", timeout=30,
                            env={**os.environ, "PYTHONPATH": str(DOCS.parents[1])})
    assert result.returncode == 0, result.stderr
