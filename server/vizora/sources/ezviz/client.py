"""EZVIZ / Hik-Connect bulutiga login — terminal-limit chetlab o'tuvchi client.

Ikki provayder (bir xil backend, farqli login):
  * "ezviz" — apiisgp.ezvizlife.com, /v3/users/login/v5, clientType=1 (Android).
  * "hik"   — Hik-Connect: api.hik-connect.com (global, regionga redirect 1100),
              /v3/users/login/v2, clientType=55. NVR hisoblari uchun.
Har xil clientType alohida "slot" oladi — egasining mobil ilovasini chiqarib
yubormaydi.

Bu modul faqat `requests` (bloklovchi) ishlatadi — `asyncio.to_thread` orqali
chaqiriladi (tokens.py).
"""
from __future__ import annotations

import hashlib

import requests

# Ezviz tool'idan olingan, sinovdan o'tgan feature code.
FEATURE_CODE = "1fc28fa018178a1cd1c091b13b2f9f02"
# Provayder -> clientType (VTM/pagelist va login uchun ham ishlatiladi).
_CLIENT_TYPE = {"ezviz": "1", "hik": "55"}


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


class MfaRequired(Exception):
    """Kalit olish uchun 2FA (email) kodi kerak bo'lganda ko'tariladi."""


class EzvizLoginError(RuntimeError):
    """Login yoki API xatosi."""


def _safe_domain(domain: str) -> str:
    """Login md5(parol) yuboriladigan domen — FAQAT EZVIZ/Hik bulut domeni.

    Schema yozishda tekshiradi, lekin bazadagi eski/qo'lda o'zgartirilgan qiymat
    yoki bulut javobidagi redirect domeni ham shu yerda qayta tekshiriladi
    (audit 2026-11-03): aks holda parol xeshi begona serverga ketardi."""
    from vizora.schemas.ezviz_account import _check_region

    try:
        d = _check_region(domain)
    except ValueError as exc:
        raise EzvizLoginError(f"Ruxsat etilmagan bulut domeni: {domain!r}") from exc
    if not d:
        raise EzvizLoginError("Bulut domeni bo'sh")
    return d


