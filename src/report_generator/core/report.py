from __future__ import annotations

from math import isfinite
import os
from pathlib import Path
import re
import tempfile

import numpy as np
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Color, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.views import Selection

from .models import ReportConfig, ReportResult, SpdData, TouchstoneData, checkpoint
from .render import mapped_nets, placement_components, render_group_plot, render_placement
from .capacitance import component_active, summarize_caps
from .touchstone import interpolate_impedance


FREQUENCIES = (1e5, 1e6, 1e7, 1e8, 1e9)
FREQUENCY_LABELS = ("100kHz", "1MHz", "10MHz", "100MHz", "1GHz")
WHITE = PatternFill("solid", fgColor=Color(theme=0))
HEADER = PatternFill("solid", fgColor=Color(theme=0, tint=-0.0499893185216834))
CHANNEL_HEADERS = [PatternFill("solid", fgColor=color)
                   for color in ("E2EFDA", "D9E2F3", "FFF2CC", "EDEDED")]
LINE = Side(style="thin", color=Color(indexed=64))
NUMBER = "0.000_ "
TITLES = {
    "100": "Power Impedance @ 100MHz", "pi": "Power Impedance", "dcr": "DC Resistance",
    "cap": "The Number of De-coupling Capacitors",
    "placement": "The De-coupling Capacitor Distribution Layout",
}


def _font(size=11, bold=False):
    return Font(name="맑은 고딕", charset=129, family=3, scheme="minor", size=size,
                bold=bold, italic=False, color=Color(theme=1))


def _site_value(site):
    match = re.fullmatch(r"(?:SITE)?(\d+)", str(site), re.IGNORECASE)
    return int(match[1]) if match else site


def _priority(value):
    text = str(value).strip()
    return int(text) if re.fullmatch(r"[+-]?\d+", text) else text or None


def _priorities(config):
    return {(m.channel, m.site): _priority(m.priority) for m in _mappings(config)}


def _widths(ws, column, widths):
    for offset, width in enumerate(widths):
        ws.column_dimensions[get_column_letter(column + offset)].width = width


def _label_width(labels, minimum):
    return min(255, max(minimum, max((len(str(label)) * 1.1 for label in labels), default=minimum)))


def _mappings(config, ts=None, summary=False):
    return [m for m in config.mappings if m.selected and m.confirmed and
            (not summary or m.summary) and
            (ts is None or (m.port_index is not None and 1 <= m.port_index <= len(ts.ports)))]


def _spot_rows(config, ts, summary=False):
    if ts is None:
        return []
    rows = []
    for m in _mappings(config, ts, summary):
        values, interpolated = [], []
        for frequency in FREQUENCIES:
            value, interp = interpolate_impedance(ts, m.port_index, frequency)
            values.append(value)
            interpolated.append(interp)
        rows.append((m, values, interpolated))
    return rows


def _safe_sheet_name(label, used):
    label = re.sub(r"[\\/*?:\[\]\x00-\x1f]", "_", str(label)).strip("'") or "Group"
    base = label[:31]
    candidate, number = base, 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        candidate = base[:31 - len(suffix)] + suffix
        number += 1
    used.add(candidate.casefold())
    return candidate


