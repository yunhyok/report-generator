from __future__ import annotations

from collections import defaultdict
from math import ceil, cos, radians, sin
from pathlib import Path

import numpy as np
from matplotlib import rcParams
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import EllipseCollection, LineCollection, PolyCollection
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
CAP_COLORS_MLO = {100e-9: "#ff0000", 1e-6: "#92d050", 4.7e-6: "#3767ff"}
CAP_COLORS_PCB = {100e-12: "#efcce8", 10e-9: "#83df91", 100e-9: "#b9e6f2", 1e-6: "#ffff00", 10e-6: "#ff0000"}
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
    return [c for c in spd.components if any(p.side == side for p in c.pads) or
            (c.side.capitalize() == side and c.x_mm is not None and c.y_mm is not None and
             np.isfinite(c.x_mm) and np.isfinite(c.y_mm))]


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
    legend_rows = ceil(len(mappings) / min(6, len(mappings)))
    legend_height = legend_rows * .19 + .15
    height = 5.4 + legend_height
    fig = Figure(figsize=(12, height), dpi=110, facecolor="white")
    ax = fig.add_subplot(111, facecolor="white")
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
                linestyle=style, linewidth=.85,
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
    legend = fig.legend(handles, labels, loc="lower center", ncol=min(6, plotted),
                        prop=FontProperties(family="Malgun Gothic", size=9),
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
    reference_colors = CAP_COLORS_PCB if config.settings.board_type.upper() == "PCB" else CAP_COLORS_MLO
    palette = {value: next((color for nominal, color in reference_colors.items() if np.isclose(value, nominal, rtol=1e-6, atol=0)),
                           COLORS[i % len(COLORS)]) for i, value in enumerate(known)}
    polygons = defaultdict(list)
    markers = defaultdict(list)
    pad_polygons = defaultdict(list)
    pad_circles = defaultdict(list)
    fallback_bounds = []
    seen_pads = set()
    bounds = []
    inactive = unknown = missing_size = context = 0
    for c in components:
        selected_cap = highlighted(c)
        active = selected_cap and component_active(c, spd)
        value = resolved_capacitance(c, spd, config) if active else None
        color = palette.get(value, "#cf6bcc") if active else "#777777" if selected_cap else "#b8b8b8"
        kind = "active" if active else "inactive" if selected_cap else "context"
        inactive += selected_cap and not active
        context += not selected_cap
        unknown += active and value is None
        surface_pads = [pad for pad in c.pads if pad.side == side]
        pad_points = []
        pad_color = color if selected_cap else "#dddddd"
        for pad in surface_pads:
            if (pad.node, side) in seen_pads:
                continue
            seen_pads.add((pad.node, side))
            shape = pad.shape
            px, py = pad.x_mm, pad.y_mm
            if shape.kind == "Circle":
                pad_circles[pad_color].append((px, py, shape.width_mm, shape.height_mm))
                pad_points.extend(((px-shape.width_mm/2, py-shape.height_mm/2), (px+shape.width_mm/2, py+shape.height_mm/2)))
            else:
                a = radians(pad.rotation)
                verts = [(px+dx*cos(a)-dy*sin(a), py+dx*sin(a)+dy*cos(a)) for dx, dy in shape.vertices]
                pad_polygons[pad_color].append(verts)
                pad_points.extend(verts)
        bounds.extend(pad_points)
        x, y = c.x_mm, c.y_mm
        valid_position = c.side.capitalize() == side and x is not None and y is not None and np.isfinite(x) and np.isfinite(y)
        if valid_position:
            bounds.append((x, y))
        valid_size = (c.width_mm is not None and c.height_mm is not None and c.width_mm > 0 and c.height_mm > 0
                      and np.isfinite(c.width_mm) and np.isfinite(c.height_mm))
        if not valid_position or not valid_size:
            if pad_points and c.side.capitalize() == side:
                xmin, ymin = np.min(pad_points, axis=0)
                xmax, ymax = np.max(pad_points, axis=0)
                fallback_bounds.append(((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)))
            elif valid_position:
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
        polygons[kind, color].append(vertices)
        bounds.extend(vertices)
    extent = np.ptp(np.asarray(bounds), axis=0)
    ratio = extent[0] / max(extent[1], 1e-9)
    width = min(13, max(5.5, 7.8 * ratio + .4))
    fig = Figure(figsize=(width, 9), dpi=110, facecolor="black")
    ax = fig.add_subplot(111, facecolor="black")
    for (kind, color), verts in sorted(polygons.items(), key=lambda item: item[0][0] == "active"):
        ax.add_collection(PolyCollection(verts, facecolors=color if kind == "active" else "none",
                                        edgecolors="#eeeeee" if kind == "active" else color,
                                        linewidths=.22 if kind == "active" else .45))
    if fallback_bounds:
        ax.add_collection(PolyCollection(fallback_bounds, facecolors="none", edgecolors="#999999", linewidths=.35, linestyles="dashed"))
    for color, verts in pad_polygons.items():
        ax.add_collection(PolyCollection(verts, facecolors=color, edgecolors="#dddddd", linewidths=.12))
    for color, circles in pad_circles.items():
        circles = np.asarray(circles)
        ax.add_collection(EllipseCollection(circles[:, 2], circles[:, 3], np.zeros(len(circles)), units="xy",
                                           offsets=circles[:, :2], offset_transform=ax.transData,
                                           facecolors=color, edgecolors="#dddddd", linewidths=.12))
    for color, positions in markers.items():
        points = np.asarray(positions)
        ax.scatter(points[:, 0], points[:, 1], s=10, marker="x", c=color, linewidths=.65)
    outline_segments = [line for line in spd.outlines if len(line) >= 2]
    if outline_segments:
        ax.add_collection(LineCollection(outline_segments, colors="#eeeeee", linewidths=.8))
        bounds.extend(point for line in outline_segments for point in line)
    ax.update_datalim(np.asarray(bounds))
    ax.autoscale_view()
    ax.margins(.025)
    ax.set_aspect("equal", adjustable="box")
    mirror = side == "Bottom" and config.settings.bottom_mirror
    if mirror:
        ax.invert_xaxis()
    ax.set_axis_off()
    fig.text(.025, .97, f"{side.upper()}" + (" · mirrored" if mirror else ""),
             fontproperties=FontProperties(family="Malgun Gothic", size=16), color="white", va="top")
    handles = [Line2D([], [], color=color, marker="s", linestyle="none", label=format_capacitance(v))
               for v, color in palette.items()]
    if inactive:
        handles.append(Line2D([], [], color="#777777", marker="s", markerfacecolor="none", linestyle="none", label=f"Inactive ({inactive})"))
    if unknown:
        handles.append(Line2D([], [], color="#cf6bcc", marker="s", linestyle="none", label=f"Unknown capacitance ({unknown})"))
    if missing_size:
        handles.append(Line2D([], [], color="#eeeeee", marker="x", linestyle="none", label=f"Unknown dimensions ({missing_size})"))
    if context:
        handles.append(Line2D([], [], color="#b8b8b8", marker="s", markerfacecolor="none", linestyle="none", label=f"Other components / nets ({context})"))
    if fallback_bounds:
        handles.append(Line2D([], [], color="#999999", linestyle="dashed", label=f"Pin bounds ({len(fallback_bounds)})"))
    notes = []
    if not outline_segments:
        notes.append("Board outline unavailable; component coordinates only.")
    omitted = sum(c.side.capitalize() == side and not any(p.side == side for p in c.pads) and
                  (c.x_mm is None or c.y_mm is None or not np.isfinite(c.x_mm) or not np.isfinite(c.y_mm))
                  for c in spd.components)
    if omitted:
        notes.append(f"{omitted} components omitted: coordinates unavailable.")
    if not nets:
        notes.append("All capacitor nets shown; no selected net mapping.")
    if handles:
        fig.legend(handles=handles, loc="lower center", ncol=2 if width < 8 else 3,
                   prop=FontProperties(family="Malgun Gothic", size=10),
                   labelcolor="#eeeeee", frameon=False, bbox_to_anchor=(.5, .045))
    fig.text(.025, .012, "\n".join(notes), fontproperties=FontProperties(family="Malgun Gothic", size=8), color="#b8b8b8")
    fig.subplots_adjust(left=.015, right=.985, bottom=.18, top=.94)
    return _finish(fig, outputpath)
