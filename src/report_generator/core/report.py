from __future__ import annotations

from math import ceil, isfinite
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
from .render import PLACEMENT_SIDES, render_group_plot, render_placement
from .capacitance import summarize_caps
from .mapping import channel_sections as _sections, group_title, plot_group, priority_value
from .dcr import dcr_channel_order
from .touchstone import interpolate_impedance, pi_display_units, plot_frequency_bounds, plot_impedance


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


def _channel_priorities(mappings, enabled):
    return {mapping.channel: priority_value(mapping.priority) for mapping in mappings} if enabled else {}


def _widths(ws, column, widths):
    for offset, width in enumerate(widths):
        ws.column_dimensions[get_column_letter(column + offset)].width = width


def _label_width(labels, minimum):
    return min(255, max(minimum, max((len(line) * 1.1 for label in labels
                                    for line in str(label).splitlines()), default=minimum)))


def _channel_label(channel, mappings, sites=None):
    names = {m.site: m.display_name.strip() or channel for m in mappings if m.channel == channel}
    if sites is not None:
        names = {site: names.get(site, channel) for site in sites}
    if len(set(names.values())) <= 1:
        return next(iter(names.values()), channel)
    return "\n".join(f"{site}: {name}" for site, name in names.items())


def _format_channel_label(cell, channel, label, channel_keys, width=None):
    if label != channel:
        if channel_keys is not None:
            channel_keys[cell.parent.title, cell.coordinate] = channel
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        width = width or cell.parent.column_dimensions[cell.column_letter].width
        lines = sum(max(1, ceil(len(line) * 1.1 / width)) for line in str(cell.value).splitlines())
        if lines > 1:
            dimensions = cell.parent.row_dimensions[cell.row]
            dimensions.height = min(409, max(dimensions.height or 16.5, 16.5 * lines))


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


def plot_groups(config, ts):
    groups = {}
    mappings = _mappings(config, ts) if ts is not None else []
    for mapping in mappings:
        groups.setdefault(plot_group(mapping), []).append(mapping)
    if not config.settings.use_priority:
        return groups
    automatic = {group for group, members in groups.items() if all(member.channel == group for member in members)}
    channels = list(dict.fromkeys(mapping.channel for mapping in mappings))
    priorities = _channel_priorities(mappings, config.settings.use_priority)
    ordered = iter(channel for _, section in _sections(channels, priorities, config.settings.use_priority)
                   for channel in section if channel in automatic)
    # Keep explicitly grouped pages in their original slots and reorder only per-channel pages.
    names = [next(ordered) if group in automatic else group for group in groups]
    return {name: groups[name] for name in names}


def _safe_sheet_name(label, used):
    label = re.sub(r"[\\/*?:\[\]\x00-\x1f]", "_", str(label)).strip("'") or "Group"
    # Excel counts UTF-16 units and rejects a trailing apostrophe even after truncation.
    encoded = label.encode("utf-16-le")
    number = 1
    while True:
        suffix = "" if number == 1 else f" ({number})"
        prefix = encoded[:2 * (31 - len(suffix))].decode("utf-16-le", errors="ignore")
        candidate = (prefix.rstrip("'") + suffix) or "Group"
        if candidate.casefold() not in used:
            used.add(candidate.casefold())
            return candidate
        number += 1


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
    if spd is not None:
        add("placement", "CAP_배치도면", PLACEMENT_SIDES)
    else:
        omitted["CAP_배치도면"] = "배치도에 사용할 SPD 입력이 없습니다."
    for group, maps in plot_groups(config, ts).items():
        valid = any(np.any(plot_impedance(ts, m.port_index)[1] > 0) for m in maps)
        if valid:
            title = group_title(group, maps)
            heading = title if any(m.group.strip() or m.display_name.strip() for m in maps) else None
            add("group", title, (group, heading))
        else:
            omitted[f"그룹: {group}"] = "1kHz~2GHz 표시 범위에 유효한 임피던스가 없습니다."
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