def _plan(config, spd, ts):
    pages, omitted, used = [], {}, set()
    spots = _spot_rows(config, ts, summary=True)
    def add(kind, label, payload=None):
        pages.append((kind, _safe_sheet_name(label, used), payload))
    if any(values[3] is not None for _, values, _ in spots):
        add("100", "PI ALL@100MHz", spots)
    else:
        omitted["PI ALL@100MHz"] = "선택한 포트에 유효한 100MHz 값이 없습니다."
    if any(any(value is not None for value in values) for _, values, _ in spots):
        add("pi", "PI ALL", spots)
    else:
        omitted["PI ALL"] = "선택한 포트에 5개 기준 주파수의 유효한 값이 없습니다."
    if config.dcr:
        add("dcr", "DCR", config.dcr)
    else:
        omitted["DCR"] = "DCR 입력이 없습니다."
    caps = summarize_caps(spd, config) if spd is not None else []
    if caps:
        add("cap", "CAP_수량", caps)
    else:
        omitted["CAP_수량"] = "선택한 채널/사이트의 활성 커패시터 수량이 없습니다."
    sides = []
    if spd is not None:
        for side in (("Top", "Bottom") if config.settings.board_type == "PCB" else ("Top",)):
            if placement_components(spd, config, side):
                sides.append(side)
    if sides:
        add("placement", "CAP_배치도면", sides)
    else:
        omitted["CAP_배치도면"] = "표시할 부품 좌표가 없습니다."
    groups = list(dict.fromkeys(m.group for m in _mappings(config, ts) if m.group)) if ts else []
    for group in groups:
        maps = [m for m in _mappings(config, ts) if m.group == group]
        valid = any(np.any(np.isfinite(np.abs(ts.z_diagonal[:, m.port_index - 1])) &
                           (np.abs(ts.z_diagonal[:, m.port_index - 1]) > 0) &
                           np.isfinite(ts.frequency_hz) & (ts.frequency_hz >= 1e3) & (ts.frequency_hz <= 1e9)) for m in maps)
        if valid:
            add("group", group, group)
        else:
            omitted[f"그룹: {group}"] = "1kHz~1GHz 범위에 유효한 임피던스가 없습니다."
    return pages, omitted


def plan_pages(config: ReportConfig, spd: SpdData | None = None,
               ts: TouchstoneData | None = None) -> tuple[list[str], dict[str, str]]:
    pages, omitted = _plan(config, spd, ts)
    return [name for _, name, _ in pages], omitted


def _put(ws, row, column, value, *, header=False, number_format=None, bold=None, size=11, fill=None):
    cell = ws.cell(row, column)
    if isinstance(value, str):
        # Explicit string type prevents spreadsheet formulas from source names.
        cell.value = ILLEGAL_CHARACTERS_RE.sub("", value)[:32767]
        cell.data_type = "s"
    else:
        cell.value = value
    cell.font = _font(size, header if bold is None else bold)
    cell.fill = fill if fill is not None else HEADER if header else WHITE
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = Border(left=LINE, right=LINE, top=LINE, bottom=LINE)
    if number_format:
        cell.number_format = number_format
    return cell


def _table(ws, headers, rows, row=4, column=2, number_columns=()):
    for i, label in enumerate(headers):
        _put(ws, row, column + i, label, header=True)
    for offset, values in enumerate(rows, row + 1):
        for i, value in enumerate(values):
            _put(ws, offset, column + i, value,
                 number_format=NUMBER if i in number_columns else None)


def _base_sheet(wb, name, config, kind, ts=None):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_view.selection = [Selection(activeCell="B4", sqref="B4")]
    ws.sheet_format.defaultRowHeight = 16.5
    ws.sheet_format.defaultColWidth = 9
    ws.column_dimensions["A"].width = 9
    custom_title = config.settings.title.strip()
    page_title = TITLES.get(kind, "")
    if custom_title and custom_title != "Power Integrity Report":
        page_title = f"{custom_title} · {page_title}" if page_title else custom_title
    if page_title:
        title = ws.cell(1, 1, ILLEGAL_CHARACTERS_RE.sub("", page_title)[:32767])
        title.data_type = "s"
        title.font = _font(24, True)
        title.fill = WHITE
        title.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 38.25
    if kind in {"100", "pi"} and ts is not None:
        def frequency_label(value):
            for scale, suffix in ((1e9, "GHz"), (1e6, "MHz"), (1e3, "kHz")):
                if value >= scale:
                    return f"{value / scale:g}{suffix}"
            return f"{value:g}Hz"
        sweep = ts.frequency_hz[ts.frequency_hz > 0]
        lower, upper = (sweep[0], sweep[-1]) if len(sweep) else (0, 0)
        _note(ws, 2, f"* {frequency_label(lower)} ~ {frequency_label(upper)} : Automatic Frequency Sweep", 1)
    metadata = " · ".join(str(v) for v in [config.settings.product, config.settings.board_type,
                         config.settings.report_date, config.settings.revision] if v)
    _note(ws, 3, metadata, 1)
    return ws


