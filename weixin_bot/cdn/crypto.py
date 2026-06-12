"""AES-128-ECB 加解密 — CDN 媒体传输用.

对应原版 cdn/aes-ecb.ts. 微信 CDN 要求 AES-128-ECB + PKCS7 padding.
Node.js createCipheriv("aes-128-ecb", key, null) 默认 PKCS7, Python 需显式处理.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

BLOCK_BITS = 128  # AES block = 16 bytes


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密, PKCS7 padding."""
    padder = PKCS7(BLOCK_BITS).padder()
    padded = padder.update(plaintext) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密, 自动去除 PKCS7 padding."""
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    dec = cipher.decryptor()
    decrypted = dec.update(ciphertext) + dec.finalize()

    unpadder = PKCS7(BLOCK_BITS).unpadder()
    return unpadder.update(decrypted) + unpadder.finalize()


def padded_size(plaintext_size: int) -> int:
    """PKCS7 padding 后的密文大小 (对齐到 16 字节边界)."""
    return ((plaintext_size // 16) + 1) * 16
