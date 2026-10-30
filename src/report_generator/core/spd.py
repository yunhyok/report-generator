from __future__ import annotations

import math
import re
from pathlib import Path

from .capacitance import parse_capacitance
from .models import Component, Port, SpiceModel, SpdData, checkpoint, source_signature

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
            if not section and not stripped.startswith((b".PartialCkt", b".Connect ", b".Component ", b".Part ", b".Port", b".NetAlias", b".NetList")):
                continue
            text = raw.decode(encoding).strip()
            try:
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
                if text.startswith(".PartialCkt "):
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
                        data.warnings.append("An instance has no placement coordinates and is omitted from the placement image.")
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
    # shortcut: copper contours are not board edges; add explicit outline parsing when inputs supply it.
    data.warnings.append("Exact board outline is unavailable; placement view shows component geometry only.")
    if any(c.is_cap and c.status == "Unknown" for c in data.components):
        data.warnings.append("Components with unknown activation flags are excluded from active capacitor counts.")
    if progress:
        progress("SPD 분석 완료", signature["size"], signature["size"])
    return data
