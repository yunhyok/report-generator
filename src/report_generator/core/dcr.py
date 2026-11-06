from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries

from .models import ChannelMapping, DcrRow


_EXCEL_ERROR = object()
_UNCACHED_FORMULA = object()


def natural_key(value: str) -> list:
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", value)]


def numeric_value(value, unit: str = "mΩ") -> float:
    if unit not in ("mΩ", "Ω", "µΩ"):
        raise ValueError("DCR 단위를 확인하세요.")
    if isinstance(value, bool) or value is None:
        raise ValueError("DCR 셀이 비어 있거나 숫자가 아닙니다.")
    text = str(value).strip()
    if "," in text and not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?(?:[eE][+-]?\d+)?", text):
        raise ValueError(f"DCR 숫자의 천 단위 쉼표를 확인하세요: {value}")
    try:
        number = float(text.replace(",", "")) * {"mΩ": 1, "Ω": 1000, "µΩ": 0.001}[unit]
    except ValueError as exc:
        raise ValueError(f"DCR 숫자를 확인하세요: {value}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("DCR은 0 이상의 유한한 숫자여야 합니다.")
    return number


def parse_column(text: str, unit: str = "mΩ") -> list[float]:
    lines = text.strip().splitlines()
    if not lines:
        raise ValueError("붙여넣을 값이 없습니다.")
    values = []
    for index, line in enumerate(lines, 1):
        if "\t" in line.strip():
            raise ValueError(f"{index}행: 한 열만 복사하세요.")
        values.append(numeric_value(line, unit))
    return values


def read_range(path: str | Path, sheet: str, cell_range: str, unit: str = "mΩ") -> list[float]:
    try:
        left, top, right, bottom = range_boundaries(cell_range)
    except (ValueError, TypeError) as exc:
        raise ValueError("A1:A10처럼 셀 범위를 지정하세요.") from exc
    if None in (left, top, right, bottom) or min(left, top) < 1 or right < left or bottom < top or (right-left+1)*(bottom-top+1) > 100_000:
        raise ValueError("범위는 100,000셀 이하의 유효한 영역이어야 합니다.")
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in book.sheetnames:
            raise ValueError("시트 이름을 확인하세요.")
        values = []
        for row_index, row in enumerate(book[sheet].iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right), top):
            for column_index, cell in enumerate(row, left):
                try:
                    values.append(numeric_value(cell.value, unit))
                except ValueError as exc:
                    raise ValueError(f"{row_index}행 {column_index}열: {exc}") from exc
        if len(values) != (right-left+1)*(bottom-top+1):
            raise ValueError("선택한 범위에 빈 셀이 있습니다.")
        return values
    finally:
        book.close()


def ordered_targets(mappings: list[ChannelMapping], order: str = "site") -> list[tuple[str, str]]:
    keys = {(m.site.strip(), m.channel.strip()) for m in mappings if m.selected and m.site.strip() and m.channel.strip()}
    return sorted(keys, key=lambda key: (natural_key(key[0]), natural_key(key[1])) if order == "site" else (natural_key(key[1]), natural_key(key[0])))


def assign_column(values: list[float], mappings: list[ChannelMapping], order: str = "site") -> list[DcrRow]:
    keys = ordered_targets(mappings, order)
    if not keys:
        raise ValueError("채널·그룹에서 사이트와 채널을 먼저 추가하세요. DCR만 입력할 수도 있습니다.")
    if len(values) != len(keys):
        raise ValueError(f"입력 {len(values)}개, 선택 대상 {len(keys)}개입니다. 순서와 행 수를 확인하세요.")
    return [DcrRow(site, channel, value) for (site, channel), value in zip(keys, values)]


def _table_value(value, row: int, column: int):
    if value is _EXCEL_ERROR:
        raise ValueError(f"{row}행 {column}열: Excel 오류 대신 값을 입력하세요.")
    if value is _UNCACHED_FORMULA:
        raise ValueError(f"{row}행 {column}열: Excel 수식의 계산 결과가 없습니다. Excel에서 다시 계산하고 저장하세요.")
    return value


