from __future__ import annotations

from collections import Counter, defaultdict
from colorsys import hsv_to_rgb
from math import cos, radians, sin
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
from .mapping import group_presentation
from .touchstone import pi_display_units, plot_frequency_bounds, plot_impedance


FONT = FontProperties(family="Malgun Gothic", size=15)
TITLE_FONT = FontProperties(family="Malgun Gothic", size=26)
COLORS = ["#2474b5", "#d95f02", "#168653", "#9857aa", "#bc3143", "#876441",
          "#d85f9c", "#53747b", "#928500", "#16a1a5", "#6067ba", "#b87524"]
CAP_COLORS_MLO = {100e-9: "#ff0000", 1e-6: "#92d050", 4.7e-6: "#3767ff"}
CAP_COLORS_PCB = {100e-12: "#efcce8", 10e-9: "#83df91", 100e-9: "#b9e6f2", 1e-6: "#ffff00", 10e-6: "#ff0000"}
PLACEMENT_SIDES = ("Top", "Bottom")
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
    canvas = FigureCanvasAgg(fig)
    legend = fig.axes[0].get_legend()
    if legend is not None:
        canvas.print_figure(str(path), format="png", bbox_inches="tight", bbox_extra_artists=[legend])
    else:
        canvas.print_png(str(path))
    fig.clear()
    return str(path)


def _axes_font(ax, pcb=False) -> None:
    for text in ax.get_yticklabels():
        text.set_fontproperties(FONT)
    for text in ax.get_xticklabels():
        text.set_fontproperties(FontProperties(family="Malgun Gothic", size=10 if pcb else 15))
    ax.xaxis.label.set_fontproperties(FontProperties(family="Malgun Gothic", size=9 if pcb else 14.5))
    ax.yaxis.label.set_fontproperties(FontProperties(family="Malgun Gothic", size=14))


