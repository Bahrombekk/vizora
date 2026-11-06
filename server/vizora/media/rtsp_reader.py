"""Minimal asinxron RTSP/RTP o'quvchi — MediaMTX'dan KODLANGAN kadrlarni oladi.

Maqsad: brauzerga WebSocket + WebCodecs orqali uzatish uchun H.264/H.265 access
unit'larini dekod/enkodsiz olish (`ws_stream.py`). ffmpeg subprocess emas — bitta
TCP soket, RTP paketlarni o'zimiz yig'amiz: eng past kechikish, RTP timestamp'lar
saqlanadi (WebCodecs'ga aniq vaqt kerak), CPU ~0.

Qamrov ataylab tor: faqat RTP/AVP/TCP (interleaved), faqat video track, faqat
H264/H265. MediaMTX shu shaklda beradi; boshqa serverlar maqsad emas.

Chiqish: `AccessUnit(nals, ts_us, key)` — Annex-B'siz xom NAL'lar ro'yxati
(start-code'larni iste'molchi qo'yadi), mikrosekund timestamp (90kHz'dan, wrap
hisobga olingan), kalit kadr bayrog'i (IDR/IRAP).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse

log = logging.getLogger("vizora.rtsp")

_KEEPALIVE_SECONDS = 8.0     # MediaMTX readTimeout (10s) dan kichik bo'lishi shart
_CONNECT_TIMEOUT = 20.0      # on-demand manba (kamera) ochilishini DESCRIBE kutadi
_READ_TIMEOUT = 15.0         # shu vaqt hech narsa kelmasa — oqim o'lgan
# Bitta NAL / bitta kadr (AU) uchun yuqori chegara — 8K I-kadr ham bundan ancha kichik.
_MAX_NAL_BYTES = 8 * 1024 * 1024
_MAX_AU_BYTES = 16 * 1024 * 1024

H264 = "h264"
H265 = "h265"


class RtspError(RuntimeError):
    pass


@dataclass
class Track:
    codec: str                 # H264 | H265
    payload_type: int
    control: str               # SETUP uchun to'liq URL
    vps: bytes | None = None
    sps: bytes | None = None
    pps: bytes | None = None
    clock: int = 90000


@dataclass
class AccessUnit:
    nals: list[bytes]
    ts_us: int
    key: bool
    codec: str = H264

    def annexb(self, params: tuple[bytes, ...] = ()) -> bytes:
        """Annex-B bayt oqimi. `params` (VPS/SPS/PPS) berilsa va AU'da param-set
        bo'lmasa, oldiga qo'shiladi — har kalit kadr o'z-o'zidan dekodlanadigan
        bo'lsin (yangi tomoshabin istalgan GOP boshidan boshlaydi)."""
        out = bytearray()
        if params and not any(is_param_set(self.codec, n) for n in self.nals):
            for p in params:
                out += b"\x00\x00\x00\x01" + p
        for n in self.nals:
            out += b"\x00\x00\x00\x01" + n
        return bytes(out)


# ---------------------------------------------------------------- NAL yordamchilari

def nal_type(codec: str, nal: bytes) -> int:
    if not nal:
        return -1
    return (nal[0] & 0x1F) if codec == H264 else ((nal[0] >> 1) & 0x3F)


def is_param_set(codec: str, nal: bytes) -> bool:
    t = nal_type(codec, nal)
    return t in (7, 8) if codec == H264 else t in (32, 33, 34)


def is_keyframe_nal(codec: str, nal: bytes) -> bool:
    t = nal_type(codec, nal)
    return t == 5 if codec == H264 else 16 <= t <= 21   # IDR / IRAP (BLA, IDR, CRA)


