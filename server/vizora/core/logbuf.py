"""Xotiradagi log ring-buffer — web "Loglar" bo'limi uchun.

Ilova loglarini (probe, ezviz manager, hls, h.k.) xotirada saqlaydi (oxirgi N ta)
— admin web panelidan SSH/htop'siz ko'rish uchun. Root logger'ga handler sifatida
o'rnatiladi. Faqat o'qish (diagnostika); diskka yozmaydi.
"""
from __future__ import annotations

import collections
import logging
import threading

_CAP = 1000
_records: collections.deque = collections.deque(maxlen=_CAP)
_lock = threading.Lock()

_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

# Shovqinli kutubxonalar — ulardan faqat WARNING+ ni olamiz (INFO/DEBUG SQL/HTTP
# spam'i foydali loglarni bosib qo'ymasin). vizora.* hammasi qoladi.
_NOISY = ("sqlalchemy", "httpcore", "httpx", "uvicorn.access", "aiosqlite", "asyncio")


import re  # noqa: E402

# Loglarda sir qolmasin: panelning «Loglar» bo'limi, kamera publisher logi va uvicorn
# access logi (URL'dagi ?api_key= / ?token=) ko'riladigan joylar.
# Kalit nomlari: query (`k=v`) va JSON (`"k": "v"`) ikkalasida ham.
_SECRET_KEYS = (r"api_key|token|access_token|refresh_token|password|passwd|verify_code|verifycode"
                r"|key|sessionId|session_id|refreshSessionId|rf_session_id|rfSessionId")
_SECRET_PATTERNS = (
    # Parolda kodlanmagan `/` bo'lsa ham (audit 2026-11-03): foydalanuvchi nomida `/`
    # bo'lmaydi, parol esa `@` gacha istalgan belgi.
    (re.compile(r"(rtsps?://)[^:@/\s]+:[^@\s]+@", re.I), r"\1***:***@"),
    (re.compile(rf"\b({_SECRET_KEYS})=([^&\s\"']+)", re.I), r"\1=***"),
    # JSON javoblar (bulut login/sessiya) log'ga tushsa: "sessionId": "..."
    (re.compile(rf"(\"(?:{_SECRET_KEYS})\"\s*:\s*\")[^\"]*(\")", re.I), r"\1***\2"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
    (re.compile(r"(Authorization:\s*Basic\s+)[A-Za-z0-9+/=]+", re.I), r"\1***"),
)


def redact(text: str) -> str:
    for rx, rep in _SECRET_PATTERNS:
        text = rx.sub(rep, text)
    return text


class RingBufferHandler(logging.Handler):
    """Har log yozuvini xotira buferiga qo'shadi (sodda dict)."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING and record.name.startswith(_NOISY):
            return  # shovqinli kutubxonalarning INFO/DEBUG'ini tashlaymiz
        try:
            msg = redact(record.getMessage())
        except Exception:  # noqa: BLE001 — format xatosi logni buzmasin
            msg = redact(str(getattr(record, "msg", "")))
        item = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": msg[:2000],  # juda uzun xabarlarni cheklaymiz
        }
        with _lock:
            _records.append(item)


class _AccessRedact(logging.Filter):
    """uvicorn access logidagi URL'dan `?api_key=` / `?token=` qiymatlarini yashiradi
    (tashqi tizimlar WS'ga kalitni query'da yuboradi — u docker logida qolib ketardi)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            args[2] = redact(str(args[2]))
            record.args = tuple(args)
        return True


def install(level: int = logging.INFO) -> None:
    """Root logger'ga buffer handler'ni bir marta o'rnatadi."""
    acc = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _AccessRedact) for f in acc.filters):
        acc.addFilter(_AccessRedact())
    root = logging.getLogger()
    if any(isinstance(h, RingBufferHandler) for h in root.handlers):
        return
    h = RingBufferHandler()
    h.setLevel(level)
    root.addHandler(h)


def get_records(level: str | None = None, q: str | None = None, limit: int = 300) -> list[dict]:
    """Buferdagi yozuvlarni filtrlab qaytaradi (eng yangisi oxirida).

    level — shu daraja VA undan yuqorisi (masalan WARNING -> WARNING+ERROR).
    q — xabar yoki logger nomida qism-satr (katta-kichik harfsiz).
    """
    with _lock:
        items = list(_records)
    if level and level != "ALL":
        minv = _ORDER.get(level, 0)
        items = [r for r in items if _ORDER.get(r["level"], 0) >= minv]
    if q:
        ql = q.lower()
        items = [r for r in items if ql in r["msg"].lower() or ql in r["logger"].lower()]
    return items[-limit:]