def _base_sheet(wb, name, config, kind, ts=None, page_title=None):
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
    if page_title is None:
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
        bounds = plot_frequency_bounds(ts.frequency_hz)
        if bounds is not None:
            lower, upper = bounds
            _note(ws, 2, f"* {frequency_label(lower)} ~ {frequency_label(upper)} : Automatic Frequency Sweep", 1)
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


def _write_100(ws, spots, factor, unit, use_priority=False, *, channel_keys=None):
    mappings = [m for m, _, _ in spots]
    sites = list(dict.fromkeys(m.site for m, _, _ in spots))
    channels = list(dict.fromkeys(m.channel for m, _, _ in spots))
    labels = {channel: _channel_label(channel, mappings) for channel in channels}
    small = len(sites) <= 8
    lookup = {(m.channel, m.site): values[3] * factor for m, values, _ in spots if values[3] is not None}
    priorities = _channel_priorities(mappings, use_priority)
    sections = _sections(channels, priorities, use_priority)
    data = {}
    for channel in channels:
        values = [lookup.get((channel, site)) for site in sites]
        available = [v for v in values if v is not None]
        maximum = max(available) if available else None
        average = sum(available) / len(available) if available else None
        deviation = (maximum - average) / average if average else None
        data[channel] = [labels[channel], *values, deviation, maximum, average] if small else [labels[channel], maximum, average, deviation, *values]
    if small:
        headers = [f"Channel / [{unit}]", *sites, "편차", "MAX.", "AVG."]
        widths = [_label_width(labels.values(), 36.75), *([9] * (len(sites) + 3))]
        deviation_column, site_column = 3 + len(sites) + use_priority, 3 + use_priority
    else:
        headers = [f"Channel / [{unit}]", "MAX.", "AVG.", "편차", *sites]
        numeric_width = 6.875 if unit == "Ω" else 9
        widths = [_label_width(labels.values(), 16.125), numeric_width, numeric_width,
                  7.75 if unit == "Ω" else 6.75, *([numeric_width] * len(sites))]
        deviation_column, site_column = 5 + use_priority, 6 + use_priority
    if use_priority:
        headers.insert(0, "Priority")
        widths.insert(0, 9)
    _widths(ws, 2, widths)
    _table(ws, headers, [])
    flags = {(m.channel, m.site): interpolated[3] for m, _, interpolated in spots}
    row = 5
    for index, (label, section) in enumerate(sections):
        if index:
            row += 1
        start_row = row
        for channel in section:
            rank = priorities.get(channel)
            values = ([rank if isinstance(rank, int) else None] if use_priority else []) + data[channel]
            for offset, value in enumerate(values):
                _put(ws, row, 2 + offset, value,
                     number_format=NUMBER if offset > use_priority else None)
            _format_channel_label(ws.cell(row, 2 + use_priority), channel, labels[channel], channel_keys)
            ws.cell(row, deviation_column).number_format = "0.0%"
            if not small:
                ws.cell(row, 2 + use_priority).font = _font(bold=True)
            for column, site in enumerate(sites, site_column):
                if flags.get((channel, site)):
                    ws.cell(row, column).number_format = '0.000"*"'
            row += 1
        if label:
            _merged(ws, start_row, 2, row - 1, 2, label)


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