def render_group_plot(ts: TouchstoneData, config: ReportConfig, group: str,
                      outputpath: str | Path, *, display_units: tuple[int, str] | None = None) -> str:
    """Render selected mapped ports from the same Z diagonal used by Excel."""
    mappings = [m for m in config.mappings if m.selected and m.confirmed and
                m.group == group and m.port_index is not None]
    if not mappings:
        raise ValueError("그룹에 선택한 Touchstone 포트가 없습니다.")
    title, show_names = group_presentation(group, mappings)
    fig = Figure(figsize=(12, 5.4), dpi=110, facecolor="white")
    ax = fig.add_subplot(111, facecolor="white")
    factor, unit = display_units if display_units is not None else pi_display_units(ts, config)
    plotted = 0
    for index, m in enumerate(mappings):
        if not 1 <= m.port_index <= len(ts.ports):
            raise ValueError(f"Touchstone 포트 범위 오류: {m.port_index}")
        frequency, magnitude = plot_impedance(ts, m.port_index)
        if not np.isfinite(magnitude).any():
            continue
        # Beyond the fixed palette, golden-ratio hue steps keep adjacent curves distinct.
        color = COLORS[index] if len(mappings) <= len(COLORS) else hsv_to_rgb(index * .61803398875 % 1, .72, .60)
        ax.plot(frequency / 1e6, magnitude * factor, color=color,
                linestyle="solid", linewidth=config.settings.plot_line_width_pt,
                label=f"{m.display_name.strip()} ({m.channel}) / {m.site}"
                if m.display_name.strip() else f"{m.channel} / {m.site}")
        plotted += 1
    if not plotted:
        raise ValueError("그룹에 그래프로 표시할 유효한 임피던스가 없습니다.")
    ax.set_xscale("log")
    ax.set_yscale("log")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        axis.set_minor_formatter(NullFormatter())
    bounds = plot_frequency_bounds(ts.frequency_hz)
    if bounds and bounds[0] < bounds[1]:
        ax.set_xlim(bounds[0] / 1e6, bounds[1] / 1e6)
    ax.set_title(title, fontproperties=TITLE_FONT, pad=15, parse_math=False)
    if show_names:
        legend = ax.legend(handles=ax.lines, labels=[line.get_label() for line in ax.lines],
                           prop=FontProperties(family="Malgun Gothic", size=9),
                           loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        legend.set_in_layout(False)
        for text in legend.get_texts():
            text.set_parse_math(False)
    ax.set_xlabel("Frequency [MHz]")
    ax.set_ylabel(f"|Zii| [{unit}]")
    ax.grid(True, which="major", color="#d8d8d8", linewidth=.7)
    ax.grid(True, which="minor", color="#eeeeee", linewidth=.4)
    _axes_font(ax, pcb=config.settings.board_type.upper() == "PCB")
    fig.tight_layout()
    return _finish(fig, outputpath)


def render_placement(spd: SpdData, config: ReportConfig, side: str,
                     outputpath: str | Path) -> str:
    """Render actual component positions; dimensions never inferred from a refdes."""
    side = side.capitalize()
    if side not in PLACEMENT_SIDES:
        raise ValueError("배치도 면은 Top 또는 Bottom이어야 합니다.")
    nets = mapped_nets(spd, config)
    components = placement_components(spd, config, side)
    def highlighted(c):
        return c.is_cap and (not nets or bool(nets.intersection(c.nets)))
    counts = Counter(resolved_capacitance(c, spd, config) for c in components
                     if highlighted(c) and component_active(c, spd))
    known = sorted(v for v in counts if v is not None)
    reference_colors = CAP_COLORS_PCB if config.settings.board_type.upper() == "PCB" else CAP_COLORS_MLO
    palette = {value: next((color for nominal, color in reference_colors.items() if np.isclose(value, nominal, rtol=1e-6, atol=0)),
                           COLORS[i % len(COLORS)]) for i, value in enumerate(known)}
    polygons = defaultdict(list)
    polygon_sizes = defaultdict(list)
    markers = defaultdict(list)
    pad_polygons = defaultdict(list)
    pad_sizes = defaultdict(list)
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
        x, y = c.x_mm, c.y_mm
        valid_position = c.side.capitalize() == side and x is not None and y is not None and np.isfinite(x) and np.isfinite(y)
        valid_size = (c.width_mm is not None and c.height_mm is not None and c.width_mm > 0 and c.height_mm > 0
                      and np.isfinite(c.width_mm) and np.isfinite(c.height_mm))
        surface_pads = [pad for pad in c.pads if pad.side == side]
        pad_points = []
        pad_color = color if selected_cap else "#dddddd"
        pad_edge = "#dddddd"
        if active:
            pad_edge = "none" if valid_position and valid_size else "#111111"
        for pad in surface_pads:
            if (pad.node, side) in seen_pads:
                continue
            seen_pads.add((pad.node, side))
            shape = pad.shape
            px, py = pad.x_mm, pad.y_mm
            if shape.kind == "Circle":
                pad_circles[pad_edge, pad_color].append((px, py, shape.width_mm, shape.height_mm))
                pad_points.extend(((px-shape.width_mm/2, py-shape.height_mm/2), (px+shape.width_mm/2, py+shape.height_mm/2)))
            else:
                a = radians(pad.rotation)
                verts = [(px+dx*cos(a)-dy*sin(a), py+dx*sin(a)+dy*cos(a)) for dx, dy in shape.vertices]
                pad_polygons[pad_edge, pad_color].append(verts)
                if pad_edge == "#111111":
                    pad_sizes[pad_edge, pad_color].append(np.ptp(np.asarray(shape.vertices), axis=0).min())
                pad_points.extend(verts)
        bounds.extend(pad_points)
        if valid_position:
            bounds.append((x, y))
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
        half_width, half_height = c.width_mm / 2, c.height_mm / 2
        outline = c.outline_vertices or ((-half_width, -half_height), (half_width, -half_height),
                                         (half_width, half_height), (-half_width, half_height))
        for dx, dy in outline:
            vertices.append((x + dx * cos(angle) - dy * sin(angle),
                             y + dx * sin(angle) + dy * cos(angle)))
        polygons[kind, color].append(vertices)
        if active:
            polygon_sizes[kind, color].append(min(c.width_mm, c.height_mm))
        bounds.extend(vertices)
    has_coordinates = bool(bounds)
    outline_segments = [line for line in spd.outlines if len(line) >= 2]
    layout_bounds = bounds or [point for line in outline_segments for point in line]
    width = 8.2
    if layout_bounds:
        extent = np.ptp(np.asarray(layout_bounds), axis=0)
        ratio = extent[0] / max(extent[1], 1e-9)
        width = min(13, max(5.5, 7.8 * ratio + .4))
    fig = Figure(figsize=(width, 9), dpi=110, facecolor="black")
    ax = fig.add_subplot(111, facecolor="black")
    cap_edges = []
    for (kind, color), verts in sorted(polygons.items(), key=lambda item: item[0][0] == "active"):
        ax.add_collection(PolyCollection(verts, facecolors=color if kind == "active" else "none",
                                        edgecolors="none" if kind == "active" else color,
                                        linewidths=.45))
    if fallback_bounds:
        ax.add_collection(PolyCollection(fallback_bounds, facecolors="none", edgecolors="#999999", linewidths=.35, linestyles="dashed"))
    for (edge, color), verts in pad_polygons.items():
        collection = PolyCollection(verts, facecolors=color, edgecolors=edge, linewidths=.12)
        ax.add_collection(collection)
        if edge == "#111111":
            cap_edges.append((collection, .6, pad_sizes[edge, color]))
    for (edge, color), circles in pad_circles.items():
        circles = np.asarray(circles)
        collection = EllipseCollection(circles[:, 2], circles[:, 3], np.zeros(len(circles)), units="xy",
                                       offsets=circles[:, :2], offset_transform=ax.transData,
                                       facecolors=color, edgecolors=edge, linewidths=.12)
        ax.add_collection(collection)
        if edge == "#111111":
            cap_edges.append((collection, .6, circles[:, 2:].min(axis=1)))
    for color, positions in markers.items():
        points = np.asarray(positions)
        ax.scatter(points[:, 0], points[:, 1], s=10, marker="x", c=color, linewidths=.65)
    # Draw actual CAP perimeters after pads so same-color pad fills cannot hide them.
    for (kind, color), verts in polygons.items():
        if kind == "active":
            collection = PolyCollection(verts, facecolors="none", edgecolors="#111111", zorder=3)
            ax.add_collection(collection)
            cap_edges.append((collection, .8, polygon_sizes[kind, color]))
    if outline_segments:
        ax.add_collection(LineCollection(outline_segments, colors="#eeeeee", linewidths=.8))
        bounds.extend(point for line in outline_segments for point in line)
    if bounds:
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
    if not has_coordinates:
        fig.text(.5, .11 if outline_segments else .5, "No displayable component / pad coordinates",
                 fontproperties=FontProperties(family="Malgun Gothic", size=10),
                 color="#eeeeee", ha="center", va="center")
    handles = [Line2D([], [], color=color, marker="s", linestyle="none", label=f"{format_capacitance(v)} ({counts[v]})")
               for v, color in palette.items()]
    if inactive:
        handles.append(Line2D([], [], color="#777777", marker="s", markerfacecolor="none", linestyle="none", label=f"Inactive ({inactive})"))
    if unknown:
        handles.append(Line2D([], [], color="#cf6bcc", marker="s", linestyle="none", label=f"Unknown capacitance ({unknown})"))
    if missing_size:
        handles.append(Line2D([], [], color="#eeeeee", marker="x", linestyle="none", label=f"Unknown dimensions ({missing_size})"))
    if context:
        handles.append(Line2D([], [], color="#b8b8b8", marker="s", markerfacecolor="none", linestyle="none", label="Other components / nets"))
    if fallback_bounds:
        handles.append(Line2D([], [], color="#999999", linestyle="dashed", label=f"Pin bounds ({len(fallback_bounds)})"))
    if handles:
        fig.legend(handles=handles, loc="lower center", ncol=2 if width < 8 else 3,
                   prop=FontProperties(family="Malgun Gothic", size=10),
                   labelcolor="#eeeeee", frameon=False, bbox_to_anchor=(.5, .045))
    fig.subplots_adjust(left=.015, right=.985, bottom=.18, top=.94)
    ax.apply_aspect()
    points_per_mm = abs(ax.transData.get_matrix()[0, 0]) * 72 / fig.dpi
    for collection, maximum, sizes in cap_edges:
        collection.set_linewidths(np.minimum(maximum, np.asarray(sizes) * points_per_mm * .2))
    return _finish(fig, outputpath)
