"""WebSocket "xom oqim" hub'i — bitta kamera, bitta RTSP o'quvchi, ko'p tomoshabin.

Nega: HLS'da pleyer segment(lar)ni KUTISHI shart, WebRTC UDP port talab qiladi.
Bu yo'l esa oddiy WebSocket (nginx/Cloudflare o'tkazadi) orqali KODLANGAN kadrlarni
uzatadi, brauzer WebCodecs bilan dekodlaydi:
  * transkod yo'q, segment yo'q — birinchi kadr kelishi bilan chiziladi;
  * yangi tomoshabin OXIRGI GOP'ni (so'nggi kalit kadrdan buyon) bir zumda oladi —
    keyingi kalit kadrni kutmaydi ("ochilish" = tarmoq RTT + dekod);
  * MediaMTX'da bitta reader (biz), N brauzer — kamera/bulutga yuk ko'paymaydi.

Kadr xabari (binary): 12 bayt sarlavha + Annex-B:
  [0]      bayroqlar: bit0 = kalit kadr
  [1..3]   zaxira (0)
  [4..11]  timestamp, mikrosekund, uint64 big-endian
Boshida bitta JSON: {"type":"init","codec":"h264|h265","codecString":"avc1.../hev1..."}.
Oqim tugasa/uzilsa: {"type":"end","reason":...} va ulanish yopiladi.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field

from vizora.core.config import settings
from vizora.media import mediamtx, signals
from vizora.media import rtsp_reader as rr
from vizora.sources.ezviz import manager as ezviz_manager

log = logging.getLogger("vizora.wsstream")

_HDR = struct.Struct(">B3xQ")   # bayroq, 3 bayt zaxira, uint64 timestamp
_QUEUE_MAX = 400                # obunachi navbati (kadr); to'lsa keyframe'gacha tashlanadi
_QUEUE_MAX_BYTES = 12 * 1024 * 1024   # obunachi navbati (bayt) — o'qimaydigan ulanish xotirani yemasin
MAX_SUBS_PER_HUB = 64           # bitta kameraga bir vaqtda ulangan WS tomoshabinlar
_GOP_MAX_FRAMES = 300           # GOP keshi (juda uzun GOP'li kameralarda xotira himoyasi)
_GOP_MAX_BYTES = 16 * 1024 * 1024
_RECONNECT_TRIES = 3


def pack_frame(key: bool, ts_us: int, data: bytes) -> bytes:
    return _HDR.pack(1 if key else 0, max(0, ts_us)) + data


@dataclass(eq=False)   # eq=False: identity bo'yicha hash — set'da saqlanadi
class Subscriber:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_MAX))
    need_key: bool = True     # birinchi kadr KALIT bo'lishi shart (dekoder shunday boshlanadi)
    dropped: int = 0
    # Navbatdagi baytlar. Kadr soni (400) yetarli emas: 1800p HEVC kalit kadrlari
    # o'nlab MB — o'qimaydigan yuzlab ulanish serverni xotirasiz qoldirardi.
    qbytes: int = 0

    async def get(self):
        msg = await self.queue.get()
        if isinstance(msg, bytes):
            self.qbytes = max(0, self.qbytes - len(msg))
        return msg

    def push(self, msg: bytes | dict, *, key: bool = False) -> None:
        if isinstance(msg, bytes):
            if self.need_key and not key:
                return
            if self.queue.full() or self.qbytes + len(msg) > _QUEUE_MAX_BYTES:
                # Orqada qoldi (sekin tarmoq/dekoder) — navbatni bo'shatib, keyingi
                # kalit kadrdan "jonli"ga sakraymiz. Yarim GOP yuborish — buzuq kadr.
                self._drain()
                self.need_key = True
                self.dropped += 1
                if not key:
                    return
            self.need_key = False
        try:
            self.queue.put_nowait(msg)
            if isinstance(msg, bytes):
                self.qbytes += len(msg)
        except asyncio.QueueFull:
            pass

    def _drain(self) -> None:
        self.qbytes = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break


class Hub:
    def __init__(self, stream_path: str):
        self.stream_path = stream_path
        # Publisher ASOSIY path bo'yicha ro'yxatdan o'tgan (`cam/<uuid>`), biz esa
        # `/raw` yoki `/sub` dan o'qiyotgan bo'lishimiz mumkin — idle soatini
        # yangilashda asosiy nomni ishlatamiz.
        self.base_path = "/".join(stream_path.split("/")[:2])
        self._touched = 0.0
        self._key_signalled = 0.0
        self.url = mediamtx.rtsp_url(stream_path)
        self.subs: set[Subscriber] = set()
        self.track: rr.Track | None = None
        self.init_msg: dict | None = None
        self.gop: list[bytes] = []     # pack_frame() qilingan kadrlar, [0] — kalit
        self.gop_bytes = 0
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.started = time.monotonic()
        self.frames = 0
        self.last_frame = 0.0
        self.idle_since: float | None = None

    # ---- obuna
    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self.subs.add(sub)
        self.idle_since = None
        if self.init_msg:
            sub.push(self.init_msg)
            # Oxirgi GOP — tomoshabin darhol kalit kadr + ungacha yig'ilgan
            # kadrlarni oladi va JONLI chetga yetadi.
            for i, fr in enumerate(self.gop):
                sub.push(fr, key=(i == 0))
        if self.task is None or self.task.done():
            self.stop.clear()
            self.task = asyncio.create_task(self._run(), name=f"wsstream:{self.stream_path}")
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subs.discard(sub)
        if not self.subs:
            self.idle_since = time.monotonic()

    # ---- RTSP o'quvchi
    async def _run(self) -> None:
        tries = 0
        waiting_since: float | None = None
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                await rr.read_stream(self.url, self._on_track, self._on_au, stop=self.stop)
                reason = "oqim tugadi"
            except (rr.RtspError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                reason = str(exc) or exc.__class__.__name__
            if self.stop.is_set():
                break
            if not self.subs:
                break
            # DESCRIBE 404/400 = path bor, lekin manba HALI YO'Q: EZVIZ publisher bulutga
            # ulanmoqda (~6-20s) yoki on-demand RTSP kamera endi tortilmoqda. Bu xato
            # emas — sabr bilan qayta uramiz (`ws_stream_ready_timeout` gacha).
            if reason.startswith("DESCRIBE 404") or reason.startswith("DESCRIBE 400"):
                if waiting_since is None:
                    waiting_since = time.monotonic()
                    log.info("WS oqim: manba hali yo'q (%s) — kutilmoqda (%s)", reason, self.stream_path)
                waited = time.monotonic() - waiting_since
                # Publisher O'LGAN bo'lsa kutish BEKOR: oflayn EZVIZ kamerada
                # publish.py qurilmani bulut ro'yxatida topmay ~1s da chiqadi, biz
                # esa to'liq timeout kutib foydalanuvchini aldab turardik.
                # Publisher ASOSIY path bo'yicha ro'yxatda (`cam/<id>`), hub esa `/raw`
                # ni o'qishi mumkin — shuning uchun base_path. Media alohida jarayonda
                # bo'lsa holat Redis'dan keladi ([[signals]]).
                if (ezviz_manager.publisher_dead(self.base_path)
                        or await signals.publisher_dead(self.base_path)):
                    log.info("WS oqim: publisher o'lgan (kamera oflayn?) — kutish to'xtatildi (%s)",
                             self.stream_path)
                    self._broadcast_end("kamera oqim bermadi (oflayn?)", source_failed=True)
                    break
                # Idle-reaper publisher'ni "reader yo'q" deb o'ldirmasin: biz path
                # tayyor bo'lmaguncha MediaMTX reader'i bo'la olmaymiz, lekin tomoshabin BOR.
                await self._touch()
                if int(waited) % 10 == 0 and waited - int(waited) < 0.12:
                    log.info("WS oqim: manba kutilmoqda %.0fs (%s)", waited, self.stream_path)
                # XOM (`/raw`) path so'ralgan, lekin ASOSIY path allaqachon tayyor
                # bo'lsa — bu publisher xom chiqishsiz ishlayapti (deploydan oldin
                # boshlangan yoki `ezviz_raw_path` o'chirilgan). 45s kutish o'rniga
                # darhol aytamiz; pleyer H.264 `ws_url` ga o'tadi. source_failed
                # EMAS — manba sog'lom, faqat bu variant yo'q.
                if self.stream_path != self.base_path and waited > 2.0:
                    main = await mediamtx.get_path(self.base_path)
                    if main and main.get("ready"):
                        log.info("WS oqim: xom path yo'q, asosiysi tayyor — pleyer "
                                 "H.264 ga o'tsin (%s)", self.stream_path)
                        self._broadcast_end("xom oqim yo'q", source_failed=False)
                        break
                # Brauzerga "tirikman, manbani kutyapman" — pleyer o'z taymerini
                # qayta boshlaydi va HLS'ga (u ham shu manbani kutadi) tushib ketmaydi.
                if int(waited * 2) % 4 == 0:   # ~har 2s
                    for s in list(self.subs):
                        s.push({"type": "wait", "waited": round(waited, 1)})
                if waited >= settings.ws_stream_ready_timeout:
                    log.warning("WS oqim: manba %.0fs da tayyor bo'lmadi (%s)", waited, self.stream_path)
                    self._broadcast_end("manba tayyor bo'lmadi", source_failed=True)
                    break
                # Qadam KICHIK: bu sof kutish kechikishi — publisher path'ni ochgach
                # biz o'rtacha yarim qadam bekorga kutamiz, va o'sha paytda oqimning
                # birinchi kalit kadri o'tib ketishi mumkin. DESCRIBE lokal va arzon.
                await asyncio.sleep(0.12)
                continue
            waiting_since = None
            # Uzoq (>10s) sog'lom ishlagan bo'lsa — hisoblagichni nollaymiz (blip)
            if time.monotonic() - t0 > 10:
                tries = 0
            tries += 1
            log.warning("WS oqim uzildi (%s): %s — urinish %d/%d", self.stream_path, reason, tries, _RECONNECT_TRIES)
            if tries >= _RECONNECT_TRIES:
                self._broadcast_end(reason)
                break
            await asyncio.sleep(1.0)
        self.task = None

    async def _on_track(self, track: rr.Track) -> None:
        self.track = track
        sps = track.sps or b""
        self.init_msg = {
            "type": "init",
            "codec": track.codec,
            "codecString": rr.codec_string(track.codec, sps) if sps else
            ("avc1.42E01E" if track.codec == rr.H264 else "hev1.1.6.L93.B0"),
            "path": self.stream_path,
        }
        # Qayta ulanishda GOP eskirgan — tashlaymiz; obunachilar yangi init oladi
        self.gop, self.gop_bytes = [], 0
        for s in self.subs:
            s.need_key = True
            s.push(self.init_msg)

    async def _on_au(self, au: rr.AccessUnit) -> None:
        tr = self.track
        if tr is None:
            return
        # SPS SDP'da bo'lmasa — in-band kelgan zahoti codecString'ni yangilaymiz
        if au.key and tr.sps and self.init_msg and self.init_msg["codecString"] in ("avc1.42E01E", "hev1.1.6.L93.B0"):
            self.init_msg["codecString"] = rr.codec_string(tr.codec, tr.sps)
        params: tuple[bytes, ...] = ()
        if au.key:
            params = tuple(p for p in ((tr.vps, tr.sps, tr.pps) if tr.codec == rr.H265 else (tr.sps, tr.pps)) if p)
        data = au.annexb(params)
        if not au.key and not self.gop:
            return  # birinchi kalit kadrgacha hech kim dekodlay olmaydi
        frame = pack_frame(au.key, au.ts_us, data)
        self.frames += 1
        self.last_frame = time.monotonic()
        # Tomoshabin BOR ekanini publisher'ga bildirib turamiz. MediaMTX reader
        # hisobi endi yetarli emas edi: biz `/raw` dan o'qiymiz, idle-reaper esa
        # asosiy path'ni qaraydi. `_readers_for` buni tuzatdi, bu esa ikkinchi
        # himoya (path sxemasi kelajakda o'zgarsa ham ishlaydi).
        if self.subs:
            await self._touch()
        if au.key:
            self.gop, self.gop_bytes = [frame], len(frame)
            if self.last_frame - self._key_signalled > 10.0:
                # API jarayoni `/raw` ni berish qarorini shunga qarab qiladi
                self._key_signalled = self.last_frame
                await signals.hub_keyframe(self.stream_path, True)
        else:
            self.gop.append(frame)
            self.gop_bytes += len(frame)
            if len(self.gop) > _GOP_MAX_FRAMES or self.gop_bytes > _GOP_MAX_BYTES:
                # GOP o'ta uzun — keshni tashlaymiz, yangi tomoshabin keyingi kalitni kutadi
                self.gop, self.gop_bytes = [], 0
        for s in self.subs:
            s.push(frame, key=au.key)

    async def _touch(self) -> None:
        """Tomoshabin borligini publisher'ga bildiradi (ko'pi bilan 5 s da bir).

        Bir jarayonda — to'g'ridan; media alohida bo'lsa — Redis orqali API'ning
        idle-reaper'iga ([[signals.touch_viewer]])."""
        now = time.monotonic()
        if now - self._touched < 5.0:
            return
        self._touched = now
        ezviz_manager.touch_viewer(self.base_path)
        await signals.touch_viewer(self.base_path)

    def _broadcast_end(self, reason: str, *, source_failed: bool = False) -> None:
        # source_failed=True — MANBA (kamera/publisher) oqim bermadi. Bunda pleyer
        # WebRTC/HLS'ga tushishi BEHUDA (ular ham shu manbani o'qiydi) — aniq xabar
        # ko'rsatish to'g'ri. False — transport muammosi: fallback ma'noli.
        msg = {"type": "end", "reason": reason, "source_failed": source_failed}
        for s in list(self.subs):
            s.push(msg)

    async def close(self) -> None:
        self.stop.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.task = None

    def stats(self) -> dict:
        return {
            "path": self.stream_path, "subscribers": len(self.subs), "frames": self.frames,
            "gop_frames": len(self.gop), "codec": self.track.codec if self.track else None,
            "uptime_s": round(time.monotonic() - self.started, 1),
        }


