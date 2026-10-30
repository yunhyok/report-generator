from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .models import ChannelMapping, DcrRow, ReportConfig, ReportSettings, source_signature


def validate_config(config: ReportConfig) -> None:
    if not isinstance(config.dcr_source_path, str):
        raise ValueError("DCR 원본 파일 경로가 올바르지 않습니다.")
    if config.settings.board_type not in ("MLO", "PCB"):
        raise ValueError("보드 종류는 MLO 또는 PCB여야 합니다.")
    if not all(isinstance(v, str) for v in config.settings.ground_nets):
        raise ValueError("접지망 이름을 확인하세요.")
    for overrides in (config.model_overrides, config.component_overrides):
        for key, value in overrides.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("CAP 수정값은 0보다 큰 유한한 용량이어야 합니다.")
    seen = set()
    for row in config.dcr:
        if not row.site.strip() or not row.channel.strip():
            raise ValueError("DCR의 사이트와 채널을 입력하세요.")
        if isinstance(row.resistance_mohm, bool) or not isinstance(row.resistance_mohm, (int, float)) or not math.isfinite(row.resistance_mohm) or row.resistance_mohm < 0:
            raise ValueError("DCR은 0 이상의 유한한 숫자여야 합니다.")
        key = (row.site, row.channel)
        if key in seen:
            raise ValueError(f"DCR 대상이 중복됩니다: {row.site} / {row.channel}")
        seen.add(key)


def save_project(path: str | Path, config: ReportConfig, sources: dict[str, str], signatures: dict | None = None) -> None:
    validate_config(config)
    destination = Path(path).resolve()
    if any(destination == Path(p).resolve() for p in sources.values() if p):
        raise ValueError("입력 파일에 프로젝트를 덮어쓸 수 없습니다.")
    payload = {"version": 1, "sources": sources, "signatures": signatures or {}, "config": asdict(config)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_project(path: str | Path) -> tuple[ReportConfig, dict[str, str], dict, list[str]]:
    path = Path(path).resolve()
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("프로젝트 파일이 너무 큽니다 (최대 16MB).")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("지원하지 않는 프로젝트 버전입니다.")
        raw = data["config"]
        config = ReportConfig(
            settings=ReportSettings(**raw["settings"]),
            mappings=[ChannelMapping(**v) for v in raw["mappings"]],
            model_overrides=raw.get("model_overrides", {}),
            component_overrides=raw.get("component_overrides", {}),
            dcr=[DcrRow(**v) for v in raw.get("dcr", [])],
            dcr_source_path=raw.get("dcr_source_path", ""),
        )
        for row in config.mappings:
            if not all(isinstance(getattr(row, key), str) for key in ("source_name", "spd_port", "net", "site", "channel", "group", "priority", "status")):
                raise ValueError("채널 매핑 형식이 올바르지 않습니다.")
            if not all(isinstance(getattr(row, key), bool) for key in ("selected", "summary", "confirmed")):
                raise ValueError("채널 선택 상태가 올바르지 않습니다.")
            if row.port_index is not None and (type(row.port_index) is not int or row.port_index < 1):
                raise ValueError("포트 번호가 올바르지 않습니다.")
        validate_config(config)
        sources = data.get("sources", {})
        if not isinstance(sources, dict) or any(k not in ("spd", "touchstone") or not isinstance(v, str) for k, v in sources.items()):
            raise ValueError("입력 파일 참조가 올바르지 않습니다.")
        sources = {k: str((path.parent / v).resolve()) if v else "" for k, v in sources.items()}
        signatures = data.get("signatures", {})
        changed = []
        for key, source in sources.items():
            if source and (not Path(source).is_file() or signatures.get(key) != source_signature(source)):
                changed.append(key)
        if changed:
            for row in config.mappings:
                row.confirmed = False
                row.status = "원본 변경: 연결 재확인 필요"
        return config, sources, signatures, changed
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"프로젝트 파일 형식이 올바르지 않습니다: {exc}") from exc