def _identity(value, row: int, column: int) -> str:
    value = _table_value(value, row, column)
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        if value == int(value):
            return str(int(value))
    if isinstance(value, str) and not re.search(r"[\x00-\x09\x0b-\x1f]", value.strip()) and len(value) <= 32767:
        return value.strip()
    raise ValueError(f"{row}행 {column}열: 사이트·채널 이름은 텍스트 또는 정수로 입력하세요.")


def _unit(value: str) -> str:
    aliases = {"Ω": "Ω", "Ω": "Ω", "ohm": "Ω", "mΩ": "mΩ", "mΩ": "mΩ", "mohm": "mΩ",
               "µΩ": "µΩ", "μΩ": "µΩ", "uΩ": "µΩ", "µΩ": "µΩ", "uohm": "µΩ"}
    try:
        return aliases[value.strip()]
    except KeyError as exc:
        raise ValueError(f"DCR 단위를 확인하세요: {value}. mΩ, Ω, µΩ만 지원합니다.") from exc


def _site_alias(value: str) -> str:
    match = re.fullmatch(r"(?:site)?(\d+)", value, re.IGNORECASE)
    return str(int(match[1])) if match else value.casefold()


def _header(value) -> tuple[str | None, str | None]:
    # Header discovery does not evaluate unrelated spreadsheet formulas.
    value = getattr(value, "value", value)
    if not isinstance(value, str):
        return None, None
    match = re.fullmatch(r"\s*(.*?)\s*(?:/\s*)?[\[(]([^\])]+)[\])]\s*", value)
    label = match[1].rstrip(" /:") if match else value
    label = re.sub(r"[\s_-]", "", label).casefold()
    fields = {"site": "site", "사이트": "site", "channel": "channel", "channelname": "channel", "name": "channel",
              "채널": "channel", "채널명": "channel", "dcr": "dcr", "dcresistance": "dcr",
              "저항": "dcr", "저항값": "dcr", "직류저항": "dcr", "dcr값": "dcr", "dcrirdrop": "dcr"}
    field = fields.get(label)
    return field, _unit(match[2]) if match and field in {"channel", "dcr"} else None


def _resistance(value, default_unit: str, header_unit: str | None, row: int, column: int) -> float:
    value = _table_value(value, row, column)
    if isinstance(value, str) and not value.strip():
        value = None
    cell_unit = None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*([+-]?(?:[\d,]+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s].*?)?\s*", value)
        if match and match[2]:
            value, cell_unit = match[1], _unit(match[2])
    if cell_unit and header_unit and cell_unit != header_unit:
        raise ValueError(f"{row}행 {column}열: 셀 단위와 DCR 제목 단위가 다릅니다.")
    try:
        return numeric_value(value, cell_unit or header_unit or default_unit)
    except ValueError as exc:
        raise ValueError(f"{row}행 {column}열: {exc}") from exc


