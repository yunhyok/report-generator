from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal


def run_job(action, payload, messages, cancellation):
    last = 0.0

    def progress(message, current, total):
        nonlocal last
        now = time.monotonic()
        if now - last > 0.15 or current == total:
            messages.put(("progress", (message, current, total)))
            last = now

    try:
        from .core.models import CancelledError, source_signature
        if action == "load":
            from .core.spd import read_spd
            from .core.touchstone import read_touchstone
            from .core.mapping import suggest_mappings
            spd = ts = None
            errors = []
            for kind, reader in (("spd", read_spd), ("touchstone", read_touchstone)):
                path = payload.get(kind)
                if not path:
                    continue
                try:
                    result = reader(path, progress=progress, cancel=cancellation.is_set)
                    if kind == "spd":
                        spd = result
                    else:
                        ts = result
                except CancelledError:
                    raise
                except Exception as exc:
                    errors.append(f"{kind}: {exc}")
            if spd is None and ts is None:
                raise ValueError("\n".join(errors) or "입력 파일을 선택하세요.")
            result = (spd, ts, suggest_mappings(spd, ts), errors)
        else:
            from .core.mapping import validate_mappings
            from .core.project import validate_config
            from .core.report import build_report
            from .core.render import render_placement, render_group_plot, placement_components
            from .core.report import plan_pages
            config, spd, ts = payload["config"], payload.get("spd"), payload.get("ts")
            validate_config(config)
            if any(m.selected for m in config.mappings):
                validate_mappings(config, ts)
            for source in (spd, ts):
                if source and source_signature(source.path) != source.signature:
                    raise ValueError("입력 파일이 변경되었습니다. 파일을 다시 읽고 연결을 확인하세요.")
            if action == "export":
                result = build_report(config, spd, ts, payload["path"], progress=progress, cancel=cancellation.is_set)
            elif action == "preview":
                folder = Path(payload["folder"])
                folder.mkdir(parents=True, exist_ok=True)
                report = build_report(config, spd, ts, folder / "preview.xlsx", progress=progress, cancel=cancellation.is_set)
                images = []
                if spd and any(c.x_mm is not None and c.y_mm is not None for c in spd.components):
                    for side in (["Top", "Bottom"] if config.settings.board_type == "PCB" else ["Top"]):
                        if not placement_components(spd, config, side):
                            continue
                        if cancellation.is_set():
                            raise CancelledError("작업이 취소되었습니다.")
                        path = folder / f"placement-{side}.png"
                        render_placement(spd, config, side, path)
                        images.append((f"CAP {side}", str(path)))
                if ts:
                    for index, group in enumerate(dict.fromkeys(m.group for m in config.mappings if m.selected and m.group and m.port_index is not None)):
                        if f"그룹: {group}" in report.omitted:
                            continue
                        if cancellation.is_set():
                            raise CancelledError("작업이 취소되었습니다.")
                        path = folder / f"plot-{index}.png"
                        render_group_plot(ts, config, group, path)
                        images.append((group, str(path)))
                result = (report, images)
            else:
                raise ValueError("알 수 없는 작업입니다.")
        messages.put(("result", result))
    except Exception as exc:
        messages.put(("error", str(exc)))


class JobRunner(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.messages = None
        self.cancellation = None
        self.cancel_time = None
        self.exit_time = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    @property
    def busy(self):
        return self.process is not None

    def start(self, action, payload):
        if self.busy:
            raise RuntimeError("진행 중인 작업을 먼저 마치세요.")
        context = mp.get_context("spawn")
        self.messages = context.Queue()
        self.cancellation = context.Event()
        self.process = context.Process(target=run_job, args=(action, payload, self.messages, self.cancellation), daemon=True)
        self.cancel_time = self.exit_time = None
        try:
            self.process.start()
        except Exception:
            self.messages.close()
            self.process.close()
            self.process = self.messages = self.cancellation = None
            raise
        self.timer.start()
        self.busy_changed.emit(True)

    def cancel(self):
        if self.busy:
            self.cancellation.set()
            self.cancel_time = self.cancel_time or time.monotonic()

    def poll(self):
        import queue
        try:
            while self.busy:
                kind, value = self.messages.get_nowait()
                if kind == "progress":
                    self.progress.emit(*value)
                else:
                    self.cleanup()
                    (self.finished if kind == "result" else self.failed).emit(value)
                    return
        except queue.Empty:
            pass
        if not self.busy:
            return
        if self.cancel_time and time.monotonic() - self.cancel_time > 10:
            self.cleanup(force=True)
            self.failed.emit("작업을 취소했습니다.")
        elif not self.process.is_alive():
            self.exit_time = self.exit_time or time.monotonic()
            if time.monotonic() - self.exit_time > 1:
                code = self.process.exitcode
                self.cleanup()
                self.failed.emit(f"작업 프로세스가 종료되었습니다 (코드 {code}).")

    def cleanup(self, force=False):
        self.timer.stop()
        if self.process:
            if force and self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=0.2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=0.2)
            self.process.close()
        if self.messages:
            self.messages.close()
        self.process = self.messages = self.cancellation = None
        self.busy_changed.emit(False)