def rbsp(nal: bytes) -> bytes:
    """NAL'dan emulation-prevention baytlarini olib tashlaydi (00 00 03 -> 00 00).

    Enkoder oqimda tasodifan start-code (00 00 01) paydo bo'lmasligi uchun
    `00 00 00/01/02/03` ketma-ketligiga 0x03 qistiradi. Uni olib tashlamasdan
    SPS'ni bayt bo'yicha o'qish SURILGAN natija beradi: o'lchandi — 3200x1800
    kamerada codecString `hev1.1.C0000006.L0.00.00.03.00.00.03` chiqdi (daraja
    **0**, ichida 03 baytlari). Brauzer shunday satr bilan dekoderni noto'g'ri
    sozlab, 1412 kadrdan faqat 177 tasini chizdi (3 fps, uzilishlar bilan)."""
    out = bytearray()
    i, n = 0, len(nal)
    while i < n:
        if i + 2 < n and nal[i] == 0 and nal[i + 1] == 0 and nal[i + 2] == 3:
            out += b"\x00\x00"
            i += 3
        else:
            out.append(nal[i])
            i += 1
    return bytes(out)


def codec_string(codec: str, sps: bytes) -> str:
    """WebCodecs `codec` satri SPS'dan: `avc1.PPCCLL` yoki `hev1.P.C.LxxL.Bx`.

    H.264: profile_idc, constraint_set bayti, level_idc — SPS'ning 1..3 baytlari.
    H.265: profile_tier_level (ISO 14496-15 Annex E formati): profil, moslik
    bayroqlari (bit tartibi TESKARI, oxirgi nollar tashlanadi), tier (L/H) +
    level_idc, cheklov baytlari (oxirgi nol baytlar tashlanadi)."""
    sps = rbsp(sps)   # 0x03 qistirmalari bayt o'rinlarini SURADI — avval tozalaymiz
    if codec == H264:
        if len(sps) < 4:
            return "avc1.42E01E"  # Baseline 3.0 — hech bo'lmaganda dekoder ochilsin
        return f"avc1.{sps[1]:02X}{sps[2]:02X}{sps[3]:02X}"
    # H.265: 2 bayt NAL sarlavha, 1 bayt (vps_id/max_sub_layers/nesting), so'ng PTL 12 bayt
    if len(sps) < 15:
        return "hev1.1.6.L93.B0"  # Main 3.1
    ptl = sps[3:15]
    profile_space = ptl[0] >> 6
    tier = (ptl[0] >> 5) & 1
    profile_idc = ptl[0] & 0x1F
    compat = int.from_bytes(ptl[1:5], "big")
    rev = int(f"{compat:032b}"[::-1], 2)
    compat_hex = f"{rev:X}".rstrip("0") or "0"
    constraint = list(ptl[5:11])
    while constraint and constraint[-1] == 0:
        constraint.pop()
    level_idc = ptl[11]
    s = f"hev1.{['', 'A', 'B', 'C'][profile_space]}{profile_idc}.{compat_hex}.{'H' if tier else 'L'}{level_idc}"
    if constraint:
        s += "." + ".".join(f"{b:02X}" for b in constraint)
    return s


# ---------------------------------------------------------------- SDP

_RTPMAP = re.compile(r"a=rtpmap:(\d+)\s+(H264|H265|HEVC)/(\d+)", re.I)