def _write_pi(ws, spots, factor, unit, column=2, *, wide=False, detail=False, channel_order=(),
              use_priority=False, pcb=None, channel_keys=None):
    channels = list(dict.fromkeys(m.channel for m, _, _ in spots))
    by_channel = {channel: [spot for spot in spots if spot[0].channel == channel] for channel in channels}
    labels = [mapping.display_name.strip() or mapping.channel for mapping, _, _ in spots]
    priorities = _channel_priorities([m for m, _, _ in spots], use_priority)
    sections = _sections(channels, priorities, use_priority)
    pcb = unit == "Ω" if pcb is None else pcb
    number_format = "0.0_ " if wide and unit == "mΩ" else NUMBER
    if not wide or (detail and not pcb):
        priority = use_priority
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
            widths = ([9] if priority else []) + [_label_width(labels, 16.125), 7.5, 8.5, 7.5, 8.125, 9.625, 9.5]
        else:
            widths = ([9] if priority else []) + [_label_width(labels, 16.125 if detail else 30.5), 9]
            widths += [9] * 5 if detail else [9.5, 9.125, 9.125, 9.125, 9.125, 9]
        _widths(ws, column, widths)
        row = 5
        channel_index = 0
        for section_index, (label, section) in enumerate(sections):
            if section_index:
                row += 1
            section_row = row
            for channel in section:
                entries = by_channel[channel]
                fill = HEADER if not detail and channel_index % 2 else WHITE
                for mapping, values, flags in entries:
                    if priority:
                        rank = priorities.get(channel)
                        _put(ws, row, column, rank if isinstance(rank, int) else None, fill=fill)
                    display = mapping.display_name.strip() or channel
                    cell = _put(ws, row, channel_column, display, fill=fill)
                    _format_channel_label(cell, channel, display, channel_keys)
                    _put(ws, row, site_column, _site_value(mapping.site), fill=fill)
                    _pi_values(ws, row, site_column + 1, values, flags, factor, number_format, fill)
                    row += 1
                if not detail:
                    _merged(ws, row - len(entries), site_column + 6, row - 1, site_column + 6,
                            _deviation(entries), fill=fill, number_format="0.0%")
                channel_index += 1
            if label:
                _merged(ws, section_row, column, row - 1, column, label)
    else:
        across = 1 if detail else 4
        block_width = 6 if detail else 7
        row = 4
        for label, section in sections:
            if label:
                _merged(ws, row, column, row, column + min(across, len(section)) * block_width - 1,
                        label, header=True)
                row += 1
            for band_start in range(0, len(section), across):
                band = section[band_start:band_start + across]
                band_height = max(len(by_channel[channel]) for channel in band)
                ranked = use_priority and isinstance(priorities.get(band[0]), int)
                header_row = row + ranked
                for offset, channel in enumerate(band):
                    start = column + offset * block_width
                    color_index = channel_order.index(channel) if channel in channel_order else channels.index(channel)
                    fill = CHANNEL_HEADERS[color_index % len(CHANNEL_HEADERS)]
                    if ranked:
                        _merged(ws, row, start, row, start + 2, "Priority", header=True, fill=fill)
                        _merged(ws, row, start + 3, row, start + block_width - 1,
                                priorities[channel], header=True, fill=fill)
                    display = _channel_label(channel, [mapping for mapping, _, _ in by_channel[channel]])
                    _merged(ws, header_row, start, header_row, start + block_width - 1,
                            f"{display} / [{unit}]", header=True, fill=fill)
                    for index, heading in enumerate(["SITE", *FREQUENCY_LABELS] + ([] if detail else ["편차"])):
                        _put(ws, header_row + 1, start + index, heading, header=True, fill=fill)
                    widths = [9] * block_width if unit == "Ω" else (
                        [5.5, 8.5, 7, 8.125, 9.25, 7.875, 5.75],
                        [5.5, 9, 7.875, 8.125, 9.625, 10, 5.75],
                        [5.5, 9, 7.875, 8.125, 9.625, 10, 6.75],
                        [5.5, 9, 7.875, 8.125, 9.25, 9, 5.75],
                    )[offset][:block_width]
                    _widths(ws, start, widths)
                    _format_channel_label(ws.cell(header_row, start), channel, display, channel_keys, sum(widths))
                    for data_row, (mapping, values, flags) in enumerate(by_channel[channel], header_row + 2):
                        _put(ws, data_row, start, _site_value(mapping.site))
                        _pi_values(ws, data_row, start + 1, values, flags, factor, number_format)
                    if not detail:
                        end = header_row + 1 + len(by_channel[channel])
                        _merged(ws, header_row + 2, start + 6, end, start + 6,
                                _deviation(by_channel[channel]), number_format="0.0%")
                row = header_row + band_height + 4


