from __future__ import annotations

from collections import defaultdict
from math import ceil, cos, radians, sin
from pathlib import Path

import numpy as np
from matplotlib import rcParams
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, NullFormatter

from .models import ReportConfig, SpdData, TouchstoneData
from .capacitance import component_active, format_capacitance, resolved_capacitance


FONT = FontProperties(family="Malgun Gothic", size=11)
TITLE_FONT = FontProperties(family="Malgun Gothic", size=24)
COLORS = ["#2474b5", "#d95f02", "#168653", "#9857aa", "#bc3143", "#876441",
          "#d85f9c", "#53747b", "#928500", "#16a1a5", "#6067ba", "#b87524"]
rcParams["axes.unicode_minus"] = False


def mapped_nets(spd: SpdData, config: ReportConfig):
    nets = {m.net for m in config.mappings if m.selected and m.confirmed and m.net}
    pending = list(nets)
    grounds = set(spd.ground_nets) | set(config.settings.ground_nets)
    while pending:
        for alias in spd.aliases.get(pending.pop(), []):
            if alias not in nets and alias not in grounds:
                nets.add(alias)
                pending.append(alias)
    return nets


def placement_components(spd: SpdData, config: ReportConfig, side: str):
    return [c for c in spd.components if c.side.capitalize() == side and
            c.x_mm is not None and c.y_mm is not None and
            np.isfinite(c.x_mm) and np.isfinite(c.y_mm)]


def _finish(fig: Figure, output_path: str | Path) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    FigureCanvasAgg(fig).print_png(str(path))
    fig.clear()
    return str(path)


def _axes_font(ax) -> None:
    for text in [*ax.get_xticklabels(), *ax.get_yticklabels(), ax.xaxis.label, ax.yaxis.label]:
        text.set_fontproperties(FONT)


