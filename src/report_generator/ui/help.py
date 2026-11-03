import html
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QAction, QIcon, QTextOption
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from .. import __version__
from .theme import apply_theme


DOCS = Path(__file__).resolve().parents[1] / "docs"
HELP_PATH = DOCS / "help.html"


class HelpBrowser(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.documents = {
            QUrl.fromLocalFile(str(path)): path
            for path in (HELP_PATH, DOCS / "THIRD_PARTY_NOTICES.txt")
        }

    def document_path(self, url):
        document_url = QUrl(url)
        document_url.setFragment(None)
        return self.documents.get(document_url)

    def loadResource(self, resource_type, url):
        path = self.document_path(url)
        if path is not None:
            if path.suffix == ".txt":
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    text = f"문서를 읽을 수 없습니다: {exc}"
                return QByteArray(f'<pre style="white-space: pre-wrap;">{html.escape(text)}</pre>'.encode("utf-8"))
            return super().loadResource(resource_type, url)
        return QByteArray()


class HelpWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"SPD Report Generator {__version__} · 사용 설명서")
        self.setWindowIcon(QIcon(str(DOCS.parent / "assets" / "app.ico")))
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(960, available.width() - 40), min(780, available.height() - 60))
        self.setMinimumSize(320, 240)
        self.browser = HelpBrowser()
        self.browser.setAccessibleName("사용 설명서 본문")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.browser.anchorClicked.connect(self.navigate)
        root = QWidget()
        layout = QVBoxLayout(root)
        controls = QHBoxLayout()
        self.back_button = QPushButton("뒤로")
        self.forward_button = QPushButton("앞으로")
        self.home_button = QPushButton("목차")
        self.back_button.clicked.connect(self.browser.backward)
        self.forward_button.clicked.connect(self.browser.forward)
        self.home_button.clicked.connect(self.home)
        self.browser.backwardAvailable.connect(self.back_button.setEnabled)
        self.browser.forwardAvailable.connect(self.forward_button.setEnabled)
        self.back_button.setEnabled(False)
        self.forward_button.setEnabled(False)
        for control in (self.back_button, self.forward_button, self.home_button):
            controls.addWidget(control)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(self.browser, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setProperty("muted", True)
        layout.addWidget(self.message)
        self.setCentralWidget(root)
        apply_theme(self)
        self.browser.setStyleSheet("""
            QTextBrowser { background: #202226; color: #eceef1; border: 1px solid #626c7a; padding: 10px; }
            QTextBrowser:focus { border-color: #8fc1ff; }
        """)
        close_action = QAction(self)
        close_action.setShortcut("Esc")
        close_action.triggered.connect(self.close)
        self.addAction(close_action)
        home_action = QAction(self)
        home_action.setShortcut("F1")
        home_action.triggered.connect(self.home)
        self.addAction(home_action)
        self.navigate(QUrl.fromLocalFile(str(HELP_PATH)))

    def home(self):
        url = QUrl.fromLocalFile(str(HELP_PATH))
        url.setFragment("contents")
        self.navigate(url)

    def navigate(self, url):
        url = self.browser.source().resolved(url)
        path = self.browser.document_path(url)
        if path is None:
            self.message.setText("동봉된 설명서와 라이선스 문서만 열 수 있습니다.")
            return
        if not path.is_file():
            self.message.setText(f"설명서 파일을 찾을 수 없습니다: {url.toLocalFile()}")
            return
        self.browser.setSource(url)
        if url.fragment() == "top":
            self.browser.verticalScrollBar().setValue(0)
        self.message.setText("오프라인 설명서 · 뒤로 / 목차로 설명서에 돌아갈 수 있습니다.")
