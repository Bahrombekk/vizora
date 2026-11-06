"""RTSP URL kabi maxfiy maydonlarni AES-256-GCM bilan shifrlash.

Saqlash formati (BYTEA): nonce(12) || ciphertext || tag(16) — AESGCM tag'ni
ciphertext oxiriga qo'shadi. Ochishda nonce ajratib olinadi.
"""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vizora.core.config import settings

_NONCE_LEN = 12


def _key() -> bytes:
    key = bytes.fromhex(settings.aes_key_hex)
    if len(key) != 32:
        raise ValueError("AES_KEY_HEX 32 bayt (64 hex belgi) bo'lishi shart")
    return key


def encrypt(plaintext: str) -> bytes:
    """Matnni shifrlab, BYTEA uchun baytlar qaytaradi."""
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt(blob: bytes) -> str:
    """encrypt() qaytargan baytlarni ochib, asl matnni qaytaradi."""
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    return AESGCM(_key()).decrypt(nonce, ct, None).decode("utf-8")
