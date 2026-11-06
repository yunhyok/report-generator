import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import Workbook
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from report_generator.ui.window import MainWindow


def test_preview_table_has_contrast_in_qt_render(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "preview.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["VISIBLE", "123"])
    book.save(path)

    window = MainWindow()
    try:
        window.preview_book = str(path)
        window.preview_selector.addItem("table", ("table", sheet.title))
        window.resize(900, 700)
        window.show()
        window.show_preview()
        app.processEvents()

        browser = window.preview_table
        image = browser.grab().toImage()
        assert image.pixelColor(image.width() - 2, image.height() - 2).name() == "#ffffff"
        assert browser.palette().color(browser.palette().ColorRole.Text).name() == "#222222"

        cursor = browser.document().find("VISIBLE")
        assert not cursor.isNull()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        browser.setTextCursor(cursor)
        app.processEvents()
        selected = browser.grab().toImage()
        assert any(selected.pixelColor(x, y).name() == "#345d8c"
                   for x in range(selected.width()) for y in range(selected.height()))
        assert browser.palette().color(browser.palette().ColorRole.HighlightedText).name() == "#ffffff"
    finally:
        window.close()