def _parse_dcr_rows(table, mappings: list[ChannelMapping], unit: str) -> list[DcrRow]:
    unit = _unit(unit)
    targets = {}
    aliases = {}
    site_forms = {}
    for mapping in mappings:
        if not mapping.selected:
            continue
        site, channel = str(mapping.site).strip(), mapping.channel.strip()
        if not site or not channel:
            raise ValueError("선택한 매핑의 사이트와 채널을 모두 입력하세요.")
        key = (site.casefold(), channel.casefold())
        if key in targets and targets[key] != (site, channel):
            raise ValueError(f"선택한 매핑의 채널·사이트가 중복됩니다: {channel} / {site}")
        targets[key] = (site, channel)
        site_forms.setdefault(_site_alias(site), set()).add(site.casefold())
        for label in {channel, mapping.display_name.strip() or channel}:
            aliases.setdefault((site.casefold(), label.casefold()), set()).add(key)
    if mappings and not targets:
        raise ValueError("DCR을 가져올 채널·사이트를 하나 이상 선택하세요.")

    header_index = None
    fields = {}
    site_columns = {}
    header_unit = None
    for index, row in enumerate(table):
        found = {}
        for column, value in enumerate(row):
            field, field_unit = _header(value)
            if field:
                found.setdefault(field, []).append((column, field_unit))
        row_table = {"site", "channel", "dcr"} <= found.keys()
        matrix = "channel" in found and "site" not in found and "dcr" not in found
        if not row_table and not matrix:
            continue
        if any(len(columns) > 1 for columns in found.values()):
            raise ValueError(f"{index + 1}행: 사이트·채널·DCR 제목이 중복되었습니다.")
        fields = {field: columns[0][0] for field, columns in found.items()}
        if row_table:
            header_unit = found["dcr"][0][1]
        else:
            channel_column = fields["channel"]
            header_unit = found["channel"][0][1]
            seen_sites = set()
            for column in range(channel_column + 1, len(row)):
                site = _identity(row[column], index + 1, column + 1)
                if not site:
                    continue
                if site.casefold() in seen_sites:
                    raise ValueError(f"{index + 1}행: 사이트 제목이 중복되었습니다: {site}")
                seen_sites.add(site.casefold())
                site_columns[column] = site
            if not site_columns:
                continue
        header_index = index
        break
    if header_index is None:
        raise ValueError("사이트(Site), 채널(Channel), DCR 제목 또는 Channel × 사이트 표가 필요합니다.")

    family_aliases = {}
    header = table[header_index]
    if ("dcr" in fields and str(header[fields["channel"]]).strip().casefold() == "name"
            and re.match(r"\s*dcr[\s_-]*ir[\s_-]*drop\b", str(header[fields["dcr"]]), re.IGNORECASE)):
        for key, (site, channel) in targets.items():
            match = re.fullmatch(r"(?:LGA|POGO|BGA|DIE|LOAD|SOURCE)\d+[_:-]+(.+)", channel, re.IGNORECASE)
            if match:
                family_aliases.setdefault((site.casefold(), match[1].casefold()), set()).add(key)

    result = []
    seen = set()
    exact_sites = {site for site, _ in targets}

    def add(site, channel, value, row, column):
        if site_columns and "\n" in channel:
            if not targets:
                raise ValueError(f"{row}행: 여러 줄의 채널 표시명은 원본 채널을 확인할 선택 매핑이 필요합니다.")
            labels = {}
            for line in channel.splitlines():
                line_site, separator, label = line.partition(":")
                if not separator or not label.strip() or line_site.strip().casefold() in labels:
                    raise ValueError(f"{row}행: 사이트별 채널 표시명을 확인하세요.")
                labels[line_site.strip().casefold()] = label.strip()
            if site.casefold() not in labels:
                raise ValueError(f"{row}행: {site}의 채널 표시명이 누락되었습니다.")
            channel = labels[site.casefold()]
        if targets:
            if site.casefold() not in exact_sites:
                forms = site_forms.get(_site_alias(site), set())
                if len(forms) > 1:
                    raise ValueError(f"{row}행: 사이트 이름이 모호합니다: {site}. 원본 사이트 이름을 정확히 입력하세요.")
                if forms:
                    site = next(iter(forms))
            key = (site.casefold(), channel.casefold())
            matches = aliases.get(key, set()) | family_aliases.get(key, set())
            if not site_columns and key in targets:
                matches = {key}
            if len(matches) > 1:
                raise ValueError(f"{row}행: 채널 표시명이 모호합니다: {channel} / {site}")
            if not matches:
                raise ValueError(f"{row}행: 선택된 매핑에 없는 채널·사이트입니다: {channel} / {site}")
            key = next(iter(matches))
            site, channel = targets[key]
        else:
            key = (site.casefold(), channel.casefold())
        if not site or not channel:
            raise ValueError(f"{row}행: 사이트와 채널을 모두 입력하세요.")
        if key in seen:
            raise ValueError(f"{row}행: 채널·사이트의 DCR이 중복되었습니다: {channel} / {site}")
        seen.add(key)
        result.append(DcrRow(site, channel, _resistance(value, unit, header_unit, row, column)))
        return channel.casefold()

    for index in range(header_index + 1, len(table)):
        row = table[index]
        cell = lambda column: row[column] if column < len(row) else None
        channel = _identity(cell(fields["channel"]), index + 1, fields["channel"] + 1)
        if "dcr" in fields:
            site = _identity(cell(fields["site"]), index + 1, fields["site"] + 1)
            value = cell(fields["dcr"])
            if not site and not channel and _table_value(value, index + 1, fields["dcr"] + 1) in (None, ""):
                continue
            add(site, channel, value, index + 1, fields["dcr"] + 1)
        else:
            channels = set()
            for column in range(fields["channel"] + 1, len(row)):
                if column not in site_columns and _table_value(cell(column), index + 1, column + 1) not in (None, ""):
                    raise ValueError(f"{index + 1}행 {column + 1}열: 사이트 제목이 없는 값이 있습니다.")
            for column, site in site_columns.items():
                value = cell(column)
                if _table_value(value, index + 1, column + 1) in (None, ""):
                    continue
                channels.add(add(site, channel, value, index + 1, column + 1))
            if len(channels) > 1:
                raise ValueError(f"{index + 1}행: 표시명이 여러 원본 채널에 연결되어 모호합니다.")
            if channel and not channels:
                raise ValueError(f"{index + 1}행: 채널의 DCR 값이 모두 비어 있습니다.")
    if not result:
        raise ValueError("가져올 DCR 값이 없습니다.")
    missing = targets.keys() - seen
    if missing:
        examples = ", ".join(f"{targets[key][1]} / {targets[key][0]}" for key in sorted(missing)[:5])
        raise ValueError(f"선택 대상 DCR {len(missing)}개가 누락되었습니다: {examples}")
    if targets:
        lookup = {(row.site, row.channel): row for row in result}
        result = [lookup[key] for key in ordered_targets(mappings)]
    return result


