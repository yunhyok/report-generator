from __future__ import annotations

import multiprocessing
import os
import sys


def main():
    multiprocessing.freeze_support()
    # Tiny per-frequency solves are faster without a large BLAS thread pool.
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from report_generator.selftest import run
        return run(sys.argv[2])
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont
    from report_generator.ui.window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("SPD Report Generator")
    app.setOrganizationName("SPD Report Generator")
    app.setFont(QFont("Malgun Gothic", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
