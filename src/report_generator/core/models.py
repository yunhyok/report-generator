from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

Progress = Callable[[str, int, int], None]
Cancelled = Callable[[], bool]


class CancelledError(Exception):
    pass


def checkpoint(cancel: Cancelled | None) -> None:
    if cancel and cancel():
        raise CancelledError("작업이 취소되었습니다.")


def source_signature(path: str | Path) -> dict[str, int]:
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


@dataclass
class Port:
    index: int
    name: str
    description: str = ""
    net: str = ""
    site: str = ""
    channel: str = ""
    instance: str = ""
    enabled: bool = True


@dataclass
class SpiceModel:
    name: str
    capacitance_f: float | None = None
    name_capacitance_f: float | None = None
    comment_capacitance_f: float | None = None
    valid: bool = False
    conflict: bool = False
    evidence: str = ""


@dataclass
class PadShape:
    kind: str
    width_mm: float = 0.0
    height_mm: float = 0.0
    vertices: tuple[tuple[float, float], ...] = ()
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0


@dataclass
class SurfacePad:
    node: str
    x_mm: float
    y_mm: float
    side: str
    shape: PadShape
    rotation: float = 0.0


@dataclass
class Component:
    refdes: str
    model_name: str
    nets: list[str] = field(default_factory=list)
    status: str = "Automatic"
    x_mm: float | None = None
    y_mm: float | None = None
    rotation: float = 0.0
    side: str = "Top"
    width_mm: float | None = None
    height_mm: float | None = None
    is_cap: bool = False
    pin_bounds: tuple[float, float, float, float] | None = None
    pads: list[SurfacePad] = field(default_factory=list)


@dataclass
class SpdData:
    path: str
    signature: dict[str, int]
    models: dict[str, SpiceModel] = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    ground_nets: list[str] = field(default_factory=lambda: ["GND", "DGND"])
    aliases: dict[str, list[str]] = field(default_factory=dict)
    outlines: list[list[tuple[float, float]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TouchstoneData:
    path: str
    signature: dict[str, int]
    ports: list[Port]
    frequency_hz: Any
    z_diagonal: Any
    reference_ohm: float = 50.0
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChannelMapping:
    port_index: int | None = None
    source_name: str = ""
    spd_port: str = ""
    net: str = ""
    site: str = ""
    channel: str = ""
    selected: bool = True
    summary: bool = True
    group: str = ""
    priority: str = ""
    status: str = "manual"
    confirmed: bool = True


@dataclass
class DcrRow:
    site: str
    channel: str
    resistance_mohm: float


@dataclass
class ReportSettings:
    title: str = "Power Integrity Report"
    product: str = ""
    board_type: str = "MLO"
    report_date: str = field(default_factory=lambda: date.today().isoformat())
    revision: str = ""
    bottom_mirror: bool = True
    ground_nets: list[str] = field(default_factory=lambda: ["GND", "DGND"])


@dataclass
class ReportConfig:
    settings: ReportSettings = field(default_factory=ReportSettings)
    mappings: list[ChannelMapping] = field(default_factory=list)
    model_overrides: dict[str, float] = field(default_factory=dict)
    component_overrides: dict[str, float] = field(default_factory=dict)
    dcr: list[DcrRow] = field(default_factory=list)
    dcr_source_path: str = ""


@dataclass
class ReportResult:
    path: str
    sheets: list[str]
    omitted: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