def _note(ws, row, text, column=2):
    cell = ws.cell(row, column, ILLEGAL_CHARACTERS_RE.sub("", text)[:32767])
    cell.data_type = "s"
    cell.font = _font()
    cell.fill = WHITE
    cell.alignment = Alignment(vertical="center")


def _add_image(ws, path, anchor, max_width=900):
    image = Image(path)
    ratio = min(1, max_width / image.width)
    image.width *= ratio
    image.height *= ratio
    ws.add_image(image, anchor)
    return 5 + int(image.height / 20)


def _write_100(ws, spots, factor, unit):
    sites = list(dict.fromkeys(m.site for m, _, _ in spots))
    channels = list(dict.fromkeys(m.channel for m, _, _ in spots))
    small = len(sites) <= 8
    lookup = {(m.channel, m.site): values[3] * factor for m, values, _ in spots if values[3] is not None}
    priorities = {m.channel: _priority(m.priority) for m, _, _ in spots}
    rows = []
    for channel in channels:
        values = [lookup.get((channel, site)) for site in sites]
        available = [v for v in values if v is not None]
        maximum = max(available) if available else None
        average = sum(available) / len(available) if available else None
        deviation = (maximum - average) / average if average else None
        rows.append([priorities[channel], channel, *values, deviation, maximum, average] if small
                    else [channel, maximum, average, deviation, *values])
    if small:
        headers = ["Priority", f"Channel / [{unit}]", *sites, "편차", "MAX.", "AVG."]
        _widths(ws, 2, [9, _label_width(channels, 36.75), *([9] * (len(sites) + 3))])
        deviation_column, site_column = 4 + len(sites), 4
        number_columns = range(2, len(headers))
    else:
        headers = [f"Channel / [{unit}]", "MAX.", "AVG.", "편차", *sites]
        numeric_width = 6.875 if unit == "Ω" else 9
        _widths(ws, 2, [_label_width(channels, 16.125), numeric_width, numeric_width,
                        7.75 if unit == "Ω" else 6.75, *([numeric_width] * len(sites))])
        deviation_column, site_column = 5, 6
        number_columns = range(1, len(headers))
    _table(ws, headers, rows, number_columns=number_columns)
    for r in range(5, len(rows) + 5):
        ws.cell(r, deviation_column).number_format = "0.0%"
        if not small:
            ws.cell(r, 2).font = _font(bold=True)
    flags = {(m.channel, m.site): interpolated[3] for m, _, interpolated in spots}
    for r, channel in enumerate(channels, 5):
        for c, site in enumerate(sites, site_column):
            if flags.get((channel, site)):
                ws.cell(r, c).number_format = '0.000"*"'
    _note(ws, len(rows) + 7, "편차 = (MAX − AVG) / AVG. 빈 셀은 주파수 범위 밖이거나 변환에 실패한 값입니다.")
    _note(ws, len(rows) + 8, "* 복소 Z 선형 보간값. MAX·AVG·편차는 표시된 유효 값으로 계산합니다.")


def _merged(ws, row, column, end_row, end_column, value, *, header=False, fill=None, number_format=None):
    for r in range(row, end_row + 1):
        for c in range(column, end_column + 1):
            _put(ws, r, c, value if (r, c) == (row, column) else None,
                 header=header, fill=fill, number_format=number_format)
    if end_row > row or end_column > column:
        ws.merge_cells(start_row=row, start_column=column, end_row=end_row, end_column=end_column)


def _pi_values(ws, row, column, values, flags, factor, number_format, fill=None):
    for index, (value, interpolated) in enumerate(zip(values, flags)):
        fmt = number_format[:-2] + '"*"' if interpolated else number_format
        _put(ws, row, column + index, value * factor if value is not None else None,
             number_format=fmt, bold=index == 3, fill=fill)


def _deviation(spots):
    values = [values[3] for _, values, _ in spots if values[3] is not None]
    average = sum(values) / len(values) if values else 0
    return (max(values) - average) / average if average else None


