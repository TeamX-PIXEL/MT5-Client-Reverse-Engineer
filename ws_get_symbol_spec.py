#!/usr/bin/env python3
"""
Get detailed symbol specs: min_lot, max_lot, lot_step, tick_size, tick_value, etc.
"""
import asyncio, struct, time, random, ssl, zlib
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
def pack_data(cid, enc):
    return struct.pack('<II', len(enc), 1) + enc
def mk_cmd(cid, pl=b''):
    cmd = bytearray(4 + len(pl))
    cmd[0] = random.randint(0,255); cmd[1] = random.randint(0,255)
    struct.pack_into('<H', cmd, 2, cid)
    if pl: cmd[4:4+len(pl)] = pl
    return bytes(cmd)
def pr(data):
    if len(data) < 5: return None
    return {'tag': struct.unpack('<H', data[0:2])[0], 'cmd_id': struct.unpack('<H', data[2:4])[0],
            'res_code': data[4], 'res_body': data[5:]}
def mk_login(lid, pw, url):
    h = bytearray(912)
    p = pw.encode('utf-16-le'); h[4:4+len(p)] = p
    struct.pack_into('<I', h, 476, len(url))
    ip = url.encode('utf-16-le'); h[480:480+len(ip)] = ip
    struct.pack_into('<Q', h, 736, lid)
    return bytes(h)
async def sc(ws, sk, cid, pl=b''):
    await ws.send(pack_data(cid, aes_enc(sk, mk_cmd(cid, pl))))
async def recv_cmd(ws, sk, target_cmd, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2)
            r = pr(aes_dec(sk, raw[8:]))
            if r and r['cmd_id'] == target_cmd:
                return r
        except asyncio.TimeoutError:
            continue
    return None

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = pr(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")
        
        # Try cmd_id=6 (GET_SYMBOLS_FULL) - full symbol config
        print(f"\n{'='*60}")
        print("Try cmd_id=6 (GET_SYMBOLS_FULL)")
        print(f"{'='*60}")
        await sc(ws, sk, 6)
        r = await recv_cmd(ws, sk, 6, timeout=10)
        if r:
            body = r['res_body']
            print(f"  Body size: {len(body)} bytes")
            print(f"  First 64 bytes: {body[:64].hex()}")
            
            # This is NOT gzip compressed - raw data
            # Find EURUSDm and dump its data
            # Search for EURUSDm in UTF-16LE
            eurusd_bytes = 'EURUSDm'.encode('utf-16-le')
            idx = body.find(eurusd_bytes)
            if idx >= 0:
                print(f"\n  EURUSDm found at offset {idx}")
                # Dump 500 bytes around it
                start = max(0, idx - 128)
                end = min(len(body), idx + 500)
                rec = body[start:end]
                
                print(f"\n  Raw hex from offset {start}:")
                for j in range(0, min(600, len(rec)), 16):
                    hex_str = ' '.join(f'{b:02x}' for b in rec[j:j+16])
                    print(f"    {start+j:4d}: {hex_str}")
                
                # Find doubles (volume, prices)
                print(f"\n  Doubles in EURUSDm record:")
                for j in range(0, len(rec)-8, 4):
                    try:
                        v = struct.unpack_from('<d', rec, j)[0]
                        actual_off = start + j
                        if 0 < v < 10000 and v != 0.0:
                            print(f"    @{actual_off}: {v:.6f}")
                        elif v > 100000:
                            print(f"    @{actual_off}: {v:.0f} (large)")
                    except: pass
                
                # Find uint32 (volume_min, volume_max, etc.)
                print(f"\n  Uint32 in EURUSDm record:")
                for j in range(0, len(rec)-4, 4):
                    v = struct.unpack_from('<I', rec, j)[0]
                    actual_off = start + j
                    if v in [5, 426, 1000000, 100000000]:
                        print(f"    @{actual_off}: {v}")
                    elif 1000 < v < 100000000:
                        print(f"    @{actual_off}: {v} (possible volume)")
        else:
            print("  No response")
        
        print("\n[+] Done")

if __name__ == '__main__':
    asyncio.run(main())
