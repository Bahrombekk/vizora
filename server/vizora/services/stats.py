"""Boshqaruv paneli uchun yengil tarix: onlayn kameralar soni va holat hodisalari.

Avval Dashboard grafigi qo'lda chizilgan shakl edi, "So'nggi hodisalar" esa
8 ta kameraning `last_seen_at` idan yasalardi (oflayn kamera ham "onlayn"
deb chiqardi). Endi ikkalasi Redis'da saqlanadi:
  * `stats:online` (ZSET) — har `SAMPLE_SECONDS` da bitta nuqta
    `"<ts>:<online>:<total>"`, `KEEP_SECONDS` dan eskisi kesiladi
    (1 daqiqada 1 nuqta × 31 kun ≈ 45 000 element, ~1.5 MB).
  * `camera:events` (LIST) — probe aniqlagan holat o'zgarishlari, oxirgi 200 ta.
  * `stats:probe_at` — probe siklining oxirgi ishlagan vaqti (xizmat holati uchun).
Redis yo'q yoki xato bo'lsa — jimgina o'tkazib yuboriladi, probe yiqilmaydi.
"""
import json
import logging
import time

import redis.asyncio as aioredis
from sqlalchemy import func, select

from vizora.core.db import SessionLocal
from vizora.models.camera import Camera

log = logging.getLogger("vizora.stats")

SERIES_KEY = "stats:online"
EVENTS_KEY = "camera:events"
PROBE_KEY = "stats:probe_at"
SYS_KEY = "stats:sys"     # server resurslari (CPU/RAM), har probe siklida, oxirgi ~1 soat
SYS_MAX = 240
SAMPLE_SECONDS = 60   # 1 daq × 31 kun ≈ 45 000 element (~1.5 MB) — Redis uchun arzimas
KEEP_SECONDS = 31 * 86400
EVENTS_MAX = 200

# Panel diapazonlari: (davomiylik, bo'laklar soni)
RANGES = {"24h": (86400, 48), "7d": (7 * 86400, 7), "30d": (30 * 86400, 30)}  # 30 daq / kunlik / kunlik

_last_sample = 0.0
_cpu_prev: tuple[int, int] | None = None


async def record_events(redis: aioredis.Redis | None, changes: list[tuple[str, str]]) -> None:
    """Probe aniqlagan holat o'zgarishlarini jurnalga yozadi."""
    if not redis or not changes:
        return
    now = time.time()
    try:
        items = [json.dumps({"camera_id": cid, "status": st, "ts": now}) for cid, st in changes]
        await redis.lpush(EVENTS_KEY, *items)
        await redis.ltrim(EVENTS_KEY, 0, EVENTS_MAX - 1)
    except Exception as exc:  # noqa: BLE001
        log.warning("Hodisa jurnaliga yozib bo'lmadi: %s", exc)


def _read_cpu() -> tuple[int, int]:
    """/proc/stat birinchi qatori: (jami, bo'sh) jiffies — butun server (host) CPU."""
    with open("/proc/stat", encoding="ascii") as f:
        vals = [int(x) for x in f.readline().split()[1:]]
    return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait


def sys_sample() -> dict:
    """Server CPU (oldingi chaqiruvdan beri o'rtacha, %) va xotira. Bloklamaydi.

    Avval Monitoring sahifasi CPU'ni warm-keeper'dan olardi — u o'chiq bo'lgani uchun
    doim 0% ko'rsatardi, kichik grafiklar esa sinusdan yasalgan edi."""
    global _cpu_prev
    out: dict = {"ts": time.time()}
    try:
        cur = _read_cpu()
        if _cpu_prev and cur[0] > _cpu_prev[0]:
            dt = cur[0] - _cpu_prev[0]
            out["cpu"] = round(max(0.0, min(100.0, (1 - (cur[1] - _cpu_prev[1]) / dt) * 100)), 1)
        _cpu_prev = cur
    except OSError:
        pass
    try:
        mem = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                k, v = line.split(":", 1)
                if k in ("MemTotal", "MemAvailable"):
                    mem[k] = int(v.split()[0])
        tot, avail = mem.get("MemTotal"), mem.get("MemAvailable")
        if tot:
            out["mem_total_gb"] = round(tot / 1048576, 1)
            out["mem_used_gb"] = round((tot - (avail or 0)) / 1048576, 1)
            out["mem"] = round((1 - (avail or 0) / tot) * 100, 1)
    except OSError:
        pass
    try:
        with open("/proc/loadavg", encoding="ascii") as f:
            out["load1"] = float(f.read().split()[0])
    except OSError:
        pass
    return out