def _write_pi(ws, spots, factor, unit, column=2, *, wide=False, detail=False, channel_order=()):
    channels = list(dict.fromkeys(m.channel for m, _, _ in spots))
    by_channel = {channel: [spot for spot in spots if spot[0].channel == channel] for channel in channels}
    number_format = "0.0_ " if wide and unit == "mΩ" else NUMBER
    if not wide or (detail and unit == "mΩ"):
        priority = not wide
        headers = (["Priority"] if priority else []) + [f"Channel / [{unit}]", "SITE", *FREQUENCY_LABELS]
        if not detail:
            headers.append("편차")
        _table(ws, headers, [], column=column)
        if wide and detail:
            color_index = channel_order.index(channels[0]) if channels[0] in channel_order else 0
            for c in range(column, column + len(headers)):
                ws.cell(4, c).fill = CHANNEL_HEADERS[color_index % len(CHANNEL_HEADERS)]
        channel_column = column + priority
        site_column = channel_column + 1
        if detail and wide:
            widths = [_label_width(channels, 16.125), 7.5, 8.5, 7.5, 8.125, 9.625, 9.5]
        else:
            widths = ([9] if priority else []) + [_label_width(channels, 16.125 if detail else 30.5), 9]
            widths += [9] * 5 if detail else [9.5, 9.125, 9.125, 9.125, 9.125, 9]
        _widths(ws, column, widths)
        row = 5
        for index, channel in enumerate(channels):
            entries = by_channel[channel]
            fill = HEADER if not detail and index % 2 else WHITE
            for mapping, values, flags in entries:
                if priority:
                    _put(ws, row, column, _priority(mapping.priority), fill=fill)
                _put(ws, row, channel_column, channel, fill=fill)
                _put(ws, row, site_column, _site_value(mapping.site), fill=fill)
                _pi_values(ws, row, site_column + 1, values, flags, factor, number_format, fill)
                row += 1
            if not detail:
                _merged(ws, row - len(entries), site_column + 6, row - 1, site_column + 6,
                        _deviation(entries), fill=fill, number_format="0.0%")
    else:
        across = 1 if detail else 4
        block_width = 6 if detail else 7
        row = 4
        for band_start in range(0, len(channels), across):
            band = channels[band_start:band_start + across]
            band_height = max(len(by_channel[channel]) for channel in band)
            for offset, channel in enumerate(band):
                start = column + offset * block_width
                color_index = channel_order.index(channel) if channel in channel_order else band_start + offset
                fill = CHANNEL_HEADERS[color_index % len(CHANNEL_HEADERS)]
                _merged(ws, row, start, row, start + block_width - 1, f"{channel} / [{unit}]", header=True, fill=fill)
                for index, label in enumerate(["SITE", *FREQUENCY_LABELS] + ([] if detail else ["편차"])):
                    _put(ws, row + 1, start + index, label, header=True, fill=fill)
                widths = [9] * block_width if unit == "Ω" else (
                    [5.5, 8.5, 7, 8.125, 9.25, 7.875, 5.75],
                    [5.5, 9, 7.875, 8.125, 9.625, 10, 5.75],
                    [5.5, 9, 7.875, 8.125, 9.625, 10, 6.75],
                    [5.5, 9, 7.875, 8.125, 9.25, 9, 5.75],
                )[offset][:block_width]
                _widths(ws, start, widths)
                for data_row, (mapping, values, flags) in enumerate(by_channel[channel], row + 2):
                    _put(ws, data_row, start, _site_value(mapping.site))
                    _pi_values(ws, data_row, start + 1, values, flags, factor, number_format)
                if not detail:
                    end = row + 1 + len(by_channel[channel])
                    _merged(ws, row + 2, start + 6, end, start + 6,
                            _deviation(by_channel[channel]), number_format="0.0%")
            row += band_height + 4
    _note(ws, row + 2, "|Zii|. * 복소 Z 선형 보간값. 주파수 범위 밖은 공란입니다.", column)


