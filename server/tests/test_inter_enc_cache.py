"""Inter-shifr qarorining keshi — sovuq startdan 0.65-0.85s oladi.

Qaror kameraning O'ZGARMAS xususiyati, lekin har ochilishda qaytadan aniqlanardi
va qaror kelguncha NAL'lar buferda ushlanardi. Kesh buni yo'q qiladi, ammo
xato keshdan himoya uchun qaror BARIBIR har ochilishda tekshiriladi.
"""
import json

import pytest

from vizora.sources.ezviz import decrypt


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    from vizora.core.config import settings
    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    return tmp_path


def test_no_cache_buffers_until_decided(cache_dir):
    """Kesh yo'q -> eski xatti-harakat: qaror kelguncha buferlanadi."""
    d = decrypt._HevcRtpDecryptor("1234567890", cache_key="SER-1")
    assert d._inter_enc is None
    assert d._pending == []          # buferlash YOQILGAN
    assert d._verifying is False


def test_cache_skips_buffering_but_still_verifies(cache_dir):
    """Kesh bor -> buferlash YO'Q (tez), lekin tekshirish YOQILGAN."""
    decrypt._enc_cache_store("SER-1", True)
    d = decrypt._HevcRtpDecryptor("1234567890", cache_key="SER-1")
    assert d._inter_enc is True
    assert d._pending is None        # birinchi kadr DARHOL ketadi
    assert d._verifying is True      # ammo qaror tekshiriladi


def test_wrong_cache_self_corrects(cache_dir):
    """Kesh xato bo'lsa qaror TUZATILADI va kesh yangilanadi.

    Bu himoya bo'lmasa kamera xatti-harakati o'zgarganda har P-freym buzilardi
    va sabab log'da ko'rinmasdi."""
    decrypt._enc_cache_store("SER-2", True)          # "shifrlangan" deb keshlangan
    d = decrypt._HevcRtpDecryptor("1234567890", cache_key="SER-2")
    assert d._inter_enc is True
    # Aniqlash TOZA deb qaror qilsin: "toza" baytlar takrorlanuvchi (tuzilishli),
    # "deshifr" baytlar esa har xil (tasodifiy) bo'lganda shunday chiqadi.
    d._inter_clear = [7] * decrypt._INTER_SAMPLES
    d._inter_dec = list(range(decrypt._INTER_SAMPLES))
    d._decide_inter()
    assert d._inter_enc is False                     # tuzatildi
    assert d._verifying is False
    with open(decrypt._enc_cache_path("SER-2"), encoding="utf-8") as f:
        assert json.load(f)["inter_enc"] is False    # kesh ham yangilandi


def test_decision_is_persisted_for_next_open(cache_dir):
    """Birinchi ochilishdagi qaror keyingi ochilish uchun saqlanadi."""
    d = decrypt._HevcRtpDecryptor("1234567890", cache_key="SER-3")
    d._inter_clear = list(range(decrypt._INTER_SAMPLES))   # tasodifiy = shifrlangan
    d._inter_dec = [3] * decrypt._INTER_SAMPLES            # tuzilishli = to'g'ri o'qish
    d._decide_inter()
    assert d._inter_enc is True
    assert decrypt._enc_cache_load("SER-3") is True


def test_cache_key_optional_and_isolated(cache_dir):
    """Kalit berilmasa kesh ishlatilmaydi; kalitlar bir-biriga ta'sir qilmaydi."""
    d = decrypt._HevcRtpDecryptor("1234567890")
    assert d._cache_key is None and d._verifying is False
    decrypt._enc_cache_store("SER-A", True)
    assert decrypt._enc_cache_load("SER-A") is True
    assert decrypt._enc_cache_load("SER-B") is None


def test_all_decryptors_accept_cache_key(cache_dir):
    """Uchala dekoder ham kalitni qabul qilsin (publish.py hammasini chaqiradi)."""
    for cls in (decrypt._HevcRtpDecryptor, decrypt._H264RtpDecryptor,
                decrypt._PsStreamDecryptor):
        assert cls("1234567890", cache_key="K")._cache_key == "K"