def parse_sdp(sdp: str, base_url: str) -> Track:
    """SDP'dan video track'ni topadi (H264/H265) va sprop param-setlarni oladi."""
    sections = re.split(r"(?m)^m=", sdp)
    for sec in sections[1:]:
        if not sec.startswith("video"):
            continue
        m = _RTPMAP.search(sec)
        if not m:
            continue
        pt = int(m.group(1))
        codec = H264 if m.group(2).upper() == "H264" else H265
        clock = int(m.group(3))
        ctrl_m = re.search(r"(?m)^a=control:(\S+)", sec)
        control = ctrl_m.group(1) if ctrl_m else ""
        if control == "*" or not control:
            control = base_url
        elif not control.lower().startswith("rtsp://"):
            control = base_url.rstrip("/") + "/" + control
        tr = Track(codec=codec, payload_type=pt, control=control, clock=clock)
        fm = re.search(rf"(?m)^a=fmtp:{pt}\s+(.+)$", sec)
        if fm:
            params = dict(
                kv.split("=", 1) for kv in fm.group(1).split(";") if "=" in kv.strip()
            )
            params = {k.strip(): v.strip() for k, v in params.items()}
            if codec == H264 and params.get("sprop-parameter-sets"):
                sets = [_b64(x) for x in params["sprop-parameter-sets"].split(",") if x]
                for s in sets:
                    t = nal_type(H264, s)
                    if t == 7:
                        tr.sps = s
                    elif t == 8:
                        tr.pps = s
            elif codec == H265:
                tr.vps = _b64(params["sprop-vps"]) if params.get("sprop-vps") else None
                tr.sps = _b64(params["sprop-sps"]) if params.get("sprop-sps") else None
                tr.pps = _b64(params["sprop-pps"]) if params.get("sprop-pps") else None
        return tr
    raise RtspError("SDP'da H264/H265 video track topilmadi")


def _b64(s: str) -> bytes:
    s = s.strip()
    return base64.b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- RTP

@dataclass
class RtpPacket:
    marker: bool
    payload_type: int
    seq: int
    timestamp: int
    payload: bytes


def parse_rtp(data: bytes) -> RtpPacket | None:
    if len(data) < 12 or (data[0] >> 6) != 2:
        return None
    cc = data[0] & 0x0F
    has_ext = bool(data[0] & 0x10)
    has_pad = bool(data[0] & 0x20)
    off = 12 + 4 * cc
    if has_ext:
        if len(data) < off + 4:
            return None
        ext_len = int.from_bytes(data[off + 2:off + 4], "big")
        off += 4 + 4 * ext_len
    end = len(data)
    if has_pad and end > off:
        end -= data[-1]
    if off > end:
        return None
    return RtpPacket(
        marker=bool(data[1] & 0x80),
        payload_type=data[1] & 0x7F,
        seq=int.from_bytes(data[2:4], "big"),
        timestamp=int.from_bytes(data[4:8], "big"),
        payload=data[off:end],
    )