def _write_dcr(ws, rows, config):
    sites = list(dict.fromkeys(row.site for row in rows))
    channels = list(dict.fromkeys(row.channel for row in rows))
    lookup = {}
    for row in rows:
        key = (row.channel, row.site)
        if key in lookup or not isfinite(row.resistance_mohm) or row.resistance_mohm < 0:
            raise ValueError("DCR 값은 채널/사이트별로 하나의 유효한 음이 아닌 값이어야 합니다.")
        lookup[key] = row.resistance_mohm
    small = len(sites) <= 8
    priorities = _priorities(config)
    data = [([next((priorities.get((channel, site)) for site in sites if priorities.get((channel, site)) is not None), None)] if small else []) +
            [channel, *[lookup.get((channel, site)) for site in sites]] for channel in channels]
    headers = (["Priority"] if small else []) + ["Channel / [mΩ]", *sites]
    _table(ws, headers, data, number_columns=range(2 if small else 1, len(headers)))
    if small:
        _widths(ws, 2, [8.25, _label_width(channels, 36.75), *([9.5] * len(sites))])
    else:
        pcb = config.settings.board_type == "PCB"
        _widths(ws, 2, [_label_width(channels, 15.875 if pcb else 15.375), *([9 if pcb else 7.875] * len(sites))])


def _write_caps(ws, rows, spd, config):
    sites = list(dict.fromkeys(row["site"] for row in rows))
    channels = list(dict.fromkeys(row["channel"] for row in rows))
    values = sorted({row["capacitance_f"] for row in rows}, key=lambda value: (value is None, value or 0))
    labels = {row["capacitance_f"]: row["label"] for row in rows}
    lookup = {(r["channel"], r["site"], r["capacitance_f"]): r["count"] for r in rows}
    mapped = {(m.channel, m.site) for m in _mappings(config)}
    priorities = _priorities(config)
    small = len(sites) <= 8
    channel_column = 3 if small else 2
    site_column = channel_column + 1
    if small:
        _widths(ws, 2, [8.25, _label_width(channels, 30.5), *([6.625] * len(sites))])
    else:
        _widths(ws, 2, [_label_width(channels, 15.625 if config.settings.board_type == "PCB" else 14.625), *([9] * len(sites))])
    row_number = 4
    for value in values:
        if small:
            _merged(ws, row_number, 2, row_number + 1, 2, "Priority", header=True)
        _merged(ws, row_number, channel_column, row_number + 1, channel_column, "Channel", header=True)
        _merged(ws, row_number, site_column, row_number, site_column + len(sites) - 1, labels[value], header=True)
        for column, site in enumerate(sites, site_column):
            _put(ws, row_number + 1, column, site, header=True)
        for row, channel in enumerate(channels, row_number + 2):
            if small:
                priority = next((priorities.get((channel, site)) for site in sites if priorities.get((channel, site)) is not None), None)
                _put(ws, row, 2, priority)
            _put(ws, row, channel_column, channel, size=11 if small else 12)
            for column, site in enumerate(sites, site_column):
                count = lookup.get((channel, site, value), 0) if (channel, site) in mapped else None
                _put(ws, row, column, count, number_format="0_ " if small else "General")
            if not small:
                ws.row_dimensions[row].height = 17.25
        row_number += len(channels) + 3
    nets = mapped_nets(spd, config)
    inactive = sum(c.is_cap and (not nets or bool(nets.intersection(c.nets))) and
                   not component_active(c, spd) for c in spd.components)
    _note(ws, row_number, f"활성 부품만 집계. Disabled/상태 미확인/Automatic 유효 모델 없음 제외: {inactive}개.")
    _note(ws, row_number + 1, "용량 미확인 부품은 미확인 항목으로 별도 표시하며 0F로 처리하지 않습니다.")


