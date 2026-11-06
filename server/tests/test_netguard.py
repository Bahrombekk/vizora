"""RTSP URL ichki xizmatga ishora qilmasin (audit 2026-11-03, 9-band).

`/api/cameras/test` operator bergan URL'ni ffprobe bilan ochadi — tekshiruvsiz
`rtsp://postgres:5432` kabi manzillar bilan ichki tarmoqni skanerlash mumkin edi.
"""
import ipaddress

import pytest
from fastapi import HTTPException

from vizora.api.panel import cameras as cam_mod
from vizora.core import netguard
from vizora.core.config import settings

_REAL_SUBNETS = netguard._local_subnets.__wrapped__   # lru_cache ostidagi asl funksiya


@pytest.fixture(autouse=True)
def _docker_net(monkeypatch):
    # Konteyner 172.18.0.0/16 Docker tarmog'ida turibdi deb faraz qilamiz.
    monkeypatch.setattr(netguard, "_local_subnets",
                        lambda: (ipaddress.IPv4Network("172.18.0.0/16"),))
    monkeypatch.setattr(settings, "rtsp_block_internal", True)


@pytest.mark.parametrize("url", [
    "rtsp://postgres:5432/x",
    "rtsp://redis:6379",
    "rtsp://MediaMTX:9997/v3/config",
    "rtsp://isup-bridge:8091/keys",
    "rtsp://localhost:8554/a",
    "rtsp://127.0.0.1/a",
    "rtsp://[::1]/a",
    "rtsp://0.0.0.0/a",
    "rtsp://169.254.169.254/latest",
    "rtsp://172.18.0.5:5432",
    "rtsp://user:p%40ss@172.18.3.4/a",
])
async def test_internal_targets_rejected(url):
    assert await netguard.internal_reason(url)


@pytest.mark.parametrize("url", [
    "rtsp://admin:x@192.168.1.64:554/Streaming/Channels/101",
    "rtsp://10.20.30.40/live",
    "rtsps://cam.example.uz:322/s",
    "rtsp://172.19.0.5/a",          # boshqa (marshrutlangan) 172 tarmoq — kamera LAN'i bo'lishi mumkin
])
async def test_camera_lan_allowed(url):
    assert await netguard.internal_reason(url) is None


async def test_escape_hatch(monkeypatch):
    monkeypatch.setattr(settings, "rtsp_block_internal", False)
    assert await netguard.internal_reason("rtsp://127.0.0.1/a") is None


async def test_hostname_resolving_inside_is_rejected(monkeypatch):
    class _Loop:
        async def getaddrinfo(self, host, port, type=0):
            return [(2, 1, 6, "", ("172.18.0.9", 0))]

    monkeypatch.setattr(netguard.asyncio, "get_running_loop", lambda: _Loop())
    assert await netguard.internal_reason("rtsp://evil.example.com/a")


async def test_guard_raises_400():
    with pytest.raises(HTTPException) as e:
        await cam_mod._guard_rtsp("rtsp://ok.example/a", "rtsp://redis:6379")
    assert e.value.status_code == 400


def test_parse_proc_route(tmp_path, monkeypatch):
    """`/proc/net/route` dan faqat gateway'siz (to'g'ridan ulangan) tarmoqlar olinadi."""
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    real_open = open
    monkeypatch.setattr("builtins.open",
                        lambda p, *a, **k: real_open(route, *a, **k) if p == "/proc/net/route"
                        else real_open(p, *a, **k))
    nets = _REAL_SUBNETS()
    assert nets == (ipaddress.IPv4Network("172.18.0.0/16"),)
