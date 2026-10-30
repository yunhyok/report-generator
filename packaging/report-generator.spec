# -*- mode: python ; coding: utf-8 -*-
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "SPD Report Generator"
qt_package = Path(next(iter(find_spec("PySide6").submodule_search_locations)))
qt_excludes = ["PySide6." + path.stem for path in qt_package.glob("Qt*.pyd")
               if path.stem not in {"QtCore", "QtGui", "QtWidgets"}]

a = Analysis(
    [str(ROOT / "src/report_generator/app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src/report_generator/docs/help.html"), "report_generator/docs"),
        (str(ROOT / "src/report_generator/docs/THIRD_PARTY_NOTICES.txt"), "report_generator/docs"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "packaging/licenses"), "licenses"),
    ],
    hiddenimports=["matplotlib.backends.backend_agg", "report_generator.selftest"],
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[],
    excludes=[
        "pandas", "scipy", "IPython", "notebook", "jupyter", "tkinter", "_tkinter",
        "PyQt5", "PyQt6", "PySide2", "wx", "gi", "win32com", "pythoncom", "pywintypes",
        "lxml", "bs4", "yaml", "psutil", "pytest", "setuptools", "pip",
        "numpy.tests", "matplotlib.tests", "PIL.tests", "openpyxl.tests", "QtWebEngine",
        *qt_excludes,
    ],
    noarchive=False,
)

def needed_binary(entry):
    destination = entry[0].replace("\\", "/").lower()
    name = Path(destination).name
    # A foreign Poppler ICU DLL on PATH conflicts with Windows' ICU ABI used by Qt.
    if name == "icuuc.dll" or name.startswith("icudt"):
        return False
    if name.startswith("qt6") and name.endswith(".dll"):
        return name in {"qt6core.dll", "qt6gui.dll", "qt6widgets.dll"}
    if "/plugins/imageformats/" in destination:
        return name in {"qjpeg.dll", "qico.dll", "qgif.dll"}
    if "/plugins/platforms/" in destination:
        return name in {"qwindows.dll", "qoffscreen.dll"}
    return True

a.binaries = [entry for entry in a.binaries if needed_binary(entry)]
a.datas = [entry for entry in a.datas if "/mpl-data/sample_data/" not in entry[0].replace("\\", "/")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