def build_report(config: ReportConfig, spd: SpdData | None, ts: TouchstoneData | None,
                 output_path: str | Path, progress=None, cancel=None) -> ReportResult:
    """Write a complete workbook atomically; failures never replace an existing report."""
    checkpoint(cancel)
    target = Path(output_path).expanduser().resolve()
    if target.suffix.lower() != ".xlsx":
        raise ValueError("출력 파일 확장자는 .xlsx여야 합니다.")
    if config.dcr_source_path and Path(config.dcr_source_path).resolve() == target:
        raise ValueError("DCR 입력 파일에 보고서를 덮어쓸 수 없습니다.")
    for source in (spd, ts):
        if source is not None and Path(source.path).resolve() == target:
            raise ValueError("입력 파일에 보고서를 덮어쓸 수 없습니다.")
    from .mapping import validate_mappings
    if any(m.selected for m in config.mappings):
        validate_mappings(config, ts, spd)
    pages, omitted = _plan(config, spd, ts)
    if not pages:
        raise ValueError("보고서를 만들 유효한 데이터가 없습니다. 입력과 채널 매핑을 확인하세요.")
    factor = 1 if config.settings.board_type.upper() == "PCB" else 1000
    unit = "Ω" if factor == 1 else "mΩ"
    warnings = list(dict.fromkeys([*(spd.warnings if spd else []), *(ts.warnings if ts else [])]))
    if any(any(flags) for _, _, flags in _spot_rows(config, ts)):
        warnings.append("일부 기준 주파수 값에 복소 Z 선형 보간을 적용했습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb._fonts[0] = _font()
    wb.remove(wb.active)
    temporary_output = None
    try:
        with tempfile.TemporaryDirectory(prefix="report-generator-") as images:
            for index, (kind, name, payload) in enumerate(pages):
                checkpoint(cancel)
                if progress:
                    progress(f"시트 생성: {name}", index, len(pages))
                ws = _base_sheet(wb, name, config, kind, ts)
                image_bottom = 4
                if kind == "100":
                    _write_100(ws, payload, factor, unit)
                elif kind == "pi":
                    _write_pi(ws, payload, factor, unit,
                              wide=len({mapping.site for mapping, _, _ in payload}) > 8)
                elif kind == "dcr":
                    _write_dcr(ws, payload, config)
                elif kind == "cap":
                    _write_caps(ws, payload, spd, config)
                elif kind == "placement":
                    for side_index, side in enumerate(payload):
                        checkpoint(cancel)
                        path = render_placement(spd, config, side, Path(images) / f"placement-{side}.png")
                        image_bottom = max(image_bottom, _add_image(ws, path, "B4" if side_index == 0 else "P4", max_width=850))
                elif kind == "group":
                    path = render_group_plot(ts, config, payload, Path(images) / f"group-{index}.png")
                    image_bottom = _add_image(ws, path, "B4")
                    _write_pi(ws, [(m, values, flags) for m, values, flags in _spot_rows(config, ts)
                                   if m.group == payload], factor, unit, column=16,
                              wide=len({mapping.site for mapping in _mappings(config, ts)}) > 8, detail=True,
                              channel_order=list(dict.fromkeys(mapping.channel for mapping in _mappings(config, ts))))
                printed_warnings = []
                if kind == "placement" and spd:
                    printed_warnings = spd.warnings
                elif kind == "cap" and spd:
                    printed_warnings = [warning for warning in spd.warnings
                                        if not any(term in warning.lower() for term in
                                                   ("outline", "placement", "coordinate", "외곽선", "배치", "좌표", "패드"))]
                elif kind in {"100", "pi", "group"} and ts:
                    printed_warnings = ts.warnings
                if printed_warnings:
                    _note(ws, max(ws.max_row + 3, image_bottom + 2, 7), "주의: " + " | ".join(printed_warnings))
                last_column = max(ws.max_column, 30 if kind == "placement" else 23 if kind == "group" else 2)
                ws.print_area = f"A1:{get_column_letter(last_column)}{max(ws.max_row, image_bottom)}"
            checkpoint(cancel)
            handle, temporary_output = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".xlsx", dir=target.parent)
            os.close(handle)
            wb.save(temporary_output)
        checkpoint(cancel)
        os.replace(temporary_output, target)
        temporary_output = None
        if progress:
            progress("보고서 저장 완료", len(pages), len(pages))
    finally:
        wb.close()
        if temporary_output is not None:
            Path(temporary_output).unlink(missing_ok=True)
    return ReportResult(str(target), [name for _, name, _ in pages], omitted, warnings)
