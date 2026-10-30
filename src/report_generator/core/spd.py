from __future__ import annotations

import math
import re
from pathlib import Path

from .capacitance import parse_capacitance
from .models import Component, PadShape, Port, SpiceModel, SpdData, SurfacePad, checkpoint, source_signature

_PROPERTIES = re.compile(r'(\w+)\s*=\s*("[^"]*"|\S+)')
_DIMENSION = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(mm|mil|um|u|m|in)?$", re.I)
_PIN = re.compile(r"^\s*\S+\s+\$Package\.Node\S+", re.I)
_PORT = re.compile(r"^Port(\d+)\S*(?:\s|$)", re.I)
_SPICE_VALUE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(?:meg|[tgkmunpf])?[a-z]*$", re.I)


def infer_site_channel(net: str, instance: str = "") -> tuple[str, str]:
    match = re.match(r"^(?:SITE|U)(\d+)[_-](.+)$", net, re.I)
    if match:
        return f"SITE{int(match[1])}", match[2]
    match = re.match(r"^(.+)/(\d+)$", net)
    if match:
        return f"SITE{int(match[2])}", match[1]
    match = re.fullmatch(r"(?:SITE|U)(\d+)", instance, re.I)
    return (f"SITE{int(match[1])}" if match else ""), net


def _dimension(token: str) -> float:
    match = _DIMENSION.fullmatch(token)
    if not match:
        raise ValueError("Invalid SPD dimension.")
    value = float(match[1]) * {"mm": 1, "mil": .0254, "um": .001, "u": .001, "m": 1000, "in": 25.4, "": 1}[str(match[2] or "").lower()]
    if not math.isfinite(value):
        raise ValueError("SPD geometry must be finite.")
    return value


def _surface_side(layer: str) -> str | None:
    name = layer.upper().split("$")[-1]
    return "Bottom" if name == "BOTTOM" else "Top" if name == "TOP" else None


def _pad_shape(lines: list[str]) -> PadShape | None:
    text = " ".join(lines)
    tokens = re.split(r"\boffset[xy]\s*=", text, flags=re.I)[0].split()
    kind = tokens[1]
    if kind not in ("Circle", "Square", "Box", "Polygon"):
        return None
    dimensions = [_dimension(token) for token in tokens[2:]]
    properties = {key.lower(): value for key, value in _PROPERTIES.findall(text)}
    ox = _dimension(properties.get("offsetx", "0"))
    oy = _dimension(properties.get("offsety", "0"))
    if kind == "Circle" and len(dimensions) == 1 and dimensions[0] > 0:
        return PadShape(kind, dimensions[0] * 2, dimensions[0] * 2, offset_x_mm=ox, offset_y_mm=oy)
    if kind in ("Square", "Box") and len(dimensions) == (1 if kind == "Square" else 2) and min(dimensions) > 0:
        w, h = dimensions[0], dimensions[-1]
        return PadShape(kind, w, h, ((-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)), ox, oy)
    if kind == "Polygon" and len(dimensions) >= 6 and len(dimensions) % 2 == 0:
        return PadShape(kind, vertices=tuple(zip(dimensions[::2], dimensions[1::2])), offset_x_mm=ox, offset_y_mm=oy)
    return None


