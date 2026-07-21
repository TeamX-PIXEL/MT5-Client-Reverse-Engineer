#!/usr/bin/env python3
"""Debug cmd_id=4 response - parse as order format (356B)."""
import asyncio, struct, time, random, ssl
import websockets
from Crypto.Cipher import AES

WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER_IP = "15.206.31.153"
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_enc(key, pt):
    pad = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad]*pad))

def aes_dec(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

def pack_data(cmd_id, enc):
    return struct.pack('<II', len(enc), 1) + enc

def mk_cmd(cmd_id, payload=b''):
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0,255); cmd[1] = random.randint(0,255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload: cmd[4:4+len(payload)] = payload
    return bytes(cmd)

def parse_resp(data):
    if len(data) < 5: return None
    return {'tag': struct.unpack('<H', data[0:2])[0], 'cmd_id': struct.unpack('<H', data[2:4])[0],
            'res_code': data[4], 'res_body': data[5:]}

def mk_login(login_id, password, url):
    h = bytearray(912)
    pw = password.encode('utf-16-le'); h[4:4+len(pw)] = pw
    struct.pack_into('<I', h, 476, len(url))
    ip = url.encode('utf-16-le'); h[480:480+len(ip)] = ip
    struct.pack_into('<Q', h, 736, login_id)
    return bytes(h)

async def send_cmd(ws, sk, cmd_id, payload=b''):
    await ws.send(pack_data(cmd_id, aes_enc(sk, mk_cmd(cmd_id, payload))))

async def drain(ws, sk, expected_cmd, timeout=8):
    msgs = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2)
            r = parse_resp(aes_dec(sk, raw[8:]))
            if r:
                msgs.append(r)
                if r['cmd_id'] == expected_cmd:
                    return msgs
        except asyncio.TimeoutError:
            continue
    return msgs

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        # Auth
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = parse_resp(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        # Login
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")

        # Request cmd_id=4
        await send_cmd(ws, sk, 4)
        msgs = await drain(ws, sk, 4)
        for m in msgs:
            if m['cmd_id'] == 4:
                body = m['res_body']
                print(f"[+] Response body size: {len(body)} bytes")
                
                # Hex dump first 100 bytes
                print(f"[+] Hex dump (first 100 bytes):")
                for i in range(0, min(100, len(body)), 16):
                    hex_str = ' '.join(f'{b:02x}' for b in body[i:i+16])
                    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in body[i:i+16])
                    print(f"  {i:04d}: {hex_str}  {ascii_str}")
                
                # Try parsing as [count][ticket][356B order]
                cnt = struct.unpack_from('<I', body, 0)[0]
                ticket = struct.unpack_from('<q', body, 4)[0]
                print(f"\n[+] Count: {cnt}")
                print(f"[+] Ticket: {ticket}")
                
                if len(body) >= 8 + 356:
                    order = body[8:8+356]
                    print(f"\n=== ORDER DATA (356 bytes) ===")
                    print(f"  [0:8] ticket: {struct.unpack_from('<q', order, 0)[0]}")
                    print(f"  [8:72] order_id: {order[8:72].decode('utf-16-le', errors='ignore').rstrip(chr(0))!r}")
                    print(f"  [72:136] symbol: {order[72:136].decode('utf-16-le', errors='ignore').rstrip(chr(0))!r}")
                    print(f"  [136:140] time_setup: {struct.unpack_from('<I', order, 136)[0]}")
                    print(f"  [148:152] order_type: {struct.unpack_from('<I', order, 148)[0]}")
                    print(f"  [152:156] type_filling: {struct.unpack_from('<I', order, 152)[0]}")
                    print(f"  [156:160] type_time: {struct.unpack_from('<I', order, 156)[0]}")
                    print(f"  [164:172] price_order: {struct.unpack_from('<d', order, 164)[0]:.5f}")
                    print(f"  [172:180] price_trigger: {struct.unpack_from('<d', order, 172)[0]:.5f}")
                    print(f"  [180:188] price_current: {struct.unpack_from('<d', order, 180)[0]:.5f}")
                    print(f"  [188:196] volume_initial: {struct.unpack_from('<q', order, 188)[0]}")
                    print(f"  [196:204] volume_current: {struct.unpack_from('<q', order, 196)[0]}")
                    print(f"  [204:208] order_state: {struct.unpack_from('<I', order, 204)[0]}")

asyncio.run(main())
