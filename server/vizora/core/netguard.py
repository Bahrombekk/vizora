"""Operator kiritgan RTSP manzili ICHKI xizmatga ishora qilmasin (SSRF / port skan).

`POST /api/cameras/test` va kamera saqlash URL'ni ffprobe bilan ochadi (probe sikli
ham har 15 s da). Tekshiruvsiz `rtsp://postgres:5432`, `rtsp://redis:6379` yoki
Docker tarmog'idagi istalgan IP:port berib, javob vaqti/natijasidan ichki tarmoq
xaritasini olish mumkin edi (audit 2026-11-03, 9-band).

Rad etiladi:
  * compose xizmat nomlari va backend o'zi ulanadigan hostlar (DB, Redis, MediaMTX…);
  * loopback, link-local, unspecified, multicast;
  * konteyner TO'G'RIDAN ulangan tarmoqlar (Docker bridge — ichki xizmatlar shu yerda;
    `/proc/net/route` dan olinadi). Kameralar LAN'i esa marshrut orqali — unga tegilmaydi.

DNS rebinding'dan (tekshiruvdan keyin nom boshqa IP'ga o'tishi) to'liq himoya emas —
bu chuqurlashtirilgan mudofaa; asosiy chegara rol (operator) va Docker tarmog'i.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlsplit

from vizora.core.config import settings

_SERVICE_NAMES = {"localhost", "postgres", "redis", "app", "mediamtx", "isup-bridge", "web"}


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _internal_names() -> set[str]:
    names = set(_SERVICE_NAMES)
    for u in (settings.database_url, settings.redis_url, settings.mediamtx_api_url,
              settings.mediamtx_hls_url, settings.mediamtx_webrtc_url, settings.isup_bridge_url):
        h = _host_of(u)
        if h:
            names.add(h)
    if settings.mediamtx_rtsp_host:
        names.add(settings.mediamtx_rtsp_host.lower())
    try:
        names.add(socket.gethostname().lower())
    except OSError:
        pass
    return names


@lru_cache(maxsize=1)
def _local_subnets() -> tuple[ipaddress.IPv4Network, ...]:
    """Konteyner to'g'ridan ulangan IPv4 tarmoqlar (gateway'siz marshrutlar)."""
    nets = []
    try:
        with open("/proc/net/route") as f:
            next(f, None)
            for line in f:
                p = line.split()
                if len(p) < 8:
                    continue
                dest, gw, mask = (int(x, 16) for x in (p[1], p[2], p[7]))
                if dest == 0 or gw != 0:
                    continue   # default yoki gateway orqali — tashqi tarmoq
                addr = ipaddress.IPv4Address(socket.ntohl(dest))
                bits = bin(mask).count("1")
                nets.append(ipaddress.IPv4Network(f"{addr}/{bits}", strict=False))
    except (OSError, ValueError):
        pass
    return tuple(nets)


def _ip_internal(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return _ip_internal(ip.ipv4_mapped)
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return True
    return ip.version == 4 and any(ip in n for n in _local_subnets())


async def internal_reason(url: str | None) -> str | None:
    """URL ichki xizmatga ishora qilsa sabab matni, aks holda None."""
    if not url or not settings.rtsp_block_internal:
        return None
    host = _host_of(url)
    if not host:
        return "RTSP URL'da host yo'q"
    if host in _internal_names():
        return f"'{host}' — serverning ichki xizmati, kamera emas"
    try:
        ips = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
        except (OSError, asyncio.TimeoutError):
            return None   # hal qilinmadi — ffprobe ham ulanolmaydi
        ips = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}
    for ip in ips:
        if _ip_internal(ip):
            return f"'{host}' ({ip}) — ichki tarmoq manzili, kamera emas"
    return None
