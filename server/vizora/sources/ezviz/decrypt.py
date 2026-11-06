"""EZVIZ shifrli HEVC oqimini ochuvchi OQIMLI dekoderlar (RTP + MPEG-PS).

Ishlab turgan mustaqil "Ezviz/decrypt_proxy.py" mantig'i bilan bir xil. Har NAL
alohida, kechikishsiz ochiladi (vaqt bo'yicha buferlash YO'Q) — shu bilan ffmpeg
uzluksiz, toza Annex-B oqimini oladi va dekodlay oladi. pyezvizapi'ning
decrypt_hikvision_ps_video'si jonli (live) bo'laklarda ishonchsiz: kadr
o'rtasidan kesib, "Could not find ref with POC" beradi -> ffmpeg chiqish bermaydi
-> MediaMTX'ga publish bo'lmaydi -> HLS 404. Shuning uchun ishlatilmaydi.

Ikki dekoder (har ikkalasi toza Annex-B HEVC chiqaradi):
  * _HevcRtpDecryptor — RTP/HEVC (batareyali kamera/IPC).
  * _PsStreamDecryptor — MPEG-PS (Hik-Connect NVR'lari).
publish.py oqim formatini avtomatik aniqlab, mosini tanlaydi.
"""
from __future__ import annotations

import logging

log = logging.getLogger("vizora.ezviz.decrypt")

_HEVC_VIDEO_PT = 96            # RTP payload type — video
_NAL_ENCRYPTED_PREFIX = 4096   # har NAL'ning birinchi shu qadar bayti shifrlangan
# Inter-slice shifrini aniqlash uchun namuna soni va qaror chegarasi. 8 namuna
# ~0.3-0.5s P-freymga to'g'ri keladi (buferlash kechikishi shuncha). Ajralish
# baribir keskin: o'lchovda toza=1.00 / deshifr=0.12 — 8 ta yetarli.
_INTER_SAMPLES = 8
_INTER_MARGIN = 0.25           # "deshifr" shu qadar tuzilishliroq bo'lsagina tanlanadi
# Qaror qabul qilinguncha NAL'lar BUFERLANADI (chiqarilmaydi): aks holda birinchi
# ~16 P-freym noto'g'ri ishlov bilan ffmpeg'ga borib buzilish beradi (o'lchovda
# 160 xato satri). Bufer chegarasi — inter NAL umuman kelmasa qotib qolmaslik uchun.
_INTER_MAX_PENDING = 6 * 1024 * 1024
# IRAP darvozasi shu qadar slice'dan keyin majburan ochiladi (~10-15s oqim).
# Aks holda IRAP belgilanmagan kamerada tasvir umuman kelmasligi mumkin edi.
_IRAP_MAX_WAIT = 400
_ANNEXB_START = b"\x00\x00\x00\x01"
# H.264 SPS body[0] = profile_idc — toza (shifrlanmagan) oqimda shu qiymatlardan
# biri bo'ladi; shifrli oqimda tasodifiy. HEVC'da VPS body 0x0c bilan boshlanadi.
_H264_PROFILES = frozenset({66, 77, 88, 100, 110, 122, 244, 44, 83, 86, 118, 128})
# MPEG-PS demux buferlari uchun yuqori chegara — start-code'siz/buzuq kirishda
# cheksiz o'smasin. Normal ishda buferlar har feed'da bo'shaydi; bu faqat
# patologik holatda resync uchun.
_MAX_PS_BUF = 4 * 1024 * 1024  # 4 MB


def _enc_cache_path(key: str) -> str:
    """Inter-shifr qarorining kesh fayli (kamera bo'yicha)."""
    import os

    from vizora.core.config import settings
    safe = "".join(c for c in key if c.isalnum() or c in "-_")
    return os.path.join(settings.ezviz_token_dir, f"enc-{safe}.json")