class EzvizClient:
    def __init__(self, email: str, password: str, region: str, provider: str = "ezviz"):
        self.email = email
        self.password = password
        self.provider = provider if provider in _CLIENT_TYPE else "ezviz"
        self.api_url = _safe_domain(region)
        self.client_type = _CLIENT_TYPE[self.provider]
        self.session_id: str | None = None
        self.rf_session_id: str | None = None
        self.user_code: str | None = None

        self.session = requests.Session()
        self.session.headers.update({
            "clientType": self.client_type,
            "customNo": "1000001",
            "clientVersion": "5.12.1.0517",
            "lang": "en_US",
            "featureCode": FEATURE_CODE,
            "Content-Type": "application/x-www-form-urlencoded",
        })

    def login(self) -> dict:
        return self._login_hik() if self.provider == "hik" else self._login_ezviz()

    def _login_ezviz(self) -> dict:
        r = self.session.post(
            f"https://{self.api_url}/v3/users/login/v5",
            data={
                "account": self.email,
                "password": _md5(self.password),
                "featureCode": FEATURE_CODE,
                "msgType": 0,
                "cuName": "SSmartPhone_Android",
            },
            timeout=25,
        )
        data = r.json()
        if data.get("meta", {}).get("code") != 200:
            raise EzvizLoginError(f"Login xatosi: {data.get('meta')}")
        self.session_id = data["loginSession"]["sessionId"]
        self.rf_session_id = data["loginSession"]["rfSessionId"]
        self.api_url = _safe_domain(data["loginArea"]["apiDomain"])
        self.user_code = data["loginUser"].get("userCode")
        self.session.headers.update({"sessionId": self.session_id})
        return data

    def _login_hik(self) -> dict:
        """Hik-Connect: /v2 endpoint, region redirect (1100) ni kuzatamiz."""
        data = None
        for _ in range(4):
            r = self.session.post(
                f"https://{self.api_url}/v3/users/login/v2",
                data={
                    "account": self.email,
                    "password": _md5(self.password),
                    "featureCode": FEATURE_CODE,
                },
                timeout=25,
            )
            data = r.json()
            code = data.get("meta", {}).get("code")
            if code == 1100:  # boshqa regionga yo'naltirish
                self.api_url = _safe_domain(data["loginArea"]["apiDomain"])
                continue
            if code in (1013, 1014, 1226):
                raise EzvizLoginError("Login xatosi: email yoki parol noto'g'ri")
            if code == 1015:
                raise EzvizLoginError(
                    "CAPTCHA so'ralyapti — avval Hik-Connect ilovasiga kiring, keyin qayta urining")
            break
        if not data or data.get("meta", {}).get("code") != 200:
            raise EzvizLoginError(f"Login xatosi: {data.get('meta') if data else 'javob yoq'}")
        self.session_id = data["loginSession"]["sessionId"]
        self.rf_session_id = data["loginSession"]["rfSessionId"]
        self.api_url = _safe_domain(data.get("loginArea", {}).get("apiDomain", self.api_url))
        self.user_code = data.get("loginUser", {}).get("userCode")
        self.session.headers.update({"sessionId": self.session_id})
        return data

    def refresh_session(self) -> bool:
        """Sessiyani refresh token bilan yangilash — parol/MFA so'ramaydi.

        Muvaffaqiyatsiz bo'lsa to'liq qayta login qiladi. 24/7 ishlash uchun muhim.
        """
        try:
            r = self.session.put(
                f"https://{self.api_url}/v3/apigateway/login",
                data={
                    "refreshSessionId": self.rf_session_id,
                    "featureCode": FEATURE_CODE,
                },
                timeout=25,
            )
            data = r.json()
            if data.get("meta", {}).get("code") == 200:
                info = data.get("sessionInfo", {})
                self.session_id = info.get("sessionId", self.session_id)
                self.rf_session_id = info.get("refreshSessionId", self.rf_session_id)
                self.session.headers.update({"sessionId": self.session_id})
                return True
        except Exception:  # noqa: BLE001
            pass
        # Refresh ishlamasa — to'liq qayta login
        self.login()
        return True

    def get_devices(self) -> dict[str, dict]:
        """Barcha kameralar: {serial: {name, status, model, channels, encrypted}}.

        status: 1 = online, aks holda offline.
        encrypted: qurilma shifrlangan (oqim ko'rish uchun tasdiqlash kodi kerak)
        — `statusInfos[serial].isEncrypt` dan olinadi.
        """
        result: dict[str, dict] = {}
        offset = 0
        for _ in range(40):  # cheksiz sahifalashga qarshi yuqori chegara
            r = self.session.get(
                f"https://{self.api_url}/v3/userdevices/v1/devices/pagelist",
                params={
                    "filter": "CLOUD,CONNECTION,SWITCH,STATUS,WIFI,NODISTURB,"
                              "P2P,CHANNEL,VTM,FEATURE,UPGRADE,VIDEO_QUALITY,QOS",
                    "groupId": -1, "limit": 50, "offset": offset,
                },
                timeout=25,
            )
            data = r.json()
            status_infos = data.get("statusInfos") or {}
            infos = data.get("deviceInfos") or []
            for dev in infos:
                serial = dev.get("deviceSerial")
                if serial:
                    si = status_infos.get(serial) or {}
                    result[serial] = {
                        "name": dev.get("name", serial),
                        "status": dev.get("status", 0),
                        "model": dev.get("deviceType", ""),
                        "channels": dev.get("channelNumber", 1),
                        "encrypted": bool(si.get("isEncrypt")),
                    }
            page = data.get("page") or {}
            if not infos or not page.get("hasNext"):
                break
            offset += len(infos)
        return result

    def get_channels(self) -> list[dict]:
        """Ulangan NVR/qurilma kanallari: [{serial, channel, name}] — VTM resurslaridan.

        `/devices/pagelist` faqat qurilmalarni (NVR'ni bitta yozuv sifatida)
        qaytaradi; bu yer esa har bir ulangan KANALni alohida nomi bilan beradi —
        ko'p kanalli NVR'da qaysi kanalni qo'shishni tanlash uchun. `localIndex`
        0 = qurilmaning o'zi (NVR), >=1 = ulangan kamera kanali. Sahifalanadi
        (ko'p NVR hisobi bir necha sahifa bo'lishi mumkin)."""
        merged: list[dict] = []
        offset = 0
        for _ in range(40):  # cheksiz sahifalashga qarshi yuqori chegara
            r = self.session.get(
                f"https://{self.api_url}/v3/userdevices/v1/resources/pagelist",
                params={"filter": "VTM", "groupId": -1, "limit": 50, "offset": offset},
                timeout=25,
            )
            pl = r.json()
            res = pl.get("resourceInfos") or []
            merged.extend(res)
            page = pl.get("page") or {}
            if not res or not page.get("hasNext"):
                break
            offset += len(res)
        out: list[dict] = []
        for res in merged:
            try:
                ch = int(res.get("localIndex"))
            except (TypeError, ValueError):
                continue
            if ch < 1:  # 0 = qurilma (NVR) o'zi, kamera emas
                continue
            serial = res.get("deviceSerial")
            if not serial:
                continue
            name = (res.get("resourceName") or f"{serial} CH{ch}").strip()
            out.append({"serial": serial, "channel": ch, "name": name})
        out.sort(key=lambda x: (x["serial"], x["channel"]))
        return out

    def token_dict(self) -> dict:
        """pyezvizapi va publish.py o'qiy oladigan token (sessiya holatini saqlash)."""
        return {
            "session_id": self.session_id,
            "rf_session_id": self.rf_session_id,
            "username": self.email,
            "api_url": self.api_url,
            "provider": self.provider,
            "client_type": self.client_type,
        }