def render_group_plot(ts: TouchstoneData, config: ReportConfig, group: str,
                      outputpath: str | Path) -> str:
    """Render selected mapped ports from the same Z diagonal used by Excel."""
    mappings = [m for m in config.mappings if m.selected and m.confirmed and
                m.group == group and m.port_index is not None]
    if not mappings:
        raise ValueError("그룹에 선택한 Touchstone 포트가 없습니다.")
    channels = list(dict.fromkeys(m.channel for m in mappings))
    sites = list(dict.fromkeys(m.site for m in mappings))
    legend_rows = ceil(len(mappings) / min(4, len(mappings)))
    legend_height = legend_rows * .25 + .2
    height = 6 + legend_height
    fig = Figure(figsize=(12, height), dpi=110, facecolor="white")
    ax = fig.add_subplot(111)
    unit = "Ω" if config.settings.board_type.upper() == "PCB" else "mΩ"
    factor = 1 if unit == "Ω" else 1000
    frequency = np.asarray(ts.frequency_hz, dtype=float)
    in_range = (frequency >= 1e3) & (frequency <= 1e9)
    plotted = 0
    for m in mappings:
        if not 1 <= m.port_index <= len(ts.ports):
            raise ValueError(f"Touchstone 포트 범위 오류: {m.port_index}")
        magnitude = np.abs(np.asarray(ts.z_diagonal[:, m.port_index - 1])) * factor
        valid = in_range & np.isfinite(frequency) & np.isfinite(magnitude) & (magnitude > 0)
        if not valid.any():
            continue
        y = np.where(valid, magnitude, np.nan)
        site_index = sites.index(m.site)
        color_index = site_index if len(channels) == 1 else channels.index(m.channel)
        style_index = site_index // len(COLORS) if len(channels) == 1 else site_index
        style = "solid" if style_index == 0 else (0, (1 + style_index // 4, 1 + style_index % 4))
        ax.plot(frequency / 1e6, y, color=COLORS[color_index % len(COLORS)],
                linestyle=style, linewidth=1.1, marker=".", markersize=2,
                label=f"{m.channel} / {m.site}")
        plotted += 1
    if not plotted:
        raise ValueError("그룹에 그래프로 표시할 유효한 임피던스가 없습니다.")
    ax.set_xscale("log")
    ax.set_yscale("log")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        axis.set_minor_formatter(NullFormatter())
    valid_frequencies = frequency[in_range & np.isfinite(frequency)]
    if len(valid_frequencies) > 1:
        ax.set_xlim(valid_frequencies.min() / 1e6, valid_frequencies.max() / 1e6)
    ax.set_title(group, fontproperties=TITLE_FONT, pad=15, parse_math=False)
    ax.set_xlabel("Frequency [MHz]")
    ax.set_ylabel(f"|Zii| [{unit}]")
    ax.grid(True, which="major", color="#d8d8d8", linewidth=.7)
    ax.grid(True, which="minor", color="#eeeeee", linewidth=.4)
    _axes_font(ax)
    handles, labels = ax.get_legend_handles_labels()
    legend = fig.legend(handles, labels, loc="lower center", ncol=min(4, plotted), prop=FONT,
                        frameon=False, bbox_to_anchor=(.5, .01))
    for text in legend.get_texts():
        text.set_parse_math(False)
    fig.tight_layout(rect=(0, legend_height / height, 1, 1))
    return _finish(fig, outputpath)


def render_placement(spd: SpdData, config: ReportConfig, side: str,
                     outputpath: str | Path) -> str:
    """Render actual component positions; dimensions never inferred from a refdes."""
    side = side.capitalize()
    if side not in {"Top", "Bottom"}:
        raise ValueError("배치도 면은 Top 또는 Bottom이어야 합니다.")
    nets = mapped_nets(spd, config)
    components = placement_components(spd, config, side)
    if not components:
        raise ValueError(f"{side} 면에 표시할 부품 좌표가 없습니다.")
    def highlighted(c):
        return c.is_cap and (not nets or bool(nets.intersection(c.nets)))
    values = {resolved_capacitance(c, spd, config) for c in components
              if highlighted(c) and component_active(c, spd)}
    known = sorted(v for v in values if v is not None)
    palette = {value: COLORS[i % len(COLORS)] for i, value in enumerate(known)}
    polygons = defaultdict(list)
    markers = defaultdict(list)
    bounds = []
    inactive = unknown = missing_size = context = 0
    for c in components:
        selected_cap = highlighted(c)
        active = selected_cap and component_active(c, spd)
        value = resolved_capacitance(c, spd, config) if active else None
        color = palette.get(value, "#aa52a3") if active else "#a7a7a7" if selected_cap else "#d4d4d4"
        inactive += selected_cap and not active
        context += not selected_cap
        unknown += active and value is None
        x, y = c.x_mm, c.y_mm
        bounds.append((x, y))
        if (c.width_mm is None or c.height_mm is None or c.width_mm <= 0 or c.height_mm <= 0
                or not np.isfinite(c.width_mm) or not np.isfinite(c.height_mm)):
            markers[color].append((x, y))
            missing_size += 1
            continue
        angle = radians(c.rotation)
        vertices = []
        for dx, dy in [(-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5)]:
            dx *= c.width_mm
            dy *= c.height_mm
            vertices.append((x + dx * cos(angle) - dy * sin(angle),
                             y + dx * sin(angle) + dy * cos(angle)))
        polygons[color].append(vertices)
        bounds.extend(vertices)
    fig = Figure(figsize=(11, 9), dpi=110, facecolor="white")
    ax = fig.add_subplot(111)
    for color, verts in polygons.items():
        ax.add_collection(PolyCollection(verts, facecolors=color, edgecolors="#333333", linewidths=.2))
    for color, positions in markers.items():
        points = np.asarray(positions)
        ax.scatter(points[:, 0], points[:, 1], s=10, marker="x", c=color, linewidths=.6)
    outline_segments = [line for line in spd.outlines if len(line) >= 2]
    if outline_segments:
        ax.add_collection(LineCollection(outline_segments, colors="#333333", linewidths=.8))
        bounds.extend(point for line in outline_segments for point in line)
    ax.update_datalim(np.asarray(bounds))
    ax.autoscale_view()
    ax.margins(.04)
    ax.set_aspect("equal", adjustable="box")
    mirror = side == "Bottom" and config.settings.bottom_mirror
    if mirror:
        ax.invert_xaxis()
    ax.set_title(f"{side.upper()}" + (" · mirrored" if mirror else ""), fontproperties=TITLE_FONT)
    ax.set_xlabel("X [mm]")
    ax.set_ylabel("Y [mm]")
    _axes_font(ax)
    handles = [Line2D([], [], color=color, marker="s", linestyle="none", label=format_capacitance(v))
               for v, color in palette.items()]
    if inactive:
        handles.append(Line2D([], [], color="#a7a7a7", marker="s", linestyle="none", label=f"Inactive ({inactive})"))
    if unknown:
        handles.append(Line2D([], [], color="#aa52a3", marker="s", linestyle="none", label=f"Unknown capacitance ({unknown})"))
    if missing_size:
        handles.append(Line2D([], [], color="#333333", marker="x", linestyle="none", label=f"Unknown dimensions ({missing_size})"))
    if context:
        handles.append(Line2D([], [], color="#d4d4d4", marker="s", linestyle="none", label=f"Other components / nets ({context})"))
    notes = []
    if not outline_segments:
        notes.append("Board outline unavailable; component coordinates only.")
    omitted = sum(c.side.capitalize() == side and
                  (c.x_mm is None or c.y_mm is None or not np.isfinite(c.x_mm) or not np.isfinite(c.y_mm))
                  for c in spd.components)
    if omitted:
        notes.append(f"{omitted} components omitted: coordinates unavailable.")
    if not nets:
        notes.append("All capacitor nets shown; no selected net mapping.")
    if handles:
        fig.legend(handles=handles, loc="lower center", ncol=min(4, len(handles)), prop=FONT,
                   frameon=False, bbox_to_anchor=(.5, .04))
    fig.text(.02, .01, " ".join(notes), fontproperties=FONT, color="#555555")
    fig.tight_layout(rect=(0, .17, 1, 1))
    return _finish(fig, outputpath)