def _attach_surface_pads(path, data, references, padstacks, encoding, progress, cancel):
    # Retain component pins only; interior mesh nodes and unreferenced vias stay on disk.
    encoded = {name.encode("utf-8" if encoding == "utf-8-sig" else encoding): owners for name, owners in references.items()}
    missing = 0

    def extend_bounds(component, xmin, ymin, xmax, ymax):
        old = component.pin_bounds
        component.pin_bounds = (min(old[0], xmin), min(old[1], ymin), max(old[2], xmax), max(old[3], ymax)) if old else (xmin, ymin, xmax, ymax)

    def record(text, owners):
        nonlocal missing
        props = {key.lower(): value for key, value in _PROPERTIES.findall(text)}
        side = _surface_side(props.get("layer", ""))
        if side is None:
            return
        x, y = _dimension(props["x"]), _dimension(props["y"])
        rotation = float(props.get("absoluterotation", "0"))
        if not math.isfinite(rotation):
            raise ValueError("SPD pad rotation must be finite.")
        for component in owners:
            extend_bounds(component, x, y, x, y)
        definitions = padstacks.get(props.get("padstack", ""), {})
        surfaces = [(_surface_side(layer), shape) for layer, shape in definitions.items() if _surface_side(layer)]
        if not any(surface == side for surface, _ in surfaces) and "DefaultLibLayer" in definitions:
            surfaces.append((side, definitions["DefaultLibLayer"]))
        if not surfaces:
            missing += 1
        angle = math.radians(rotation)
        c, s = math.cos(angle), math.sin(angle)
        for surface, shape in surfaces:
            cx = x + shape.offset_x_mm*c - shape.offset_y_mm*s
            cy = y + shape.offset_x_mm*s + shape.offset_y_mm*c
            pad = SurfacePad(text.split()[0].split("::")[0], cx, cy, surface, shape, rotation)
            if shape.kind == "Circle":
                box = cx-shape.width_mm/2, cy-shape.height_mm/2, cx+shape.width_mm/2, cy+shape.height_mm/2
            else:
                vertices = [(cx+dx*c-dy*s, cy+dx*s+dy*c) for dx, dy in shape.vertices]
                box = min(v[0] for v in vertices), min(v[1] for v in vertices), max(v[0] for v in vertices), max(v[1] for v in vertices)
            for component in owners:
                component.pads.append(pad)
                extend_bounds(component, *box)

    pending = None
    owners = None
    offset = next_update = 0
    with path.open("rb", buffering=1024 * 1024) as handle:
        for line_no, raw in enumerate(handle, 1):
            offset += len(raw)
            if offset >= next_update:
                checkpoint(cancel)
                if progress:
                    progress("SPD 패드 분석", offset, data.signature["size"])
                next_update = offset + 1024 * 1024
            if pending is not None and raw.lstrip().startswith(b"+"):
                pending += " " + raw.decode(encoding).strip().lstrip("+")
                continue
            if pending is not None:
                try:
                    record(pending, owners)
                except (ValueError, KeyError, IndexError) as exc:
                    raise ValueError(f"Invalid SPD pin geometry near line {line_no}: {exc}") from exc
                pending = None
            if raw.lstrip().startswith(b"Node"):
                token = raw.split(None, 1)[0].split(b"::", 1)[0]
                if token in encoded:
                    pending, owners = raw.decode(encoding).strip(), encoded[token]
        if pending is not None:
            record(pending, owners)
    checkpoint(cancel)
    if missing:
        data.warnings.append(f"표면 핀 {missing}개는 지원하는 패드 형상이 없어 좌표 범위만 사용합니다.")


def read_spd(path: str | Path, progress=None, cancel=None) -> SpdData:
    path = Path(path)
    signature = source_signature(path)
    try:
        result = _scan(path, signature, "utf-8-sig", progress, cancel)
    except UnicodeDecodeError:
        try:
            result = _scan(path, signature, "cp949", progress, cancel)
        except UnicodeDecodeError as exc:
            raise ValueError("SPD identifiers cannot be decoded as UTF-8 or CP949.") from exc
        result.warnings.append("SPD text was decoded as CP949.")
    if source_signature(path) != signature:
        raise ValueError("SPD changed while being read; reload the source.")
    return result