def _bounded_rows(rows):
    table = []
    count = 0
    for number, row in enumerate(rows, 1):
        count += len(row)
        if number > 100_000 or count > 2_000_000:
            raise ValueError("DCR 표가 너무 큽니다. 불필요한 행·열을 삭제하세요.")
        table.append([_EXCEL_ERROR if getattr(value, "data_type", None) == "e" else getattr(value, "value", value)
                      for value in row])
    return table


def parse_dcr_table(text: str, mappings: list[ChannelMapping], unit: str = "mΩ") -> list[DcrRow]:
    """Parse an Excel TSV clipboard table using explicit site/channel keys."""
    try:
        table = _bounded_rows(csv.reader(io.StringIO(text), delimiter="\t", strict=True))
    except csv.Error as exc:
        raise ValueError("붙여넣은 표의 따옴표와 열 구분을 확인하세요.") from exc
    return _parse_dcr_rows(table, mappings, unit)


def read_dcr_sheet(path: str | Path, sheet: str, mappings: list[ChannelMapping], unit: str = "mΩ") -> list[DcrRow]:
    """Read a complete Excel worksheet without relying on its sort order."""
    try:
        book = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("DCR Excel 파일을 열 수 없습니다. 경로와 파일 형식을 확인하세요.") from exc
    formulas = None
    try:
        if sheet not in book.sheetnames:
            raise ValueError("시트 이름을 확인하세요.")
        worksheet = book[sheet]
        worksheet.reset_dimensions()
        formulas = load_workbook(path, read_only=True, data_only=False)
        formula_sheet = formulas[sheet]
        formula_sheet.reset_dimensions()
        rows = ([
            _UNCACHED_FORMULA if formula.data_type == "f" and cell.value is None else cell
            for cell, formula in zip(cached, original, strict=True)
        ] for cached, original in zip(worksheet.iter_rows(), formula_sheet.iter_rows(), strict=True))
        return _parse_dcr_rows(_bounded_rows(rows), mappings, unit)
    finally:
        if formulas is not None:
            formulas.close()
        book.close()