_hubs: dict[str, Hub] = {}
_reaper: asyncio.Task | None = None


def get_hub(stream_path: str) -> Hub:
    hub = _hubs.get(stream_path)
    if hub is None:
        hub = Hub(stream_path)
        _hubs[stream_path] = hub
    global _reaper
    if _reaper is None or _reaper.done():
        _reaper = asyncio.create_task(_reap_loop(), name="wsstream:reaper")
    return hub


def has_keyframe(stream_path: str) -> bool:
    """Shu path'ning hub'i ochiq va keshida kalit kadr bor — yangi tomoshabin
    birinchi kadrni DARHOL oladi (kameraning keyingi I-kadrini kutmaydi)."""
    hub = _hubs.get(stream_path)
    return bool(hub and hub.gop)


async def _reap_loop() -> None:
    """Obunachisiz qolgan hub'larni `ws_stream_idle_seconds` dan keyin yopadi —
    RTSP o'quvchi uziladi, MediaMTX reader hisobi kamayadi (EZVIZ idle-reaper va
    on-demand manba avvalgidek ishlaydi)."""
    while True:
        await asyncio.sleep(1.0)
        now = time.monotonic()
        for path, hub in list(_hubs.items()):
            if hub.subs:
                continue
            if hub.idle_since is not None and now - hub.idle_since >= settings.ws_stream_idle_seconds:
                await hub.close()
                _hubs.pop(path, None)
                await signals.hub_keyframe(path, False)
                log.info("WS hub yopildi (tomoshabin yo'q): %s", path)
        if not _hubs:
            break


def stats() -> list[dict]:
    return [h.stats() for h in _hubs.values()]


async def close_all() -> None:
    for hub in list(_hubs.values()):
        await hub.close()
        await signals.hub_keyframe(hub.stream_path, False)
    _hubs.clear()