def _scan(path, signature, encoding, progress, cancel):
    data = SpdData(str(path.resolve()), signature)
    components = {}
    placements = {}
    parts = {}
    node_references = {}
    padstacks = {}
    padstack = padlayer = None
    regular_lines = []
    regular_continuation = False
    unsupported_pads = 0
    section = ""
    model = None
    model_elements = 0
    model_invalid = False
    model_header = False
    connection = None
    port = None
    terminal = ""
    positive_nets = []
    alias_group = []
    net_group = ""
    offset = 0
    next_update = 0
    line_no = 0

    def finish_port():
        if port is not None:
            if positive_nets:
                port.net = positive_nets[0]
            port.site, port.channel = infer_site_channel(port.net, port.instance)
            if len(positive_nets) > 1:
                data.warnings.append(f"Port {port.index} has multiple positive nets; review its mapping.")
            data.ports.append(port)

    def finish_alias():
        unique = list(dict.fromkeys(alias_group))
        for name in unique:
            data.aliases.setdefault(name, [])
            data.aliases[name].extend(peer for peer in unique if peer != name and peer not in data.aliases[name])

    with path.open("rb", buffering=1024 * 1024) as handle:
        for line_no, raw in enumerate(handle, 1):
            offset += len(raw)
            if offset >= next_update:
                checkpoint(cancel)
                if progress:
                    progress("SPD 분석", offset, signature["size"])
                next_update = offset + 1024 * 1024
            stripped = raw.lstrip()
            if not section and not stripped.startswith((b".PartialCkt", b".Connect ", b".Component ", b".Part ", b".Port", b".NetAlias", b".NetList", b".PadStackDef ")):
                continue
            text = raw.decode(encoding).strip()
            try:
                if section == "padstack":
                    if text.startswith(".PadDef "):
                        padlayer = text.split()[1]
                        regular_lines = []
                        regular_continuation = False
                    elif text.startswith("Regular "):
                        regular_lines = [text]
                        regular_continuation = True
                    elif text.startswith(("Anti ", "Thermal ")):
                        regular_continuation = False
                    elif text.startswith("+") and regular_continuation:
                        regular_lines.append(text.lstrip("+ "))
                    elif text.startswith(".EndPadDef"):
                        if regular_lines:
                            shape = _pad_shape(regular_lines)
                            if shape is not None:
                                padstacks[padstack][padlayer] = shape
                            else:
                                unsupported_pads += 1
                        regular_lines = []
                        regular_continuation = False
                    elif text.startswith(".EndPadStackDef"):
                        section = ""
                    continue
                if section == "model":
                    if text.lower().startswith(".endpartialckt"):
                        model.valid = model_elements > 0 and not model_invalid
                        model.capacitance_f = model.comment_capacitance_f or model.name_capacitance_f
                        model.conflict = model.conflict or bool(model.comment_capacitance_f and model.name_capacitance_f and not math.isclose(model.comment_capacitance_f, model.name_capacitance_f, rel_tol=1e-6))
                        if model.conflict:
                            data.warnings.append("A model name and its nominal capacitance comment disagree; review model evidence.")
                        section = ""
                    elif text.startswith("*"):
                        if re.search(r"\bProperty\s*:\s*C\s*=", text, re.I):
                            value = parse_capacitance(text.split("=", 1)[1])
                            if value is not None:
                                if model.comment_capacitance_f is not None and not math.isclose(model.comment_capacitance_f, value, rel_tol=1e-6):
                                    model.conflict = True
                                    data.warnings.append("A model has conflicting nominal capacitance comments.")
                                model.comment_capacitance_f = value
                                model.evidence = text
                        elif model.comment_capacitance_f is None and re.search(r"\bDescription\s*:", text, re.I):
                            model.comment_capacitance_f = parse_capacitance(text.split(":", 1)[1])
                            model.evidence = text
                    elif text and not (text.startswith("+") and model_header):
                        model_header = False
                        tokens = text.split()
                        if text.startswith("+"):
                            pass
                        elif tokens[0][0].upper() in "RCLKXDQMVIBEFGHJSWTUOZ" and len(tokens) >= 4:
                            model_elements += 1
                            if tokens[0][0].upper() in "RCL":
                                value = _SPICE_VALUE.fullmatch(tokens[3])
                                if not value or not math.isfinite(float(value[1])):
                                    model_invalid = True
                        elif not text.startswith("."):
                            model_invalid = True
                    continue
                if section == "connect":
                    if text.lower().startswith(".endc"):
                        section = ""
                    elif _PIN.match(text):
                        node = text.split()[1].removeprefix("$Package.").split("::", 1)[0]
                        owners = node_references.setdefault(node, [])
                        if not any(owner is connection for owner in owners):
                            owners.append(connection)
                        if "::" in text:
                            net = text.split("::", 1)[1].strip()
                            if net not in connection.nets:
                                connection.nets.append(net)
                    elif text.startswith(".Connect"):
                        raise ValueError("Unterminated SPD connection block.")
                    continue
                if section == "port":
                    if text.lower().startswith(".endport"):
                        finish_port()
                        section = ""
                        port = None
                    elif _PORT.match(text):
                        finish_port()
                        match = _PORT.match(text)
                        props = dict(_PROPERTIES.findall(text))
                        name = text.split()[0]
                        instance = props.get("GenFromCktInstance", "").strip('"')
                        port = Port(int(match[1]), name, net=name.split("::", 1)[1] if "::" in name else "", instance=instance, enabled="Disabled" not in text.split()[1:])
                        positive_nets = []
                        terminal = ""
                    elif port is not None:
                        if "PositiveTerminal" in text:
                            terminal = "positive"
                        elif "NegativeTerminal" in text:
                            terminal = "negative"
                        elif not text.startswith("+"):
                            terminal = ""
                        if terminal == "positive":
                            for token in re.findall(r"\$Package\.Node\S+", text):
                                if "::" in token and token.split("::", 1)[1] not in positive_nets:
                                    positive_nets.append(token.split("::", 1)[1])
                    continue
                if section == "alias":
                    if text.lower().startswith(".endnetalias"):
                        finish_alias()
                        section = ""
                    elif text and not text.startswith("*"):
                        if not text.startswith("+"):
                            finish_alias()
                            alias_group = []
                        alias_group.extend(text.lstrip("+").split())
                    continue
                if section == "netlist":
                    if text.lower().startswith(".endnetlist"):
                        section = ""
                    elif text and not text.startswith("*"):
                        arrow = re.match(r"(\S+)\s*->\s*(\S+)", text)
                        if arrow:
                            net_group = arrow[2].split("::")[0]
                            net = arrow[1].split("::")[0]
                        else:
                            net = text.split()[0].split("::")[0]
                        if net_group.lower() == "groundnets" and net not in data.ground_nets:
                            data.ground_nets.append(net)
                    continue
                tokens = text.split()
                if text.startswith(".PadStackDef "):
                    padstack = tokens[1]
                    padstacks[padstack] = {}
                    section = "padstack"
                elif text.startswith(".PartialCkt "):
                    name = text.split(" ExtNode", 1)[0].split(None, 1)[1]
                    if name in data.models:
                        raise ValueError("Duplicate SPD model definition.")
                    model = SpiceModel(name, name_capacitance_f=parse_capacitance(name))
                    data.models[name] = model
                    model_elements, model_invalid, model_header = 0, False, True
                    section = "model"
                elif text.startswith(".Connect "):
                    if len(tokens) < 3 or tokens[1] in components:
                        raise ValueError("Invalid or duplicate SPD instance assignment.")
                    props = dict(_PROPERTIES.findall(text))
                    status = {"0b1000": "Enabled", "0b111000": "Disabled", "": "Automatic"}.get(props.get("Usage", ""), "Unknown")
                    connection = Component(tokens[1], tokens[2], status=status)
                    components[connection.refdes] = connection
                    section = "connect"
                elif text.startswith(".Component "):
                    if len(tokens) < 4 or tokens[1] in placements:
                        raise ValueError("Invalid or duplicate SPD placement.")
                    props = dict(_PROPERTIES.findall(text))
                    rotation = float(props.get("Rotation", "0"))
                    if not math.isfinite(rotation):
                        raise ValueError("Invalid SPD rotation.")
                    side = "Bottom" if props.get("AttachLayer", "").lower() == "bottomair" or "BOTTOM" in props.get("StartLayer", "").upper() else "Top"
                    if tokens[2:4] == ['""', '""']:
                        placements[tokens[1]] = (None, None, rotation, side)
                        data.warnings.append("부품 배치 좌표가 없습니다. 연결된 핀 좌표가 있으면 패드와 핀 범위를 표시합니다.")
                    else:
                        placements[tokens[1]] = (_dimension(tokens[2]), _dimension(tokens[3]), rotation, side)
                elif text.startswith(".Part "):
                    match = re.search(r"\bOutline\s*=\s*(\S+)\s+(\S+)", text)
                    if match:
                        dimensions = _dimension(match[1]), _dimension(match[2])
                        if min(dimensions) <= 0:
                            raise ValueError("SPD part outline must have positive dimensions.")
                        parts[tokens[1]] = dimensions
                elif text.lower() == ".port":
                    section = "port"
                elif text.lower() == ".netalias":
                    section, alias_group = "alias", []
                elif text.lower() == ".netlist":
                    section, net_group = "netlist", ""
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Invalid SPD structure at line {line_no}: {exc}") from exc
    checkpoint(cancel)
    if section:
        raise ValueError(f"Unterminated SPD {section} section near line {line_no}.")
    if not components and not data.models and not data.ports:
        raise ValueError("No supported SPD model, component or port records were found.")
    if len({port.name for port in data.ports}) != len(data.ports):
        raise ValueError("Duplicate SPD port names make channel mapping ambiguous.")
    for component in components.values():
        model = data.models.get(component.model_name)
        component.is_cap = bool((model and model.capacitance_f is not None) or re.match(r"(?:CAP(?:[_-]|$)|C\d)", component.model_name, re.I) or re.match(r"C\d", component.refdes, re.I))
        if component.refdes in placements:
            component.x_mm, component.y_mm, component.rotation, component.side = placements[component.refdes]
        if component.model_name in parts:
            component.width_mm, component.height_mm = parts[component.model_name]
    data.components = list(components.values())
    if node_references:
        _attach_surface_pads(path, data, node_references, padstacks, encoding, progress, cancel)
    if unsupported_pads:
        data.warnings.append(f"패드 정의 {unsupported_pads}개는 지원하지 않는 형상이어서 그리지 않습니다.")
    # shortcut: copper contours are not board edges; add explicit outline parsing when inputs supply it.
    data.warnings.append("보드 외곽선 정보가 없어 부품 좌표와 크기로 배치도를 표시합니다.")
    if any(c.is_cap and c.status == "Unknown" for c in data.components):
        data.warnings.append("Components with unknown activation flags are excluded from active capacitor counts.")
    if progress:
        progress("SPD 분석 완료", signature["size"], signature["size"])
    return data
