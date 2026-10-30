from __future__ import annotations

import math
import re
import warnings
from pathlib import Path

import numpy as np

from .models import Cancelled, Port, Progress, TouchstoneData, checkpoint, source_signature

_MAX_PORTS = 1024
_MAX_RECORD_BYTES = 64 * 1024 * 1024


def _options(line: bytes) -> tuple[float, str, float]:
    tokens = line[1:].decode("ascii").upper().split()
    unit, representation, reference = "GHZ", "MA", 50.0
    seen: set[str] = set()
    while tokens:
        token = tokens.pop(0)
        if token in {"HZ", "KHZ", "MHZ", "GHZ"}:
            key, unit = "unit", token
        elif token in {"RI", "MA", "DB"}:
            key, representation = "format", token
        elif token == "S":
            key = "parameter"
        elif token == "R":
            key = "reference"
            if not tokens:
                raise ValueError("Touchstone 기준 저항 R 값이 없습니다.")
            reference = float(tokens.pop(0))
        else:
            raise ValueError(f"지원하지 않는 Touchstone 옵션입니다: {token} (S 파라미터만 지원)")
        if key in seen:
            raise ValueError("Touchstone 옵션이 중복되었습니다.")
        seen.add(key)
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError("Touchstone 기준 저항은 유한한 양수여야 합니다.")
    return {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[unit], representation, reference


def read_touchstone(path: str | Path, progress: Progress | None = None,
                    cancel: Cancelled | None = None) -> TouchstoneData:
    """Read 1.x S-parameters with bounded per-frequency storage; keep complex Zii in ohms."""
    path = Path(path)
    signature = source_signature(path)
    match = re.fullmatch(r"\.s([1-9]\d*)p", path.suffix, re.IGNORECASE)
    if not match:
        raise ValueError("Touchstone 1.x 파일의 확장자는 .sNp 형식이어야 합니다.")
    n = int(match.group(1))
    # shortcut: cap records at 1024 ports/64 MiB; raise limits after larger-network memory validation.
    if n > _MAX_PORTS:
        raise ValueError("Touchstone은 최대 1024포트까지 지원합니다.")
    required = 1 + 2 * n * n
    numbered: dict[int, str] = {}
    descriptions: list[str] = []
    frequencies: list[float] = []
    diagonals: list[np.ndarray] = []
    notes: list[str] = []
    chunks: list[bytes] = []
    count = 0
    record_bytes = 0
    options = None
    checkpoint(cancel)
    with path.open("rb") as stream:
        for lineno, raw in enumerate(iter(lambda: stream.readline(_MAX_RECORD_BYTES + 1), b""), 1):
            if len(raw) > _MAX_RECORD_BYTES:
                raise ValueError("Touchstone 한 행 또는 주파수 기록은 64MiB 이하여야 합니다.")
            if lineno % 256 == 0:
                checkpoint(cancel)
            content, _, comment = raw.partition(b"!")
            if comment:
                try:
                    text = comment.decode("utf-8").strip()
                except UnicodeDecodeError:
                    try:
                        text = comment.decode("cp949").strip()
                    except UnicodeDecodeError as exc:
                        raise ValueError("Touchstone 포트 주석을 UTF-8 또는 CP949로 읽을 수 없습니다.") from exc
                label = re.fullmatch(r"Port\[(\d+)\]\s*=\s*(.+)", text, re.IGNORECASE)
                if label:
                    index = int(label.group(1))
                    if index < 1 or index > n or index in numbered:
                        raise ValueError(f"Touchstone 포트 번호가 중복되었거나 범위를 벗어났습니다: {index}")
                    numbered[index] = label.group(2).strip()
                elif re.match(r"Port\d+[_\s]", text, re.IGNORECASE):
                    descriptions.append(text)
            content = content.strip()
            if not content:
                continue
            if content.startswith(b"["):
                raise ValueError("Touchstone 1.x만 지원합니다. 2.x 키워드 또는 혼합 모드 데이터는 지원하지 않습니다.")
            if content.startswith(b"#"):
                if options is not None or frequencies or chunks:
                    raise ValueError("Touchstone 옵션 행이 중복되었거나 데이터 뒤에 있습니다.")
                options = _options(content)
                continue
            if options is None:
                raise ValueError("Touchstone 데이터 앞에 # 옵션 행이 필요합니다.")
            chunks.append(content)
            record_bytes += len(content) + 1
            if record_bytes > _MAX_RECORD_BYTES:
                raise ValueError("Touchstone 한 주파수 기록은 64MiB 이하여야 합니다.")
            count += len(content.split())
            if count < required:
                continue
            if count != required:
                raise ValueError(f"Touchstone {lineno}행: 주파수별 데이터 개수가 포트 수와 일치하지 않습니다.")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    values = np.fromstring(b" ".join(chunks).decode("ascii"), sep=" ")
            except (ValueError, UnicodeError, Warning) as exc:
                raise ValueError(f"Touchstone {lineno}행: 숫자 형식이 잘못되었습니다.") from exc
            if values.size != required or not np.isfinite(values).all():
                raise ValueError(f"Touchstone {lineno}행: 잘못되었거나 유한하지 않은 숫자가 있습니다.")
            scale, representation, reference = options
            frequency = float(values[0] * scale)
            if not math.isfinite(frequency) or frequency < 0 or (frequencies and frequency <= frequencies[-1]):
                raise ValueError("Touchstone 주파수는 0 이상이고 중복 없이 증가해야 합니다.")
            first, second = values[1::2], values[2::2]
            with np.errstate(over="ignore", invalid="ignore"):
                if representation == "RI":
                    matrix = first + 1j * second
                else:
                    magnitude = np.power(10.0, first / 20) if representation == "DB" else first
                    if representation == "MA" and np.any(first < 0):
                        raise ValueError("Touchstone MA 크기는 0 이상이어야 합니다.")
                    matrix = magnitude * np.exp(1j * np.deg2rad(second))
            if not np.isfinite(matrix).all():
                raise ValueError("Touchstone 복소수 변환 결과가 유한하지 않습니다.")
            matrix = matrix.reshape(n, n)
            if n == 2:
                matrix = matrix.T  # Touchstone 1.x two-port order is S11, S21, S12, S22.
            checkpoint(cancel)
            identity = np.eye(n)
            try:
                z = reference * np.linalg.solve(identity - matrix, identity + matrix)
                diagonal = np.diag(z).copy()
                if not np.isfinite(z).all():
                    raise np.linalg.LinAlgError("non-finite result")
            except np.linalg.LinAlgError:
                diagonal = np.full(n, complex(np.nan, np.nan))
                notes.append(f"{frequency:g} Hz: 행렬이 특이하거나 계산 결과가 유한하지 않아 임피던스를 생략했습니다.")
            frequencies.append(frequency)
            diagonals.append(diagonal)
            chunks.clear()
            count = 0
            record_bytes = 0
            if progress:
                progress("Touchstone 읽기", stream.tell(), signature["size"])
        checkpoint(cancel)
    if chunks:
        raise ValueError("Touchstone 파일 끝에서 주파수 행렬이 잘렸습니다.")
    if not frequencies or options is None:
        raise ValueError("Touchstone 파일에 주파수 데이터가 없습니다.")
    if source_signature(path) != signature:
        raise ValueError("Touchstone 파일이 읽는 동안 변경되었습니다. 다시 불러오세요.")
    if descriptions and len(descriptions) != n:
        notes.append("설명용 포트 헤더 수가 포트 수와 달라 순서 연결을 생략했습니다.")
    ports = [Port(i, numbered.get(i, descriptions[i - 1] if len(descriptions) == n else f"Port {i}"),
                  descriptions[i - 1] if len(descriptions) == n else "") for i in range(1, n + 1)]
    return TouchstoneData(str(path), signature, ports, np.asarray(frequencies),
                          np.asarray(diagonals), options[2], notes)


def interpolate_impedance(data: TouchstoneData, port_index: int,
                          frequency_hz: float) -> tuple[float | None, bool]:
    """Return |Zii| in ohms and whether complex Z was linearly interpolated."""
    indices = {port.index: i for i, port in enumerate(data.ports)}
    if isinstance(port_index, bool) or not isinstance(port_index, int) or port_index not in indices:
        raise ValueError("선택한 Touchstone 포트 번호가 없습니다.")
    if not math.isfinite(frequency_hz) or frequency_hz < 0:
        raise ValueError("조회 주파수는 유한한 0 이상의 값이어야 합니다.")
    frequencies = data.frequency_hz
    i = int(np.searchsorted(frequencies, frequency_hz))
    column = indices[port_index]
    if i < len(frequencies) and frequencies[i] == frequency_hz:
        z = data.z_diagonal[i, column]
        return (float(abs(z)), False) if np.isfinite(z) else (None, False)
    if i == 0 or i == len(frequencies):
        return None, False
    left, right = data.z_diagonal[i - 1:i + 1, column]
    if not np.isfinite(left) or not np.isfinite(right):
        return None, False
    fraction = (frequency_hz - frequencies[i - 1]) / (frequencies[i] - frequencies[i - 1])
    return float(abs(left + fraction * (right - left))), True
