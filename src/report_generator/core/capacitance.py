from __future__ import annotations

import math
import re
from collections import defaultdict

from .models import Component, ReportConfig, SpdData

_VALUE = re.compile(
    r"(?<![A-Za-z0-9.])([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)"
    r"\s*\[?\s*(p|n|u|µ|μ|m)?\s*f?\s*\]?"
    r"(?=$|[^A-Za-z0-9.])", re.IGNORECASE,
)
_FACTORS = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0}


def parse_capacitance(text: str) -> float | None:
    """Read a capacitance or a unit-bearing token in a component name."""
    normalized = str(text).strip()
    if re.search(r"(?:^|[=\s])-\s*\d", normalized):
        return None
    # Package sizes and part numbers are not capacitance evidence.
    for match in _VALUE.finditer(normalized):
        unit = match.group(2) or ""
        token = match.group(0).strip()
        if unit.lower() == "m" and not re.search(r"f\s*\]?$", token, re.I):
            continue
        if not unit and not re.search(r"f\s*\]?$", token, re.I):
            if match.span() != (0, len(normalized)):
                continue
        value = float(match.group(1)) * _FACTORS[unit.lower()]
        if math.isfinite(value) and value > 0:
            return value
    return None


def format_capacitance(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Capacitance must be finite and positive.")
    for suffix, factor in (("F", 1.0), ("mF", 1e-3), ("uF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)):
        if value >= factor * (1 - 1e-12):
            return f"{value / factor:.6g}{suffix}"
    return f"{value:.6g}F"


def resolved_capacitance(component: Component, spd: SpdData, config: ReportConfig) -> float | None:
    value = config.component_overrides.get(component.refdes, config.model_overrides.get(component.model_name))
    if value is None:
        model = spd.models.get(component.model_name)
        value = model.capacitance_f if model else None
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError("Capacitance overrides must be finite and positive.")
    return value


def component_active(component: Component, spd: SpdData) -> bool:
    if component.status == "Enabled":
        return True
    model = spd.models.get(component.model_name)
    return component.status == "Automatic" and model is not None and model.valid


def summarize_caps(spd: SpdData, config: ReportConfig) -> list[dict]:
    """Count unique active capacitors on explicitly selected rail nets."""
    grounds = set(spd.ground_nets) | set(config.settings.ground_nets)
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mapping in config.mappings:
        if mapping.selected and mapping.net:
            groups[mapping.site, mapping.channel].add(mapping.net)
    for nets in groups.values():
        pending = list(nets)
        while pending:
            for alias in spd.aliases.get(pending.pop(), []):
                if alias not in nets and alias not in grounds:
                    nets.add(alias)
                    pending.append(alias)
        nets.difference_update(grounds)
    buckets: dict[tuple[str, str, float | None], dict] = {}
    for (site, channel), nets in groups.items():
        seen = set()
        for component in spd.components:
            if component.refdes in seen or not component.is_cap or not component_active(component, spd):
                continue
            power_nets = set(component.nets) - grounds
            if not power_nets.intersection(nets) or not set(component.nets).intersection(grounds):
                continue
            seen.add(component.refdes)
            value = resolved_capacitance(component, spd, config)
            key = site, channel, value
            row = buckets.setdefault(key, {
                "site": site, "channel": channel, "capacitance_f": value,
                "label": format_capacitance(value), "count": 0, "refdes": [],
                "model_names": [], "unresolved": value is None,
            })
            row["count"] += 1
            row["refdes"].append(component.refdes)
            if component.model_name not in row["model_names"]:
                row["model_names"].append(component.model_name)
    return list(buckets.values())