def _write_dcr(ws, rows, config, *, channel_keys=None):
    sites = list(dict.fromkeys(row.site for row in rows))
    channels = list(dict.fromkeys(row.channel for row in rows))
    channels = dcr_channel_order(channels, _mappings(config), config.settings.use_priority) or channels
    labels = {channel: _channel_label(channel, _mappings(config),
                                    [row.site for row in rows if row.channel == channel]) for channel in channels}
    lookup = {}
    for row in rows:
        key = (row.channel, row.site)
        if key in lookup or not isfinite(row.resistance_mohm) or row.resistance_mohm < 0:
            raise ValueError("DCR 값은 채널/사이트별로 하나의 유효한 음이 아닌 값이어야 합니다.")
        lookup[key] = row.resistance_mohm
    small = len(sites) <= 8
    priorities = _priorities(config)
    data = [([next((priorities.get((channel, site)) for site in sites if priorities.get((channel, site)) is not None), None)] if small else []) +
            [labels[channel], *[lookup.get((channel, site)) for site in sites]] for channel in channels]
    headers = (["Priority"] if small else []) + ["Channel / [mΩ]", *sites]
    _table(ws, headers, data, number_columns=range(2 if small else 1, len(headers)))
    if small:
        _widths(ws, 2, [8.25, _label_width(labels.values(), 36.75), *([9.5] * len(sites))])
    else:
        pcb = config.settings.board_type == "PCB"
        _widths(ws, 2, [_label_width(labels.values(), 15.875 if pcb else 15.375), *([9 if pcb else 7.875] * len(sites))])
    for row, channel in enumerate(channels, 5):
        _format_channel_label(ws.cell(row, 3 if small else 2), channel, labels[channel], channel_keys)


def _write_caps(ws, rows, spd, config, *, channel_keys=None):
    sites = list(dict.fromkeys(row["site"] for row in rows))
    channels = list(dict.fromkeys(row["channel"] for row in rows))
    values = sorted({row["capacitance_f"] for row in rows}, key=lambda value: (value is None, value or 0))
    labels = {row["capacitance_f"]: row["label"] for row in rows}
    lookup = {(r["channel"], r["site"], r["capacitance_f"]): r["count"] for r in rows}
    mapped = {(m.channel, m.site) for m in _mappings(config)}
    channel_labels = {channel: _channel_label(channel, _mappings(config),
                                            [site for site in sites if (channel, site) in mapped]) for channel in channels}
    use_priority = config.settings.use_priority
    priorities = _channel_priorities(_mappings(config), use_priority)
    sections = _sections(channels, priorities, use_priority)
    small = len(sites) <= 8
    channel_column = 3 if use_priority else 2
    site_column = channel_column + 1
    if small:
        widths = [_label_width(channel_labels.values(), 30.5), *([6.625] * len(sites))]
    else:
        widths = [_label_width(channel_labels.values(), 15.625 if config.settings.board_type == "PCB" else 14.625), *([9] * len(sites))]
    _widths(ws, 2, ([8.25] if use_priority else []) + widths)
    row_number = 4
    for value in values:
        if use_priority:
            _merged(ws, row_number, 2, row_number + 1, 2, "Priority", header=True)
        _merged(ws, row_number, channel_column, row_number + 1, channel_column, "Channel", header=True)
        _merged(ws, row_number, site_column, row_number, site_column + len(sites) - 1, labels[value], header=True)
        for column, site in enumerate(sites, site_column):
            _put(ws, row_number + 1, column, site, header=True)
        row = row_number + 2
        for index, (label, section) in enumerate(sections):
            if index:
                row += 1
            section_row = row
            for channel in section:
                if use_priority:
                    rank = priorities.get(channel)
                    _put(ws, row, 2, rank if isinstance(rank, int) else None)
                cell = _put(ws, row, channel_column, channel_labels[channel], size=11 if small else 12)
                _format_channel_label(cell, channel, channel_labels[channel], channel_keys)
                for column, site in enumerate(sites, site_column):
                    count = lookup.get((channel, site, value), 0) if (channel, site) in mapped else None
                    _put(ws, row, column, count, number_format="0_ " if small else "General")
                if not small:
                    ws.row_dimensions[row].height = max(ws.row_dimensions[row].height or 0, 17.25)
                row += 1
            if label:
                _merged(ws, section_row, 2, row - 1, 2, label)
        row_number = row + 1


