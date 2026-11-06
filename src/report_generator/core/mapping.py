from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from .models import ChannelMapping, Port, ReportConfig, SpdData, TouchstoneData
from .spd import infer_site_channel


def priority_value(text: str) -> int | str | None:
    """A channel has a positive rank, an unranked section name, or neither."""
    text = text.strip()
    if not text:
        return None
    if text.isdecimal() and len(text) <= 15 and int(text) > 0:
        return int(text)
    try:
        float(text)
    except ValueError:
        if len(text) <= 32767 and not re.search(r"[\x00-\x1f]", text):
            return text
    raise ValueError("순위는 1 이상의 정수로, 별도 구분은 이름으로 입력하세요.")


def plot_group(mapping: ChannelMapping) -> str:
    return mapping.group or mapping.channel


def group_presentation(group: str, mappings: list[ChannelMapping]) -> tuple[str, bool]:
    names = {mapping.display_name.strip() for mapping in mappings}
    if len(names) == 1 and "" not in names:
        return names.pop(), False
    return group, bool(names - {""})


def _identity(port: Port) -> tuple[str, str, str]:
    description = re.fullmatch(r"Port\d+_([^:]+)::(.+)", port.description or port.name, re.IGNORECASE)
    instance = port.instance or (description.group(1) if description else "")
    named_family = re.match(r"^((?:LGA|POGO|BGA|DIE|LOAD|SOURCE)\d+)[_:-]+", port.name, re.IGNORECASE)
    family = named_family.group(1) if named_family else (
        instance if re.fullmatch(r"(?:LGA|POGO|BGA|DIE|LOAD|SOURCE)\d+", instance, re.IGNORECASE) else "")
    text = port.net or port.name
    text = text.rsplit("::", 1)[-1].strip()
    text = re.sub(r"^Port\d+_", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:LGA|POGO|BGA|DIE|LOAD|SOURCE)\d*[_:-]+", "", text, flags=re.IGNORECASE)
    site = str(port.site).strip()
    prefix = re.match(r"^(?:SITE|U)(\d+)[_:-]+(.+)$", text, re.IGNORECASE)
    if prefix:
        site = site or prefix.group(1)
        text = prefix.group(2)
    suffix = re.fullmatch(r"(.+)/(\d+)", text)
    if suffix:
        text, inferred = suffix.groups()
        site = site or inferred
    inferred_site, channel = infer_site_channel(text, instance)
    channel = port.channel or channel
    site = site or inferred_site
    description_net = ""
    if description and not port.net:
        description_net = description.group(2)
        description_site, description_channel = infer_site_channel(description_net, description.group(1))
        normalized_site = f"SITE{int(site)}" if site.isdecimal() else site.upper()
        if (description_channel != channel
                or (description_site and site and description_site != normalized_site)
                or (named_family and description.group(1).casefold() != family.casefold())):
            return port.net or text, "", f"{family}-{channel}" if family else channel
        site = site or description_site
    if not site:
        context = re.search(r"(?:SITE|\bU)(\d+)(?:\b|[_:-])",
                            " ".join((port.description, port.instance, port.name)), re.IGNORECASE)
        if context:
            site = context.group(1)
    site = re.sub(r"^SITE\s*", "", site, flags=re.IGNORECASE)
    if site.isdecimal():
        site = f"SITE{int(site)}"
    return port.net or description_net or text, site, f"{family}-{channel}" if family else channel


