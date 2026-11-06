"""RTSP URL shifrlash — AES-256-GCM."""
import pytest

from vizora.core import crypto


def test_encrypt_decrypt_roundtrip():
    url = "rtsp://admin:p@ss@10.20.1.15:554/Streaming/Channels/101"
    blob = crypto.encrypt(url)
    assert isinstance(blob, bytes)
    assert blob != url.encode()  # shifrlangan
    assert crypto.decrypt(blob) == url


def test_nonce_is_random():
    """Bir xil matn har safar boshqa shifrmatn beradi (nonce tasodifiy)."""
    a = crypto.encrypt("rtsp://x")
    b = crypto.encrypt("rtsp://x")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "rtsp://x"


def test_tampered_blob_fails():
    blob = bytearray(crypto.encrypt("rtsp://x"))
    blob[-1] ^= 0x01  # tag buziladi
    with pytest.raises(Exception):
        crypto.decrypt(bytes(blob))