def build_report(config: ReportConfig, spd: SpdData | None, ts: TouchstoneData | None,
                 output_path: str | Path, progress=None, cancel=None, *,
                 channel_keys: dict[tuple[str, str], str] | None = None) -> ReportResult:
    """Write the workbook atomically. Validation can collect original channel keys in memory."""
    checkpoint(cancel)
    target = Path(output_path).expanduser().resolve()
    if target.suffix.lower() != ".xlsx":
        raise ValueError("출력 파일 확장자는 .xlsx여야 합니다.")
    if config.dcr_source_path and Path(config.dcr_source_path).resolve() == target:
        raise ValueError("DCR 입력 파일에 보고서를 덮어쓸 수 없습니다.")
    if config.channel_settings_source_path and Path(config.channel_settings_source_path).resolve() == target:
        raise ValueError("채널 설정 Excel 파일에 보고서를 덮어쓸 수 없습니다.")
    for source in (spd, ts):
        if source is not None and Path(source.path).resolve() == target:
            raise ValueError("입력 파일에 보고서를 덮어쓸 수 없습니다.")
    from .mapping import validate_mappings
    if any(m.selected for m in config.mappings):
        validate_mappings(config, ts, spd)
    pages, omitted = _plan(config, spd, ts)
    if not pages:
        raise ValueError("보고서를 만들 유효한 데이터가 없습니다. 입력과 채널 매핑을 확인하세요.")
    factor, unit = pi_display_units(ts, config)
    pcb = config.settings.board_type.upper() == "PCB"
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
                heading = None
                if kind == "group":
                    group, heading = payload
                ws = _base_sheet(wb, name, config, kind, ts, page_title=heading)
                image_bottom = 4
                if kind == "100":
                    _write_100(ws, payload, factor, unit, config.settings.use_priority, channel_keys=channel_keys)
                elif kind == "pi":
                    _write_pi(ws, payload, factor, unit,
                              wide=len({mapping.site for mapping, _, _ in payload}) > 8,
                              use_priority=config.settings.use_priority, pcb=pcb, channel_keys=channel_keys)
                elif kind == "dcr":
                    _write_dcr(ws, payload, config, channel_keys=channel_keys)
                elif kind == "cap":
                    _write_caps(ws, payload, spd, config, channel_keys=channel_keys)
                elif kind == "placement":
                    for side_index, side in enumerate(payload):
                        checkpoint(cancel)
                        path = render_placement(spd, config, side, Path(images) / f"placement-{side}.png")
                        image_bottom = max(image_bottom, _add_image(ws, path, "B4" if side_index == 0 else "P4", max_width=850))
                elif kind == "group":
                    path = render_group_plot(ts, config, group, Path(images) / f"group-{index}.png",
                                             display_units=(factor, unit))
                    image_bottom = _add_image(ws, path, "B4")
                    _write_pi(ws, [(m, values, flags) for m, values, flags in _spot_rows(config, ts)
                                   if plot_group(m) == group], factor, unit, column=16,
                              wide=len({mapping.site for mapping in _mappings(config, ts)}) > 8, detail=True,
                              channel_order=list(dict.fromkeys(mapping.channel for mapping in _mappings(config, ts))),
                              use_priority=config.settings.use_priority, pcb=pcb, channel_keys=channel_keys)
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
