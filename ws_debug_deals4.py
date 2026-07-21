#!/usr/bin/env python3
"""Brute force: dump first record bytes, search for any text"""
import asyncio, struct, time, random, ssl
import websockets
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_encrypt(key, pt):
    pl = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pl]*pl))
def aes_decrypt(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b==p for b in pt[-p:]) else pt
def pack_data(cid, ed):
    return struct.pack('<II', len(ed), 1) + ed
def build_cmd(cid, payload=b''):
    cmd = bytearray(4+len(payload))
    cmd[0]=random.randint(0,255); cmd[1]=random.randint(0,255)
    struct.pack_into('<H', cmd, 2, cid)
    if payload: cmd[4:4+len(payload)]=payload
    return bytes(cmd)
def parse_resp(data):
    if len(data)<5: return None
    return {'tag':struct.unpack('<H',data[0:2])[0],'cmd_id':struct.unpack('<H',data[2:4])[0],'res_code':data[4],'res_body':data[5:]}

def try_utf16(raw):
    try:
        s = raw.decode('utf-16-le')
        nul = s.find('\x00')
        return s[:nul] if nul >= 0 else s.rstrip('\x00')
    except:
        return None

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect('wss://15.206.31.153:443/terminal', ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin':'https://15.206.31.153:443'}) as ws:
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_cmd(0, bytes(64)))))
        r = parse_resp(aes_decrypt(STATIC_KEY, (await asyncio.wait_for(ws.recv(), timeout=10))[8:]))
        sk = r['res_body'][66:]
        h = bytearray(912)
        pw = 'Trade@123'.encode('utf-16-le')
        h[4:4+len(pw)] = pw
        struct.pack_into('<I', h, 476, len('15.206.31.153'))
        ip_enc = '15.206.31.153'.encode('utf-16-le')
        h[480:480+len(ip_enc)] = ip_enc
        struct.pack_into('<Q', h, 736, 463558919)
        await ws.send(pack_data(28, aes_encrypt(sk, build_cmd(28, bytes(h)))))
        r = parse_resp(aes_decrypt(sk, (await asyncio.wait_for(ws.recv(), timeout=10))[8:]))
        print("Login OK")

        # Get ALL deals
        await ws.send(pack_data(5, aes_encrypt(sk, build_cmd(5, struct.pack('<II', 0, 0)))))

        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=2)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_resp(aes_decrypt(sk, resp[8:]))
                    if r and r['cmd_id'] == 5:
                        buf = r['res_body']
                        deal_count = struct.unpack_from('<I', buf, 0)[0]
                        print(f"Deal count: {deal_count}, buf: {len(buf)} bytes")

                        # Dump first 700 bytes
                        print(f"\n=== First 700 bytes (deal_count + first ~1.5 records) ===")
                        for i in range(0, min(700, len(buf)), 32):
                            chunk = buf[i:i+32]
                            hex_part = ' '.join(f'{b:02x}' for b in chunk)
                            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                            print(f'  {i:5d}: {hex_part:<96s} {ascii_part}')

                        # Search for ALL readable UTF-16LE strings in first 800 bytes
                        print(f"\n=== Searching for readable UTF-16LE strings (min 4 chars) ===")
                        for off in range(0, min(800, len(buf)), 2):
                            for slen in [20, 24, 32, 36, 40, 48, 52, 60, 64]:
                                if off + slen <= len(buf):
                                    s = try_utf16(buf[off:off+slen])
                                    if s and len(s) >= 4 and s.replace(' ','').replace('-','').replace('.','').replace('_','').isalnum():
                                        print(f"  offset={off}: '{s}' (size={slen})")

                        # Also search for "Balance" as ASCII (not UTF-16LE)
                        bal_ascii = b'Balance'
                        idx = buf.find(bal_ascii)
                        if idx >= 0:
                            print(f"\nFound 'Balance' as ASCII at offset {idx}")
                        else:
                            print(f"\n'Balance' not found as ASCII either")

                        # Search for deposit amount 855.39 as float64
                        amt = struct.pack('<d', 855.39)
                        idx = buf.find(amt)
                        if idx >= 0:
                            print(f"Found 855.39 as float64 at offset {idx}")
                        else:
                            print("855.39 not found as float64")

                        # Try profit values near the deposit deal
                        # Let's also search for the number 459 as different int types
                        for val, fmt in [(459, '<q'), (459, '<Q'), (459, '<I'), (459, '<H')]:
                            idx = buf.find(struct.pack(fmt, val))
                            if idx >= 0:
                                print(f"Found {val} as {fmt} at offset {idx}")

                        break
            except asyncio.TimeoutError:
                pass

asyncio.run(main())
