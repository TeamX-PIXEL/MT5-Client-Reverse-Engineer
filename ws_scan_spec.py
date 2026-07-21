#!/usr/bin/env python3
"""
Brute-force scan the full symbol spec (cmd_id=18) response
to find where key fields are located in the binary data.
Uses same connection pattern as ws_diag_exemode.py.
"""
import asyncio, struct, time, random, ssl, zlib
import websockets
from Crypto.Cipher import AES

WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER = "Exness-MT5Trial17"
SERVER_IP = "15.206.31.153"
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_encrypt(key, plaintext):
    pad_len = 16 - (len(plaintext) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(plaintext + bytes([pad_len] * pad_len))

def aes_decrypt(key, ciphertext):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

def pack_data(cmd_id, encrypted_data):
    return struct.pack('<II', len(encrypted_data), 1) + encrypted_data

def build_command(cmd_id, payload=b''):
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0, 255)
    cmd[1] = random.randint(0, 255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload:
        cmd[4:4+len(payload)] = payload
    return bytes(cmd)

def parse_response(data):
    if len(data) < 5:
        return None
    return {
        'tag': struct.unpack('<H', data[0:2])[0],
        'cmd_id': struct.unpack('<H', data[2:4])[0],
        'res_code': data[4],
        'res_body': data[5:]
    }

def build_login_payload(login_id, password, url):
    h = bytearray(912)
    pw = password.encode('utf-16-le')
    h[4:4+len(pw)] = pw
    struct.pack_into('<I', h, 476, len(url))
    ip = url.encode('utf-16-le')
    h[480:480+len(ip)] = ip
    struct.pack_into('<Q', h, 736, login_id)
    return bytes(h)

async def send_cmd(ws, sk, cmd_id, payload=b''):
    await ws.send(pack_data(cmd_id, aes_encrypt(sk, build_command(cmd_id, payload))))

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(
        WS_URL, ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin': 'https://15.206.31.153:443'},
    ) as ws:
        print("[+] Connected")

        # Auth
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        session_key = resp['res_body'][66:]
        print(f"[+] Auth OK, session_key={session_key.hex()}")

        # Login
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(session_key, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
        print(f"[+] Login OK")

        # Request full symbol spec for EURUSDm (id=426)
        sym_id = 426
        payload = struct.pack('<II', 1, sym_id)  # count=1, symbol_id=426
        await send_cmd(ws, session_key, 18, payload)

        # Receive spec response
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=2)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp and resp['cmd_id'] == 18:
                    body = resp['res_body']
                    print(f"[+] Spec response: cmd={resp['cmd_id']}, res_code={resp['res_code']}, body_size={len(body)}")
                    break
            except asyncio.TimeoutError:
                continue
        else:
            print("[-] No spec response"); return

        # Parse: body is the raw response body
        # The body format: [res_code:1][data] ? No, res_code is already extracted
        # body = res_body = everything after the 5-byte header
        # So body[0] is the first byte of the response data
        
        data = body
        count = struct.unpack_from('<I', data, 0)[0]
        print(f"[*] Count: {count}")
        
        sym_data = data[4:]  # skip count
        print(f"[*] Symbol data size: {len(sym_data)} bytes")

        # Known values to search for
        known_uint32 = {
            426: 'symbol_id',
            5: 'digits',
            0: 'zero',
            1: 'one',
            2: 'two',
            3: 'three',
            64: 'propLength_64',
            128: 'propLength_128',
            256: 'propLength_256',
        }

        # Scan for uint32 values
        print('\n=== uint32 scans (little-endian) ===')
        for i in range(0, len(sym_data) - 3):
            val = struct.unpack_from('<I', sym_data, i)[0]
            if val in known_uint32:
                print(f'  offset {i:4d} (0x{i:03x}): {val} = {known_uint32[val]}')

        # Find UTF-16LE strings
        print('\n=== UTF-16LE string scans ===')
        targets = ['EURUSDm', 'EUR/USD', 'Euro vs US Dollar', 'USD', 'Standard']
        for target in targets:
            encoded = target.encode('utf-16-le')
            idx = sym_data.find(encoded)
            if idx >= 0:
                print(f'  "{target}" found at offset {idx} (0x{idx:03x})')
                # Show surrounding bytes
                start = max(0, idx-8)
                end = min(len(sym_data), idx+len(encoded)+16)
                hex_bytes = ' '.join(f'{sym_data[j]:02x}' for j in range(start, end))
                print(f'    hex: {hex_bytes}')

        # Dump first 2048 bytes as hex
        print('\n=== First 2048 bytes hex dump ===')
        for row in range(0, min(2048, len(sym_data)), 16):
            hex_str = ' '.join(f'{sym_data[row+j]:02x}' for j in range(min(16, len(sym_data)-row)))
            ascii_str = ''.join(chr(sym_data[row+j]) if 32 <= sym_data[row+j] < 127 else '.' for j in range(min(16, len(sym_data)-row)))
            print(f'  [{row:4d}] {hex_str:<48s} {ascii_str}')

if __name__ == '__main__':
    asyncio.run(main())