class Depacketizer:
    """RTP payload → NAL'lar (H.264 RFC 6184 / H.265 RFC 7798) → access unit'lar.

    AU chegarasi: RTP marker biti (MediaMTX to'g'ri qo'yadi) yoki timestamp
    o'zgarishi (himoya). FU (bo'lingan NAL) qayta yig'iladi; seq sakrasa bo'lak
    tashlanadi (buzuq NAL dekoderga yuborilmaydi)."""

    def __init__(self, track: Track):
        self.track = track
        self.codec = track.codec
        self._nals: list[bytes] = []
        self._ts: int | None = None
        self._fu: bytearray | None = None
        self._fu_seq: int | None = None
        self._au_bytes = 0
        self._wraps = 0
        self._last_ts: int | None = None
        self._base_ext: int | None = None

    # 32-bit RTP timestamp -> uzluksiz mikrosekund
    def _to_us(self, ts: int) -> int:
        if self._last_ts is not None:
            if ts < self._last_ts and (self._last_ts - ts) > (1 << 31):
                self._wraps += 1
            elif ts > self._last_ts and (ts - self._last_ts) > (1 << 31) and self._wraps:
                self._wraps -= 1  # tartibsiz kelgan eski paket
        self._last_ts = ts
        ext = ts + (self._wraps << 32)
        if self._base_ext is None:
            self._base_ext = ext
        return (ext - self._base_ext) * 1_000_000 // self.track.clock

    def feed(self, pkt: RtpPacket) -> list[AccessUnit]:
        if pkt.payload_type != self.track.payload_type or not pkt.payload:
            return []
        out: list[AccessUnit] = []
        # Timestamp o'zgardi -> avvalgi AU yakunlanadi (marker yo'qolgan bo'lsa ham)
        if self._ts is not None and pkt.timestamp != self._ts and self._nals:
            out.append(self._flush())
        self._ts = pkt.timestamp
        for nal in self._nals_from(pkt):
            self._nals.append(nal)
            self._au_bytes += len(nal)
            self._learn(nal)
        if self._au_bytes > _MAX_AU_BYTES:
            # Marker ham, timestamp o'zgarishi ham kelmasa AU cheksiz o'sardi.
            log.warning("RTSP: access unit %d baytdan oshdi — tashlandi", _MAX_AU_BYTES)
            self._nals, self._au_bytes = [], 0
            return out
        if pkt.marker and self._nals:
            out.append(self._flush())
        return out

    def _flush(self) -> AccessUnit:
        nals, self._nals, self._au_bytes = self._nals, [], 0
        key = any(is_keyframe_nal(self.codec, n) for n in nals)
        au = AccessUnit(nals=nals, ts_us=self._to_us(self._ts or 0), key=key, codec=self.codec)
        return au

    def _learn(self, nal: bytes) -> None:
        """Oqim ichidagi (in-band) param-setlarni eslab qolamiz — SDP'da bo'lmasa
        yoki kamera ularni yangilasa, yangi tomoshabin so'nggisini oladi."""
        t = nal_type(self.codec, nal)
        if self.codec == H264:
            if t == 7:
                self.track.sps = nal
            elif t == 8:
                self.track.pps = nal
        else:
            if t == 32:
                self.track.vps = nal
            elif t == 33:
                self.track.sps = nal
            elif t == 34:
                self.track.pps = nal

    def _nals_from(self, pkt: RtpPacket) -> list[bytes]:
        p = pkt.payload
        if self.codec == H264:
            t = p[0] & 0x1F
            if 1 <= t <= 23:
                return [p]
            if t == 24:  # STAP-A
                return _split_aggregated(p[1:])
            if t == 28 and len(p) >= 2:  # FU-A
                return self._fu_step(pkt, start=bool(p[1] & 0x80), end=bool(p[1] & 0x40),
                                     header=bytes([(p[0] & 0xE0) | (p[1] & 0x1F)]), body=p[2:])
            return []  # STAP-B / MTAP / FU-B — MediaMTX ishlatmaydi
        t = (p[0] >> 1) & 0x3F
        if t == 48:  # AP
            return _split_aggregated(p[2:])
        if t == 49 and len(p) >= 3:  # FU
            fu_type = p[2] & 0x3F
            hdr = bytes([(p[0] & 0x81) | (fu_type << 1), p[1]])
            return self._fu_step(pkt, start=bool(p[2] & 0x80), end=bool(p[2] & 0x40),
                                 header=hdr, body=p[3:])
        if t <= 47:
            return [p]
        return []

    def _fu_step(self, pkt: RtpPacket, *, start: bool, end: bool, header: bytes, body: bytes) -> list[bytes]:
        if start:
            self._fu = bytearray(header) + body
            self._fu_seq = pkt.seq
            return []
        if self._fu is None:
            return []  # boshi yo'q bo'lak — tashlaymiz
        if self._fu_seq is not None and ((self._fu_seq + 1) & 0xFFFF) != pkt.seq:
            self._fu = None  # paket yo'qolgan — bu NAL buzuq
            return []
        self._fu_seq = pkt.seq
        self._fu += body
        if len(self._fu) > _MAX_NAL_BYTES:
            # `end` biti hech qachon kelmasa bufer cheksiz o'sardi (buzuq/yomon niyatli
            # publisher — xotira DoS). Bunday katta NAL baribir yaroqsiz.
            log.warning("RTSP: FU NAL %d baytdan oshdi — tashlandi", _MAX_NAL_BYTES)
            self._fu = None
            return []
        if end:
            nal, self._fu = bytes(self._fu), None
            return [nal]
        return []


