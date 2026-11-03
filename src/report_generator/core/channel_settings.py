from __future__ import annotations

import math
import os
from pathlib import Path
import re
import tempfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .mapping import priority_value
from .models import ChannelMapping


def _path(path: str | Path) -> Path:
    path = Path(path)
    if path.suffix.casefold() != ".xlsx":
        raise ValueError("채널 설정 파일은 .xlsx 형식으로 선택하세요.")
    return path


def _pairs(mappings: list[ChannelMapping]) -> dict[tuple[str, str], tuple[str, str]]:
    pairs = {}
    for row in mappings:
        channel, site = row.channel.strip(), str(row.site).strip()
        if row.selected and channel and site:
            pairs.setdefault((channel.casefold(), site.casefold()), (channel, site))
    return pairs


def write_channel_template(path: str | Path, mappings: list[ChannelMapping]) -> None:
    """Export selected complete pairs, leaving optional settings blank."""
    destination = _path(path)
    pairs = _pairs(mappings)
    if not pairs:
        raise ValueError("채널과 사이트가 입력된 행을 하나 이상 선택하세요.")
    if len(pairs) >= 100_000:
        raise ValueError("채널 설정 양식은 제목 포함 100,000행 이내로 저장하세요. 선택한 행을 나누어 내보내세요.")
    if any(len(value) > 32767 or re.search(r"[\x00-\x1f]", value)
           for pair in pairs.values() for value in pair):
        raise ValueError("채널명과 사이트는 제어 문자 없이 32,767자 이내로 입력하세요.")
    book = Workbook()
    sheet = book.active
    sheet.title = "채널 설정"
    sheet.append(["채널명", "사이트", "priority", "group", "net"])
    for number, pair in enumerate(pairs.values(), start=2):
        for column, value in enumerate(pair, start=1):
            cell = sheet.cell(number, column, value)
            cell.data_type = "s"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="202226")
        cell.fill = PatternFill("solid", fgColor="E8EBEF")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    channel_width = min(80, max(36, max(len(channel) for channel, _ in pairs.values()) + 6))
    for column, width in {"A": channel_width, "B": 20, "C": 22, "D": 28, "E": 36}.items():
        sheet.column_dimensions[column].width = width
    sheet.column_dimensions["D"].number_format = "@"  # Keep plot names such as 1-2 as text in Excel.
    sheet.column_dimensions["E"].number_format = "@"
    sheet.row_dimensions[1].height = 24
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:E{len(pairs) + 1}"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
        book.save(temporary)
        os.replace(temporary, destination)
    except Exception as exc:
        raise ValueError(f"채널 설정 양식을 저장할 수 없습니다: {destination}\n"
                         "Excel에서 파일을 닫고 저장 폴더와 쓰기 권한을 확인하세요.") from exc
    finally:
        book.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _cell_text(cell, field: str, row_number: int) -> str:
    value = cell.value
    if cell.data_type == "f":
        raise ValueError(f"Excel {row_number}행 {field}: 수식 대신 값을 직접 입력하세요.")
    if value is None:
        return ""
    if cell.data_type != "e" and isinstance(value, str):
        value = value.strip()
        if len(value) <= 32767 and not re.search(r"[\x00-\x1f]", value):
            return value
    elif not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value):
        if value == int(value):
            return str(int(value))
        if field in {"group", "net"}:
            return str(value)
    raise ValueError(f"Excel {row_number}행 {field}: 텍스트 또는 올바른 정수 값을 입력하세요.")


def read_channel_settings(
    path: str | Path, mappings: list[ChannelMapping],
) -> tuple[dict[str, str], dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """Validate the entire first sheet and return sparse priority/group/net updates."""
    source = _path(path)
    try:
        book = load_workbook(source, read_only=True, data_only=False)
    except Exception as exc:
        raise ValueError(f"채널 설정 파일을 열 수 없습니다: {source}\n"
                         "파일 경로와 .xlsx 형식을 확인하세요.") from exc
    try:
        if not book.worksheets:
            raise ValueError("채널 설정 파일에 워크시트가 없습니다.")
        sheet = book.worksheets[0]
        if sheet.max_row and sheet.max_row > 100_000:
            raise ValueError("채널 설정 시트가 너무 큽니다. 불필요한 행·열을 삭제하거나 새 양식에 값을 복사하세요.")
        # Third-party writers may omit or understate dimensions; stream the actual cells.
        sheet.reset_dimensions()
        aliases = {"채널명": "channel", "channel": "channel", "사이트": "site", "site": "site",
                   "priority": "priority", "group": "group", "net": "net"}
        columns = {}
        for cell in next(sheet.iter_rows(min_row=1, max_row=1), ()):
            header = str(cell.value or "").strip().casefold()
            field = aliases.get(header)
            if field:
                if field in columns:
                    raise ValueError(f"Excel 1행: {field} 열이 중복되었습니다. 제목을 하나만 남기세요.")
                columns[field] = cell.column
        if not {"channel", "site"} <= columns.keys():
            raise ValueError("Excel 1행에 채널명(channel), 사이트(site) 제목이 필요합니다.")
        if not {"priority", "group", "net"} & columns.keys():
            raise ValueError("Excel 1행에 priority, group, net 중 하나 이상의 제목이 필요합니다.")
        # shortcut: unusually large/sparse sheets are rejected; use a dedicated settings sheet.
        last_column = max(columns.values())
        targets = _pairs(mappings)
        priorities: dict[str, str] = {}
        groups: dict[tuple[str, str], str] = {}
        nets: dict[tuple[str, str], str] = {}
        seen_pairs: dict[tuple[str, str], int] = {}
        priority_rows: dict[str, int] = {}
        for number, row in enumerate(sheet.iter_rows(min_row=2, max_col=last_column), start=2):
            if number > 100_000 or number * last_column > 2_000_000:
                raise ValueError("채널 설정 시트가 너무 큽니다. 불필요한 행·열을 삭제하거나 새 양식에 값을 복사하세요.")
            values = {field: _cell_text(row[column - 1], field, number)
                      for field, column in columns.items()}
            priority, group, net = (values.get(field, "") for field in ("priority", "group", "net"))
            if not priority and not group and not net:
                continue
            key = (values["channel"].casefold(), values["site"].casefold())
            if not all(key):
                raise ValueError(f"Excel {number}행: 값을 적용할 채널명과 사이트를 모두 입력하세요.")
            if key not in targets:
                raise ValueError(f"Excel {number}행: 선택된 매핑에 없는 채널·사이트입니다: "
                                 f"{values['channel']} / {values['site']}")
            if key in seen_pairs:
                raise ValueError(f"Excel {seen_pairs[key]}, {number}행: 같은 채널·사이트의 설정이 중복되었습니다.")
            seen_pairs[key] = number
            if priority:
                try:
                    priority = str(priority_value(priority))
                except ValueError as exc:
                    raise ValueError(f"Excel {number}행 priority: {exc}") from exc
                if key[0] in priorities and priorities[key[0]] != priority:
                    raise ValueError(f"Excel {priority_rows[key[0]]}, {number}행: "
                                     "같은 채널의 priority가 다릅니다. 사이트별 값을 하나로 맞추세요.")
                priorities[key[0]] = priority
                priority_rows.setdefault(key[0], number)
            if group:
                groups[key] = group
            if net:
                nets[key] = net
        if not priorities and not groups and not nets:
            raise ValueError("적용할 priority, group, net 값이 없습니다. 빈 셀은 기존 설정을 유지합니다.")
        return priorities, groups, nets
    finally:
        book.close()
