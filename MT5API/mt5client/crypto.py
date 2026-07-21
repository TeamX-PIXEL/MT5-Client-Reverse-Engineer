"""AES-256-CBC encryption and XOR chain cipher for MT5 WebSocket protocol."""
from Crypto.Cipher import AES


STATIC_KEY = bytes.fromhex(
    '02de02a1a65cc794684fcbea1ecb0fd7'
    '4ae657e43662c11eee885d2fd64f4964'
)
ZERO_IV = bytes(16)

XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45,
                 123, 57, 8, 254, 33, 187, 65, 88])


def aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len] * pad_len)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(padded)


def aes_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    p = pt[-1]
    if 1 <= p <= 16 and all(b == p for b in pt[-p:]):
        return pt[:-p]
    return pt


def xor_encrypt(data: bytes) -> bytes:
    result = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        result[i] = (b ^ (prev + XOR_KEY[i & 0xF])) & 0xFF
        prev = b
    return bytes(result)


def xor_decrypt(data: bytes) -> bytes:
    result = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        result[i] = (b ^ (prev + XOR_KEY[i & 0xF])) & 0xFF
        prev = result[i]
    return bytes(result)