async def sys_history(redis: aioredis.Redis) -> list[dict]:
    rows = await redis.lrange(SYS_KEY, 0, SYS_MAX - 1)
    out = []
    for raw in reversed(rows):              # eskisidan yangisiga
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


async def tick(redis: aioredis.Redis | None) -> None:
    """Har probe siklidan keyin chaqiriladi: yurak urishi + kerak bo'lsa namuna."""
    global _last_sample
    if not redis:
        return
    now = time.time()
    try:
        await redis.set(PROBE_KEY, str(now))
        smp = sys_sample()
        if "cpu" in smp:
            await redis.lpush(SYS_KEY, json.dumps(smp))
            await redis.ltrim(SYS_KEY, 0, SYS_MAX - 1)
        if now - _last_sample < SAMPLE_SECONDS:
            return
        async with SessionLocal() as db:
            rows = (await db.execute(select(Camera.status, func.count()).group_by(Camera.status))).all()
        by = dict(rows)
        total = sum(by.values())
        await redis.zadd(SERIES_KEY, {f"{int(now)}:{by.get('online', 0)}:{total}": now})
        await redis.zremrangebyscore(SERIES_KEY, 0, now - KEEP_SECONDS)
        _last_sample = now
    except Exception as exc:  # noqa: BLE001
        log.warning("Statistika namunasi olinmadi: %s", exc)


def bucketize(points: list[tuple[float, int, int]], start: float, span: float, n: int) -> list[dict]:
    """Nuqtalarni `n` ta teng bo'lakka bo'lib, har birida o'rtacha onlaynni beradi.

    Ma'lumot yo'q bo'lak `online=None` — grafik uni chizmaydi (nolga tushirmaydi,
    aks holda yangi o'rnatilgan tizim "hamma kamera o'chgan" bo'lib ko'rinardi).
    """
    step = span / n
    acc = [[0, 0, 0] for _ in range(n)]  # online yig'indisi, total yig'indisi, soni
    for ts, on, tot in points:
        i = int((ts - start) // step)
        if 0 <= i < n:
            acc[i][0] += on
            acc[i][1] += tot
            acc[i][2] += 1
    out = []
    for i, (on, tot, cnt) in enumerate(acc):
        out.append({
            "t": int(start + i * step),
            "online": round(on / cnt) if cnt else None,
            "total": round(tot / cnt) if cnt else None,
        })
    return out


def day_window(now: float, days: int, tz_min: int) -> tuple[float, float]:
    """Kunlik bo'laklar uchun oyna: foydalanuvchining MAHALLIY yarim tunidan boshlanadi
    (server UTC'da, panel Toshkentda — aks holda "kun" soat 05:00 da almashardi)."""
    off = tz_min * 60
    today = ((now + off) // 86400) * 86400 - off
    return today - (days - 1) * 86400, days * 86400


async def history(redis: aioredis.Redis, rng: str, tz_min: int = 0) -> dict:
    """'24h' — 48 ta 30 daqiqalik bo'lak; tarix qisqa bo'lsa (yangi tizim) bo'laklar
    MAVJUD davrga moslashadi (aks holda bir necha daqiqalik ma'lumot bitta nuqta bo'lardi).
    '7d'/'30d' — har doim KUNLIK ustunlar (7 / 30 ta); ma'lumoti yo'q kun bo'sh qoladi."""
    rng = rng if rng in RANGES else "24h"
    now = time.time()
    if rng == "24h":
        span, n = RANGES["24h"]
        start = now - span
    else:
        n = RANGES[rng][1]
        start, span = day_window(now, n, tz_min)
    raw = await redis.zrangebyscore(SERIES_KEY, start, now)
    points = []
    for m in raw:
        try:
            ts, on, tot = m.split(":")
            points.append((float(ts), int(on), int(tot)))
        except ValueError:
            continue
    since = points[0][0] if points else None
    partial = since is not None and since > start
    if partial and rng == "24h":
        covered = max(now - since, SAMPLE_SECONDS)
        n = max(1, min(n, int(covered // SAMPLE_SECONDS)))
        start, span = since, covered + 1   # +1: oxirgi namuna ham ichkariga tushsin
    return {"range": rng, "step": int(span / n), "since": since, "partial": partial,
            "points": bucketize(points, start, span, n)}


async def recent_events(redis: aioredis.Redis, limit: int) -> list[dict]:
    out = []
    for raw in await redis.lrange(EVENTS_KEY, 0, max(0, limit - 1)):
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out