def _enc_cache_load(key: str) -> bool | None:
    """Oldingi ochilishda aniqlangan qaror (yo'q bo'lsa None).

    Nega kerak: inter (P/B) slice shifrlanganmi — bu KAMERANING O'ZGARMAS
    xususiyati, lekin har ochilishda qaytadan aniqlanardi va qaror kelguncha
    NAL'lar BUFERDA ushlanardi. O'lchandi: "birinchi paket" dan "ffmpeg'ga
    birinchi kadr" gacha 0.65-0.85s aynan shu kutish edi. Kesh bilan birinchi
    kadr darhol ketadi.
    """
    import json
    try:
        with open(_enc_cache_path(key), encoding="utf-8") as f:
            return bool(json.load(f)["inter_enc"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _enc_cache_store(key: str, value: bool) -> None:
    import json
    import os
    import tempfile

    from vizora.core.config import settings
    try:
        os.makedirs(settings.ezviz_token_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=settings.ezviz_token_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"inter_enc": bool(value)}, f)
        os.replace(tmp, _enc_cache_path(key))   # atomar
    except OSError as exc:
        log.warning("Inter-shifr qarorini keshlab bo'lmadi (%s): %s", key, exc)


class _NalDecryptBase:
    """NAL body'sini AES-ECB bilan ochish + param-set orqali shifr turini avto-aniqlash.

    Uch shifr varianti (clear/drop offsetlari): A) header toza+body shifr;
    B-hint) toza tur-ko'rsatkich + butun NAL shifr; B-whole) butun NAL shifr.
    SELEKTIV shifr: ko'p kamera FAQAT I-freym/IRAP + param-set NAL'larini shifrlaydi,
    inter (P/B) slice'larni TOZA qoldiradi — shularni deshifrlamaslik kerak (aks holda
    toza P-freym buziladi -> rangli/pushti artefakt)."""

    # SELEKTIV shifrda deshifrlanadigan NAL turlari (qolganlari — inter slice — toza)
    _H264_ENC_TYPES = {5, 7, 8}                              # IDR, SPS, PPS
    _HEVC_ENC_TYPES = {16, 17, 18, 19, 20, 21, 32, 33, 34}   # IRAP slice'lar + VPS/SPS/PPS
    # IRAP = tasodifiy kirish nuqtasi (dekoder shu yerdan TOZA boshlashi mumkin).
    _H264_IRAP = {5}                                         # IDR
    _HEVC_IRAP = {16, 17, 18, 19, 20, 21}                    # BLA/IDR/CRA
    # Param-set NAL'lari — IRAP'dan OLDIN ham chiqariladi (dekoderga SPS/PPS kerak).
    _H264_PARAMSET = {7, 8}
    _HEVC_PARAMSET = {32, 33, 34}

    def __init__(self, key: str, *, cache_key: str | None = None):
        from Crypto.Cipher import AES

        self._AES = AES
        # Inter-shifr qarori KAMERA bo'yicha keshlanadi (o'zgarmas xususiyat).
        # Kesh bo'lsa buferlash umuman qilinmaydi, ammo qaror BARIBIR tekshiriladi
        # ([[_decide_inter]]) — kamera proshivkasi o'zgarsa kesh o'zini tuzatadi.
        self._cache_key = cache_key
        self.key = key.encode().ljust(16, b"\0")[:16]
        self._decrypt = None   # None=noma'lum, True=shifrli, False=toza
        self.key_error = False # param-set keldi-yu na toza na to'g'ri dekod -> kod xato
        self.codec = None      # 'h264' | 'hevc' | None (oqimdan aniqlanadi)
        self._clear = 0        # boshidagi TOZA (dekodlanmaydigan) baytlar
        self._drop = 0         # deshifrdan keyin boshidan TASHLANADIGAN baytlar (ortiqcha header)
        # --- Diagnostika: qaysi shifr VARIANTI tanlandi va NAL turlari taqsimoti.
        # Busiz buzilgan oqimda (ffmpeg "PPS id out of range" toshqini) sabab
        # ko'rinmaydi: variant noto'g'ri tanlanganmi, kalit xatomi, yoki kamera
        # boshqa sxemadan foydalanadimi.
        self.variant = None    # 'A-toza' | 'A-body' | 'B-hint' | 'B-whole'
        # --- SELEKTIV shifr avto-aniqlash (inter/P slice'lar ham shifrlanganmi?) ---
        # Ba'zi kameralar FAQAT IRAP + param-set NAL'larini shifrlaydi (inter toza),
        # boshqalari esa HAMMASINI. Noto'g'ri taxmin qilinsa har P-freym buziladi:
        # o'lchovda 40s oqimda 246 vs 5 HEVC xatosi. Shuning uchun taxmin qilmaymiz —
        # oqimning O'ZIDAN aniqlaymiz ([[_decide_inter]]).
        cached = _enc_cache_load(cache_key) if cache_key else None
        self._inter_enc = cached    # None=noma'lum, True=inter ham shifrli
        self._verifying = cached is not None   # keshdagi qaror tekshirilmoqda
        self._inter_clear = []      # namuna: toza body'ning 1-bayti
        self._inter_dec = []        # namuna: AES-deshifrlangan body'ning 1-bayti
        # Kesh bo'lsa buferlash YO'Q — birinchi kadr darhol ketadi (0.65-0.85s tejam).
        self._pending = None if self._verifying else []
        self._pending_bytes = 0
        # --- IRAP darvozasi ---
        # Bulut oqimi GOP O'RTASIDAN boshlanadi va Hik NVR param-setlarni DAVRIY
        # yuboradi, ya'ni birinchi VPS/SPS/PPS'dan keyin darhol IDR kelmaydi. Agar
        # o'sha joydan uzatib yuborsak dekoder ref kadrlarni topmaydi va tomoshabin
        # boshida KUL RANG/buzilgan tasvir ko'radi (poster'dan jonliga o'tishda
        # "yomon" kadrlar). Shuning uchun birinchi IRAP (IDR/CRA) kelguncha
        # slice'larni CHIQARMAYMIZ — param-setlar esa o'tadi (dekoderga kerak).
        self._seen_irap = False
        self._gated = 0             # tashlangan (IRAP'dan oldingi) slice soni
        # Boshlanish diagnostikasi: ffmpeg'ga BIRINCHI yuborilgan NAL turlari.
        # Boshida buzilgan tasvir bo'lsa sabab shu ketma-ketlikda ko'rinadi
        # (masalan param-setlardan keyin darhol P slice, IDR'siz).
        self._nal_stats = {}   # nal_type -> [deshifrlangan, toza]
        self._stats_logged = False
        self._first_nals = []  # boshlanishdagi NAL turlari (diagnostika)

    def _aes16(self, data) -> bytes:
        return self._AES.new(self.key, self._AES.MODE_ECB).decrypt(bytes(data[:16]))

    def _decrypt_body(self, body) -> bytes:
        if not self._decrypt:  # None yoki False -> dekod qilmaymiz
            return bytes(body)
        n = (min(len(body), _NAL_ENCRYPTED_PREFIX) // 16) * 16
        if n == 0:
            return bytes(body)
        head = self._AES.new(self.key, self._AES.MODE_ECB).decrypt(bytes(body[:n]))
        return head + bytes(body[n:])

    def _classify(self, nal, hdr, is_paramset, body_marker) -> None:
        """Param-set NAL orqali shifr VARIANTINI aniqlaydi (clear/drop/decrypt).
        Uch variant: A) header toza, body shifrli/toza; B-hint) toza tur-ko'rsatkich +
        butun NAL shifrli; B-whole) butun NAL shifrli (ko'rsatkichsiz). Ba'zi kameralar
        NAL headerini ham shifrlaydi -> eski "header doim toza" farazi buzilish berardi."""
        if self._decrypt is not None:
            return
        b0 = nal[0]
        ps = is_paramset(b0)
        # A) header TOZA, body shifrli yoki toza
        if ps and len(nal) > hdr and body_marker(nal[hdr]):
            self._clear, self._drop, self._decrypt = hdr, 0, False  # toza oqim
            self._log_variant("A-toza (shifrsiz)")
            return
        if ps and len(nal) >= hdr + 16 and body_marker(self._aes16(nal[hdr:hdr + 16])[0]):
            self._clear, self._drop, self._decrypt = hdr, 0, True   # header toza, body shifrli
            self._log_variant("A-body (header toza, body shifr)")
            return
        # B-hint) toza tur-ko'rsatkich + shifrlangan to'liq NAL (header ham shifr ichida)
        if len(nal) >= hdr + 16:
            dec = self._aes16(nal[hdr:hdr + 16])
            if is_paramset(dec[0]) and len(dec) > hdr and body_marker(dec[hdr]):
                self._clear, self._drop, self._decrypt = 0, hdr, True
                self._log_variant("B-hint (toza tur-ko'rsatkich + to'liq shifr)")
                return
        # B-whole) butun NAL shifrli, ko'rsatkichsiz
        if len(nal) >= 16:
            dec = self._aes16(nal[:16])
            if is_paramset(dec[0]) and len(dec) > hdr and body_marker(dec[hdr]):
                self._clear, self._drop, self._decrypt = 0, 0, True
                self._log_variant("B-whole (butun NAL shifr)")
                return
        # toza header param-set ko'rinadi-yu hech variant mos kelmasa -> kod xato
        if ps and len(nal) >= hdr + 16:
            self.key_error = True

    def _log_variant(self, name: str) -> None:
        self.variant = name
        log.info("Shifr varianti aniqlandi: %s (clear=%d drop=%d decrypt=%s)",
                 name, self._clear, self._drop, self._decrypt)

    def _irap_gate(self, ntype: int, hdr: int) -> bool:
        """Shu NAL chiqarilsinmi? (birinchi IRAP'gacha slice'lar tashlanadi)

        Param-set NAL'lari doim o'tadi. IRAP kelgach darvoza butunlay ochiladi."""
        if self._seen_irap:
            return True
        irap = self._H264_IRAP if hdr == 1 else self._HEVC_IRAP
        pset = self._H264_PARAMSET if hdr == 1 else self._HEVC_PARAMSET
        if ntype in irap:
            self._seen_irap = True
            if self._gated:
                log.info("Boshlanish tozalandi: IRAP'dan oldingi %d slice tashlandi "
                         "(kul rang/buzilgan kadr bo'lmaydi)", self._gated)
            return True
        if ntype in pset:
            return True            # SPS/PPS/VPS — dekoderga kerak
        self._gated += 1
        if self._gated > _IRAP_MAX_WAIT:
            # Kamera GOP'i juda uzun (yoki IRAP belgilanmagan) — abadiy kutmaymiz.
            self._seen_irap = True
            log.warning("IRAP kelmadi (%d slice) — darvoza majburan ochildi; "
                        "boshida qisqa buzilish bo'lishi mumkin", self._gated)
            return True
        return False

    def _inter_bytes(self, nal, hdr: int) -> bytes:
        """Inter slice'ni qarorga muvofiq Annex-B bo'lakka aylantiradi."""
        if self._inter_enc:
            return _ANNEXB_START + bytes(nal[:hdr]) + self._decrypt_body(nal[hdr:])
        return _ANNEXB_START + bytes(nal)

    def _out(self, out, chunk: bytes) -> None:
        """Chiqarish — qaror kutilayotgan bo'lsa TARTIBNI saqlab buferga."""
        if self._pending is not None:
            self._hold(out, ("raw", chunk), len(chunk))
        else:
            out += chunk

    def _hold(self, out, item, size: int) -> None:
        if self._pending is None:            # qaror bo'lgan — buferlash tugagan
            if item[0] == "raw":
                out += item[1]
            else:
                out += self._inter_bytes(item[1], item[2])
            return
        self._pending.append(item)
        self._pending_bytes += size
        if self._pending_bytes > _INTER_MAX_PENDING:
            # Inter NAL kelmayapti (yoki juda kam) — kutib qotib qolmaymiz.
            log.warning("Inter-shifr qarori uchun namuna yetmadi (%d) — TOZA deb "
                        "qabul qilinadi", len(self._inter_clear))
            self._inter_enc = False
            self._flush(out)

    def _flush(self, out) -> None:
        """Qaror qabul qilindi — buferlangan NAL'larni TARTIB bilan chiqaramiz."""
        pend, self._pending = self._pending, None
        self._pending_bytes = 0
        for kind, *rest in pend or ():
            if kind == "raw":
                out += rest[0]
            else:
                nal, hdr = rest
                out += self._inter_bytes(nal, hdr)
                ntype = (nal[0] & 0x1F) if hdr == 1 else ((nal[0] >> 1) & 0x3F)
                self._count(ntype, bool(self._inter_enc))

    def _sample_inter(self, nal, hdr: int) -> None:
        """Inter slice'lar shifrlanganmi — TUZILISH (structure) testi bilan aniqlaydi.

        Slice header baytlari TAKRORLANUVCHI (bir xil PPS, bir xil slice tuzilishi),
        shifrlangan baytlar esa TASODIFIY. Shuning uchun N ta namunadagi FARQLI
        qiymatlar sonini solishtiramiz: kichigi — "tuzilishli", ya'ni to'g'ri o'qish.
        Bir bayt bo'yicha ehtimollik testi ishonchsiz (tasodifiy bayt ham 87% holatda
        "yaroqli" ue(v) beradi), farqli-qiymat nisbati esa keskin ajratadi."""
        body = nal[hdr:]
        if len(body) < 16:
            return
        self._inter_clear.append(body[0])
        self._inter_dec.append(self._aes16(body)[0])
        if len(self._inter_clear) >= _INTER_SAMPLES:
            self._decide_inter()

    def _decide_inter(self) -> None:
        n = len(self._inter_clear)
        d_clear = len(set(self._inter_clear)) / n
        d_dec = len(set(self._inter_dec)) / n
        # Aniq farq bo'lsa tuzilishlisini tanlaymiz; teng bo'lsa TOZA (eski,
        # xavfsizroq xatti-harakat — toza P-freymni deshifrlash uni buzadi).
        fresh = d_dec + _INTER_MARGIN < d_clear
        if self._verifying:
            # Kesh ishlatildi (buferlash o'tkazib yuborildi). Endi tekshiramiz:
            # qaror to'g'ri bo'lsa jimgina davom etamiz, xato bo'lsa TUZATAMIZ —
            # bu holda boshidagi bir necha P-freym buzuq ketgan bo'ladi (~0.5s),
            # keyin toza. Kelishuv: har ochilishda 0.7s tejash o'rniga kamera
            # xatti-harakati O'ZGARGAN kamdan-kam holatda qisqa g'alizlik.
            self._verifying = False
            if fresh != self._inter_enc:
                log.warning("Inter-shifr keshi XATO edi (kesh=%s, aniqlangan=%s) — "
                            "tuzatildi, kesh yangilandi (toza=%.2f deshifr=%.2f)",
                            self._inter_enc, fresh, d_clear, d_dec)
                self._inter_enc = fresh
                if self._cache_key:
                    _enc_cache_store(self._cache_key, fresh)
            return
        self._inter_enc = fresh
        log.info("Inter (P/B) slice shifri: %s (farqli baytlar: toza=%.2f "
                 "deshifr=%.2f, %d namuna)",
                 "SHIFRLANGAN -> deshifrlanadi" if self._inter_enc else "TOZA -> tegilmaydi",
                 d_clear, d_dec, n)
        if self._cache_key:
            _enc_cache_store(self._cache_key, self._inter_enc)

    def _count(self, ntype: int, decrypted: bool) -> None:
        """NAL turlari taqsimotini yig'ib, BIR MARTA hisobot beradi.

        Buzilgan oqimni tashxislash uchun: masalan param-set (32/33/34) NAL'lari
        deshifrlanmagan bo'lsa yoki inter slice'lar (1) deshifrlanayotgan bo'lsa —
        variant noto'g'ri tanlangan."""
        st = self._nal_stats.setdefault(ntype, [0, 0])
        st[0 if decrypted else 1] += 1
        if self._first_nals is not None:
            self._first_nals.append(ntype)
            if len(self._first_nals) >= 24:
                log.info("Boshlanish NAL ketma-ketligi (ffmpeg'ga): %s",
                         " ".join(str(t) for t in self._first_nals))
                self._first_nals = None
        if not self._stats_logged and sum(sum(v) for v in self._nal_stats.values()) >= 600:
            self._stats_logged = True
            parts = ", ".join(
                f"tur{t}: {v[0]}shifr/{v[1]}toza" for t, v in sorted(self._nal_stats.items()))
            log.info("NAL taqsimoti (variant=%s): %s", self.variant, parts)

    def _emit_nal(self, out, nal) -> None:
        if self._decrypt is None:
            return  # shifr aniqlanmaguncha chiqarmaymiz
        if not self._decrypt:                       # toza oqim
            # Darvoza toza oqimda ham kerak: oqim GOP o'rtasidan boshlanadi.
            h = self._clear or 2
            nt = (nal[0] & 0x1F) if h == 1 else ((nal[0] >> 1) & 0x3F)
            if not self._irap_gate(nt, h):
                return
            out += _ANNEXB_START + bytes(nal)
            return
        if self._drop:
            # B-hint + SELEKTIV: I-freym/param-set NAL'da toza tur-ko'rsatkich (dublikat
            # header) bor. Shifrli bo'lsa AES-deshifr header'ga mos keladi; juda qisqa
            # (PPS) bo'lsa AESsiz dublikat; P-freymda ko'rsatkich yo'q (toza).
            hdr = self._drop
            if len(nal) >= hdr + 16 and self._aes16(nal[hdr:hdr + 16])[:hdr] == bytes(nal[:hdr]):
                out += _ANNEXB_START + self._decrypt_body(nal[hdr:])   # shifrli -> to'liq NAL
            elif len(nal) >= 2 * hdr and bytes(nal[hdr:2 * hdr]) == bytes(nal[:hdr]):
                out += _ANNEXB_START + bytes(nal[hdr:])                 # toza dublikat -> tashlash
            else:
                out += _ANNEXB_START + bytes(nal)                       # oddiy toza NAL (P-freym)
            return
        hdr = self._clear
        if hdr == 0:                                    # B-whole: butun NAL shifr
            out += _ANNEXB_START + self._decrypt_body(nal)
            return
        if len(nal) <= hdr:
            out += _ANNEXB_START + bytes(nal)
            return
        # variant A + SELEKTIV: faqat kalit NAL turini deshifr, inter (P/B) slice toza
        ntype = (nal[0] & 0x1F) if hdr == 1 else ((nal[0] >> 1) & 0x3F)
        enc = self._H264_ENC_TYPES if hdr == 1 else self._HEVC_ENC_TYPES
        # Inter-shifr qarori uchun NAMUNA darvozadan QAT'IY NAZAR olinadi: shunda
        # qaror IRAP kutish paytida tayyor bo'ladi va qo'shimcha kechikish bo'lmaydi.
        if ntype not in enc and (self._inter_enc is None or self._verifying):
            self._sample_inter(nal, hdr)
        if self._inter_enc is not None and self._pending is not None:
            self._flush(out)       # qaror yetib keldi — buferni bo'shatamiz
        if not self._irap_gate(ntype, hdr):
            return
        if ntype in enc:
            # IRAP + param-set: variant allaqachon aniq, darhol chiqaramiz (yoki
            # inter qarori kutilayotgan bo'lsa TARTIBNI saqlab buferga qo'yamiz).
            self._out(out, _ANNEXB_START + bytes(nal[:hdr])
                      + self._decrypt_body(nal[hdr:]))
            self._count(ntype, True)
            return
        # inter (P/B) slice — qaror hali yo'q bo'lsa TARTIBNI saqlab buferlaymiz
        # (namuna yuqorida allaqachon olindi).
        if self._inter_enc is None:
            self._hold(out, ("inter", bytes(nal), hdr), len(nal))
            return
        self._out(out, self._inter_bytes(nal, hdr))
        self._count(ntype, bool(self._inter_enc))

    def _emit(self, out: bytearray, nal) -> None:  # HEVC RTP (2-bayt header, VPS=tur 32)
        if len(nal) < 2:
            return
        self._classify(nal, 2, lambda b: ((b >> 1) & 0x3F) == 32, lambda x: x == 0x0C)
        self._emit_nal(out, nal)


class _HevcRtpDecryptor(_NalDecryptBase):
    """RTP/HEVC (batareyali kamera/IPC) — RTP paketlarini Annex-B ga aylantiradi."""

    def __init__(self, key: str, *, cache_key: str | None = None):
        super().__init__(key, cache_key=cache_key)
        self._cur = None       # joriy FU (fragment) yig'indisi

    def feed(self, rtp_packet: bytes) -> bytes:
        from pyezvizapi.stream import rtp_payload

        if (rtp_packet[1] & 0x7F) != _HEVC_VIDEO_PT:
            return b""
        pl = rtp_payload(rtp_packet)
        if len(pl) < 3:
            return b""
        out = bytearray()
        nal_type = (pl[0] >> 1) & 0x3F
        if nal_type == 49:  # FU — bo'lingan NAL
            fu = pl[2]
            start_bit = fu >> 7
            end_bit = (fu >> 6) & 1
            fu_type = fu & 0x3F
            if start_bit:
                nh0 = ((pl[0] & 0x81) | (fu_type << 1)) & 0xFF
                self._cur = bytearray([nh0, pl[1]])
                self._cur += pl[3:]
            elif self._cur is not None:
                self._cur += pl[3:]
            if end_bit and self._cur is not None:
                self._emit(out, self._cur)
                self._cur = None
        elif nal_type == 48:  # AP — bir paketda bir nechta NAL
            i = 2
            while i + 2 <= len(pl):
                sz = int.from_bytes(pl[i:i + 2], "big")
                i += 2
                self._emit(out, pl[i:i + sz])
                i += sz
        else:  # yagona NAL (VPS/SPS/PPS/slice)
            self._emit(out, pl)
        return bytes(out)


class _H264RtpDecryptor(_NalDecryptBase):
    """RTP/H.264 — NAL header 1 bayt. FU-A=28, STAP-A=24, yagona NAL=1..23.

    Ba'zi Hik/EZVIZ kameralar va NVR kanallari HEVC emas, H.264 yuboradi.
    Shifr avto-aniqlash SPS (tur 7) profile_idc orqali ([[_HevcRtpDecryptor]] VPS
    o'rniga)."""

    def __init__(self, key: str, *, cache_key: str | None = None):
        super().__init__(key, cache_key=cache_key)
        self._cur = None

    def _emit(self, out: bytearray, nal) -> None:  # override: 1-bayt header, SPS=tur 7
        if len(nal) < 1:
            return
        self._classify(nal, 1, lambda b: (b & 0x1F) == 7, lambda x: x in _H264_PROFILES)
        self._emit_nal(out, nal)

    def feed(self, rtp_packet: bytes) -> bytes:
        from pyezvizapi.stream import rtp_payload

        if (rtp_packet[1] & 0x7F) != _HEVC_VIDEO_PT:
            return b""
        pl = rtp_payload(rtp_packet)
        if len(pl) < 1:
            return b""
        out = bytearray()
        t = pl[0] & 0x1F
        if t == 28:  # FU-A — bo'lingan NAL
            if len(pl) < 2:
                return b""
            fu = pl[1]
            start_bit = fu >> 7
            end_bit = (fu >> 6) & 1
            fu_type = fu & 0x1F
            if start_bit:
                self._cur = bytearray([(pl[0] & 0xE0) | fu_type])
                self._cur += pl[2:]
            elif self._cur is not None:
                self._cur += pl[2:]
            if end_bit and self._cur is not None:
                self._emit(out, self._cur)
                self._cur = None
        elif t == 24:  # STAP-A — bir paketda bir nechta NAL
            i = 1
            while i + 2 <= len(pl):
                sz = int.from_bytes(pl[i:i + 2], "big")
                i += 2
                self._emit(out, pl[i:i + sz])
                i += sz
        else:  # yagona NAL (SPS/PPS/slice)
            self._emit(out, pl)
        return bytes(out)


def detect_rtp_codec(payload0: int) -> str | None:
    """RTP video payload birinchi baytidan codec: 'h264' | 'hevc' | None.

    FAQAT bir-biriga to'qnashmaydigan ANIQ belgilar bo'yicha aniqlaymiz; yalang'och
    slice (masalan H.264 0x21 — tur 1) noaniq, shuning uchun None qaytaramiz va
    chaqiruvchi keyingi paketni tekshiradi. Ilgari "1<=hevc_t<=40" qoidasi H.264
    slice'larini (0x21 -> hevc_t=16) xato HEVC deb belgilab, noto'g'ri dekoder
    tanlanishiga sabab bo'lardi (oqim qotardi)."""
    h264_t = payload0 & 0x1F
    # H.264 — eng ishonchli belgilar (FU-A/STAP-A juda keng tarqalgan)
    if h264_t in (28, 24):                          # FU-A / STAP-A
        return "h264"
    if payload0 in (0x67, 0x68, 0x65, 0x47, 0x48, 0x45, 0x27, 0x28, 0x25):  # SPS/PPS/IDR (turli ref_idc)
        return "h264"
    # HEVC — FU (tur 49) va VPS/SPS/PPS (to'qnashmaydigan baytlar)
    if payload0 in (0x62, 0x63):                    # HEVC FU (tur 49)
        return "hevc"
    if payload0 in (0x40, 0x42, 0x44):              # HEVC VPS/SPS/PPS
        return "hevc"
    return None  # noaniq (yalang'och slice) -> keyingi paketni kutamiz


class _PsStreamDecryptor(_NalDecryptBase):
    """MPEG-PS oqimini demux qilib, ES'dagi NAL body'larni dekodlaydi.

    Hik-Connect NVR'lari oqimni RTP emas, MPEG-PS shaklida yuboradi. Kutubxonaning
    PS dekodlovchisi cheksiz video-PES'da ishlamaydi, shuning uchun o'zimiz demux
    qilamiz. H.264 va HEVC ni avtomatik aniqlaydi (param-set NAL turi bo'yicha)."""

    def __init__(self, key: str, *, cache_key: str | None = None):
        super().__init__(key, cache_key=cache_key)
        self._buf = bytearray()   # demux qilinmagan PS qoldig'i
        self._es = bytearray()    # ajratilgan ES (NAL ga ajratilmagan qoldiq)
        # Shifr boshlanish offseti: 0 = BUTUN NAL shifrli (header ham),
        # 1 = H.264 (1-baytli header toza), 2 = HEVC (2-baytli header toza).
        self._enc_off = None
        self._pending = []        # codec/shifr aniqlanmaguncha NAL'larni vaqtincha saqlaymiz

    def _emit(self, out: bytearray, nal) -> None:  # override: codec/variant-aware
        if not nal:
            return
        if self._decrypt is None:                 # codec/shifr hali aniqlanmagan
            self._pending.append(bytes(nal))
            res = self._decide(self._pending)
            if res == "keyerror":
                self.key_error = True
                self._pending = []
                return
            if res is None:
                return                            # aniqlanmaguncha garbage chiqarmaymiz
            self.codec, self._enc_off, self._decrypt = res
            for n in self._pending:               # yig'ilgan NAL'larni chiqaramiz
                self._write_nal(out, n)
            self._pending = []
            return
        self._write_nal(out, nal)

    def _write_nal(self, out: bytearray, nal) -> None:
        off = self._enc_off
        if len(nal) <= off:
            out += _ANNEXB_START + bytes(nal)
            return
        # off=0 -> butun NAL shifrli (header ham); aks holda header toza, body shifrli
        out += _ANNEXB_START + bytes(nal[:off]) + self._decrypt_body(nal[off:])

    def _aes16(self, data) -> bytes:
        return self._AES.new(self.key, self._AES.MODE_ECB).decrypt(bytes(data[:16]))

    @staticmethod
    def _h264_valid(b: int) -> bool:
        return (b & 0x80) == 0 and (b & 0x1F) in (1, 5, 6, 7, 8, 9)

    @staticmethod
    def _hevc_valid(b: int) -> bool:
        return (b & 0x80) == 0 and ((b >> 1) & 0x3F) in (0, 1, 2, 4, 19, 20, 21, 32, 33, 34, 39, 40)

    def _corrob(self, big, decoded) -> bool:
        """Talqinni tasdiqlash: namunadagi NAL header'larining ko'pi shu codec uchun
        haqiqiymi (yagona soxta moslikdan himoya)."""
        return sum(decoded) >= max(2, len(big) // 2)

    def _decide(self, nals):
        """Yig'ilgan NAL'lardan codec + shifr offseti + shifrli/toza ni aniqlaydi.
        4 talqin: header toza (off=hdr) yoki butun NAL shifrli (off=0), H.264/HEVC.
        Qaytaradi: (codec, enc_off, decrypt) | 'keyerror' | None."""
        big = [n for n in nals if len(n) >= 18]
        if len(big) < 3:
            return None
        prof = _H264_PROFILES
        dec_b = [self._aes16(n) for n in big]     # variant B: butun NAL deshifrlangan boshi
        for n in big:
            # --- A) header TOZA, body shifrli yoki toza ---
            if (n[0] & 0x80) == 0 and (n[0] & 0x1F) == 7:           # H.264 SPS
                if n[1] in prof and self._corrob(big, [self._h264_valid(m[0]) for m in big]):
                    return ("h264", 1, False)                       # toza oqim
                if self._aes16(n[1:17])[0] in prof and self._corrob(big, [self._h264_valid(m[0]) for m in big]):
                    return ("h264", 1, True)
            if (n[0] & 0x80) == 0 and ((n[0] >> 1) & 0x3F) == 32:   # HEVC VPS
                if n[2] == 0x0C and self._corrob(big, [self._hevc_valid(m[0]) for m in big]):
                    return ("hevc", 2, False)                       # toza oqim
                if self._aes16(n[2:18])[0] == 0x0C and self._corrob(big, [self._hevc_valid(m[0]) for m in big]):
                    return ("hevc", 2, True)
        for i in range(len(big)):
            # --- B) BUTUN NAL shifrli (header ham) ---
            d = dec_b[i]
            if (d[0] & 0x1F) == 7 and d[1] in prof and self._corrob(big, [self._h264_valid(x[0]) for x in dec_b]):
                return ("h264", 0, True)
            if ((d[0] >> 1) & 0x3F) == 32 and d[2] == 0x0C and self._corrob(big, [self._hevc_valid(x[0]) for x in dec_b]):
                return ("hevc", 0, True)
        if len(big) >= 24:        # yetarli NAL bor, lekin hech narsa mos kelmadi -> kod xato
            return "keyerror"
        return None

    def _demux(self) -> None:
        b = self._buf
        n = len(b)
        pos = 0
        while True:
            sc = b.find(b"\x00\x00\x01", pos)
            if sc < 0 or sc + 4 > n:
                break
            code = b[sc + 3]
            if code == 0xBA:  # pack header
                if sc + 14 > n:
                    break
                end = sc + 14 + (b[sc + 13] & 0x07)
                if end > n:
                    break
                pos = end
            elif code == 0xB9:  # MPEG end
                pos = sc + 4
            elif 0xE0 <= code <= 0xEF:  # video PES
                if sc + 9 > n:
                    break
                length = (b[sc + 4] << 8) | b[sc + 5]
                payload_start = sc + 9 + b[sc + 8]
                if payload_start > n:
                    break
                if length > 0:
                    end = sc + 6 + length
                    if end > n:
                        break
                    self._es += b[payload_start:end]
                    pos = end
                else:  # cheksiz: keyingi start kodgacha
                    nxt = b.find(b"\x00\x00\x01", payload_start)
                    if nxt < 0:
                        break
                    self._es += b[payload_start:nxt]
                    pos = nxt
            else:  # boshqa stream — uzunlik bo'yicha o'tkazamiz
                if sc + 6 > n:
                    break
                length = (b[sc + 4] << 8) | b[sc + 5]
                end = sc + 6 + length
                if end > n:
                    break
                pos = end
        self._buf = b[pos:]

    def _process_es(self) -> bytes:
        e = self._es
        starts = []
        i = e.find(b"\x00\x00\x01", 0)
        while i >= 0:
            starts.append(i)
            i = e.find(b"\x00\x00\x01", i + 3)
        if len(starts) < 2:
            return b""
        out = bytearray()
        for k in range(len(starts) - 1):
            nal = e[starts[k] + 3:starts[k + 1]]
            if nal and nal[-1:] == b"\x00":
                nal = nal[:-1]
            self._emit(out, nal)
        self._es = e[starts[-1]:]
        return bytes(out)

    def _cap(self) -> None:
        """Buferlar chegaradan oshsa (buzuq/start-code'siz oqim) — oxirgi start
        kodga resync qilamiz, aks holda xotira cheksiz o'sardi."""
        if len(self._buf) > _MAX_PS_BUF:
            i = self._buf.rfind(b"\x00\x00\x01")
            self._buf = self._buf[i:] if i > 0 else bytearray()
            log.warning("PS demux buferi %d baytdan oshdi — resync", _MAX_PS_BUF)
        if len(self._es) > _MAX_PS_BUF:
            i = self._es.rfind(b"\x00\x00\x01")
            self._es = self._es[i:] if i > 0 else bytearray()
            log.warning("PS ES buferi %d baytdan oshdi — resync", _MAX_PS_BUF)

    def feed(self, ps_chunk: bytes) -> bytes:
        self._buf += ps_chunk
        self._demux()
        out = self._process_es()
        self._cap()
        return out