def suggest_mappings(spd: SpdData | None, ts: TouchstoneData | None) -> list[ChannelMapping]:
    """Preserve every endpoint; duplicate channel/site candidates require explicit confirmation."""
    spd_ports = spd.ports if spd else []
    spd_candidates: dict[tuple[str, str], list[Port]] = {}
    spd_names: dict[str, list[Port]] = {}
    for port in spd_ports:
        spd_candidates.setdefault(_identity(port)[1:], []).append(port)
        for name in {port.name, port.description} - {""}:
            spd_names.setdefault(name, []).append(port)
    rows: list[ChannelMapping] = []
    ports = ts.ports if ts else spd_ports
    for port in ports:
        net, site, channel = _identity(port)
        candidates = spd_candidates.get((site, channel), [])
        if ts:
            descriptor = port.description or port.name
            if re.fullmatch(r"Port\d+_[^:]+::.+", descriptor, re.IGNORECASE):
                _, label_site, label_channel = _identity(replace(port, description=""))
                candidates = [p for p in spd_names.get(descriptor, [])
                              if _identity(p)[2] == label_channel
                              and (not label_site or _identity(p)[1] == label_site)]
                if len(candidates) == 1:
                    net, site, channel = _identity(candidates[0])
            elif len(candidates) > 1 and port.description:
                matching = [p for p in candidates if p.name == port.description or p.description == port.description]
                if matching:
                    candidates = matching
        if ts and len(candidates) == 1:
            net = candidates[0].net or net
        confirmed = bool(channel and site != "") and (not ts or not spd or len(candidates) == 1)
        rows.append(ChannelMapping(
            port_index=port.index if ts else None,
            source_name=port.name + (f" | {port.description}" if port.description and port.description != port.name else ""),
            spd_port=candidates[0].name if len(candidates) == 1 else (port.name if not ts else ""),
            net=net, site=site, channel=channel, group=channel, selected=port.enabled,
            status="자동 제안" if confirmed else "매핑 확인 필요", confirmed=confirmed))
    if spd and not ts:
        existing = {row.net for row in rows}
        ground = {net.casefold() for net in spd.ground_nets}
        for component in spd.components:
            if not component.is_cap:
                continue
            for net in component.nets:
                if net.casefold() in ground:
                    continue
                raw_net, site, channel = _identity(Port(0, net, net=net))
                if raw_net in existing:
                    continue
                existing.add(raw_net)
                rows.append(ChannelMapping(source_name=net, net=raw_net, site=site, channel=channel,
                                           group=channel, status="커패시터 네트에서 제안", confirmed=bool(site)))
    counts = Counter((row.channel.casefold(), row.site.casefold()) for row in rows)
    for row in rows:
        if counts[(row.channel.casefold(), row.site.casefold())] > 1:
            row.confirmed = False
            row.status = "같은 채널·사이트에 포트가 여러 개입니다. 사용할 단자를 선택하고 확인하세요."
    return rows


def validate_mappings(config: ReportConfig | list[ChannelMapping],
                      touchstone: TouchstoneData | None = None,
                      spd: SpdData | None = None) -> None:
    rows = config.mappings if isinstance(config, ReportConfig) else config
    selected = [row for row in rows if row.selected]
    if not selected:
        if isinstance(config, ReportConfig) and config.dcr:
            return
        raise ValueError("보고서에 포함할 채널을 하나 이상 선택하세요.")
    identities: set[tuple[str, str]] = set()
    ports: set[int] = set()
    available = {port.index for port in touchstone.ports} if touchstone else None
    spd_ports = {port.name for port in spd.ports} if spd else None
    ranks = {}
    for row in selected:
        if not row.channel.strip() or not str(row.site).strip():
            raise ValueError("선택한 행의 채널과 사이트를 입력하세요.")
        key = (row.channel.strip().casefold(), str(row.site).strip().casefold())
        if key in identities:
            raise ValueError(f"채널 {row.channel}, 사이트 {row.site}가 중복되었습니다. 사용할 단자를 하나만 선택하세요.")
        identities.add(key)
        if isinstance(config, ReportConfig) and config.settings.use_priority:
            rank = priority_value(row.priority)
            if ranks.setdefault(key[0], rank) != rank:
                raise ValueError(f"{row.channel}: 사이트마다 순위 또는 구분명이 다릅니다. 채널 순위 설정에서 하나로 맞추세요.")
        if not row.confirmed:
            raise ValueError(f"채널 {row.channel}, 사이트 {row.site}의 모호한 매핑을 확인하세요.")
        if spd_ports is not None and row.spd_port and row.spd_port not in spd_ports:
            raise ValueError(f"SPD 포트를 찾을 수 없습니다: {row.spd_port}. 입력 파일의 포트 이름을 확인하세요.")
        if row.port_index is not None:
            if isinstance(row.port_index, bool) or not isinstance(row.port_index, int) or row.port_index < 1:
                raise ValueError("Touchstone 포트 번호는 1 이상의 정수여야 합니다.")
            if available is not None and row.port_index not in available:
                raise ValueError(f"Touchstone 포트 번호가 범위를 벗어났습니다: {row.port_index}")
            if row.port_index in ports:
                raise ValueError(f"Touchstone 포트 {row.port_index}가 여러 행에 선택되었습니다.")
            ports.add(row.port_index)