def _split_aggregated(buf: bytes) -> list[bytes]:
    out: list[bytes] = []
    i = 0
    while i + 2 <= len(buf):
        n = int.from_bytes(buf[i:i + 2], "big")
        i += 2
        if n == 0 or i + n > len(buf):
            break
        out.append(buf[i:i + n])
        i += n
    return out


# ---------------------------------------------------------------- RTSP mijoz

class _Rtsp:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.r = reader
        self.w = writer
        self.cseq = 0
        self.session: str | None = None
        # MediaMTX o'quvchi auth'i (Basic, oldindan yuboriladi) — [[split_userinfo]].
        self.auth: str | None = None

    def extra_lines(self) -> str:
        """Keepalive/TEARDOWN kabi qo'lda yoziladigan so'rovlar uchun auth qatori."""
        return f"Authorization: {self.auth}\r\n" if self.auth else ""

    async def request(self, method: str, url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        self.cseq += 1
        lines = [f"{method} {url} RTSP/1.0", f"CSeq: {self.cseq}", "User-Agent: vizora-ws"]
        if self.auth:
            lines.append(f"Authorization: {self.auth}")
        if self.session:
            lines.append(f"Session: {self.session}")
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        self.w.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await self.w.drain()
        return await self.read_response()

    async def read_response(self) -> tuple[int, dict[str, str], bytes]:
        """Javob sarlavhalarini o'qiydi. Orada kelgan interleaved ($) kadrlar
        tashlanadi — bu faqat PLAY'dan oldin chaqiriladi; PLAY'dan keyin javoblar
        `read_interleaved` ichida ushlanadi."""
        status = 0
        headers: dict[str, str] = {}
        while True:
            line = await self.r.readline()
            if not line:
                raise RtspError("RTSP ulanish uzildi")
            if line[:1] == b"$":
                # interleaved kadr — uzunlikni o'qib tashlaymiz
                rest = line[1:4]
                while len(rest) < 3:
                    rest += await self.r.readexactly(3 - len(rest))
                ln = int.from_bytes(rest[1:3], "big")
                await self.r.readexactly(max(0, ln - (len(line) - 4)))
                continue
            s = line.decode("latin-1").rstrip("\r\n")
            if s.startswith("RTSP/"):
                parts = s.split(" ", 2)
                status = int(parts[1]) if len(parts) > 1 else 0
                break
        while True:
            line = (await self.r.readline()).decode("latin-1").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        body = b""
        n = int(headers.get("content-length", "0") or 0)
        if n:
            body = await self.r.readexactly(n)
        return status, headers, body


def _base_of(url: str) -> str:
    u = urlparse(url)
    return f"rtsp://{u.hostname}:{u.port or 554}{u.path}"


def split_userinfo(url: str) -> tuple[str, str | None]:
    """`rtsp://user:pass@host:port/p` -> (`rtsp://host:port/p`, "Basic …").

    Login/parol so'rov qatoriga (va MediaMTX logiga) tushmasin — sarlavhaga o'tadi.
    MediaMTX ichki foydalanuvchisi (audit 2026-11-03) Basic auth bilan tekshiriladi."""
    from urllib.parse import unquote

    u = urlparse(url)
    if not u.username:
        return url, None
    netloc = u.hostname or ""
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if u.port:
        netloc += f":{u.port}"
    clean = u._replace(netloc=netloc).geturl()
    cred = f"{unquote(u.username)}:{unquote(u.password or '')}".encode()
    return clean, "Basic " + base64.b64encode(cred).decode()


async def read_stream(
    url: str,
    on_track: Callable[[Track], Awaitable[None]],
    on_au: Callable[[AccessUnit], Awaitable[None]],
    *,
    stop: asyncio.Event,
) -> None:
    """URL'ga ulanadi, video track'ni PLAY qiladi, kadrlarni `on_au` ga uzatadi.

    `stop` o'rnatilguncha yoki oqim uzilguncha ishlaydi; uzilish RtspError/
    OSError bilan tugaydi (qayta ulanish — chaqiruvchining ishi)."""
    url, auth = split_userinfo(url)
    u = urlparse(url)
    host, port = u.hostname, u.port or 554
    t0 = time.monotonic()
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), _CONNECT_TIMEOUT)
    rtsp = _Rtsp(reader, writer)
    rtsp.auth = auth
    try:
        # DESCRIBE — on-demand manba bo'lsa MediaMTX kamera ochilguncha ushlab turadi
        status, hdrs, body = await asyncio.wait_for(
            rtsp.request("DESCRIBE", url, {"Accept": "application/sdp"}), _CONNECT_TIMEOUT)
        if status != 200:
            raise RtspError(f"DESCRIBE {status}")
        base = hdrs.get("content-base") or hdrs.get("content-location") or _base_of(url)
        track = parse_sdp(body.decode("utf-8", "replace"), base)

        status, hdrs, _ = await asyncio.wait_for(
            rtsp.request("SETUP", track.control,
                         {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}), 10.0)
        if status != 200:
            raise RtspError(f"SETUP {status}")
        rtsp.session = (hdrs.get("session") or "").split(";", 1)[0].strip() or None
        if not rtsp.session:
            raise RtspError("SETUP javobida Session yo'q")

        status, _, _ = await asyncio.wait_for(
            rtsp.request("PLAY", base, {"Range": "npt=0.000-"}), 10.0)
        if status != 200:
            raise RtspError(f"PLAY {status}")
        log.info("[vaqt] RTSP PLAY %.2fs (%s, %s)", time.monotonic() - t0, url, track.codec)
        await on_track(track)

        depack = Depacketizer(track)
        last_keepalive = time.monotonic()
        while not stop.is_set():
            # Keepalive: MediaMTX TCP o'quvchini ~10s da RTSP so'rov/RTCP bo'lmasa uzadi.
            if time.monotonic() - last_keepalive > _KEEPALIVE_SECONDS:
                rtsp.cseq += 1
                writer.write((f"OPTIONS {base} RTSP/1.0\r\nCSeq: {rtsp.cseq}\r\n"
                              f"Session: {rtsp.session}\r\n{rtsp.extra_lines()}\r\n").encode())
                await writer.drain()
                last_keepalive = time.monotonic()
            try:
                first = await asyncio.wait_for(reader.readexactly(1), _READ_TIMEOUT)
            except asyncio.TimeoutError:
                raise RtspError("oqimdan ma'lumot kelmadi (timeout)")
            if first == b"$":
                hdr = await reader.readexactly(3)
                ch, ln = hdr[0], int.from_bytes(hdr[1:3], "big")
                data = await reader.readexactly(ln)
                if ch != 0:
                    continue  # RTCP — kerak emas
                pkt = parse_rtp(data)
                if pkt is None:
                    continue
                for au in depack.feed(pkt):
                    await on_au(au)
            else:
                # RTSP javobi (OPTIONS keepalive) yoki server so'rovi — sarlavhalarni yutamiz
                await _skip_rtsp_message(reader, first)
    finally:
        try:
            if rtsp.session:
                rtsp.cseq += 1
                writer.write((f"TEARDOWN {url} RTSP/1.0\r\nCSeq: {rtsp.cseq}\r\n"
                              f"Session: {rtsp.session}\r\n{rtsp.extra_lines()}\r\n").encode())
                await asyncio.wait_for(writer.drain(), 1.0)
        except Exception:  # noqa: BLE001
            pass
        writer.close()


async def _skip_rtsp_message(reader: asyncio.StreamReader, first: bytes) -> None:
    line = first + await reader.readline()
    content_length = 0
    while True:
        line = await reader.readline()
        if not line:
            raise RtspError("RTSP ulanish uzildi")
        s = line.decode("latin-1").strip()
        if not s:
            break
        if s.lower().startswith("content-length:"):
            try:
                content_length = int(s.split(":", 1)[1])
            except ValueError:
                content_length = 0
    if content_length:
        await reader.readexactly(content_length)
