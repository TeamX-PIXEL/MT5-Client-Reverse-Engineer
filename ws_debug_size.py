#!/usr/bin/env python3
"""Brute force find deal record size and order record size"""
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
                        total = len(buf)
                        print(f"Deal count: {deal_count}, buf: {total} bytes")

                        # Brute force: try every deal_size
                        # After deal records: [order_count:4][orders...]
                        # order_count * order_size + 4 = remaining bytes
                        remaining_area = total - 4  # minus deal_count
                        print(f"\nRemaining area: {remaining_area} bytes")

                        for ds in range(100, 1001):
                            deal_area = deal_count * ds
                            if deal_area > remaining_area:
                                break
                            remaining = remaining_area - deal_area
                            if remaining < 4:
                                continue
                            order_count = struct.unpack_from('<I', buf, 4 + deal_area)[0]
                            if order_count < 0 or order_count > 10000:
                                continue
                            order_area = remaining - 4
                            if order_count == 0 and order_area == 0:
                                print(f"  MATCH: deal_size={ds}, order_count=0, perfect fit!")
                                continue
                            if order_count > 0 and order_area > 0 and order_area % order_count == 0:
                                order_size = order_area // order_count
                                if 100 <= order_size <= 1000:
                                    print(f"  MATCH: deal_size={ds}, order_count={order_count}, order_size={order_size}")
                                    # Verify: first order record should have some valid data
                                    order_start = 4 + deal_area + 4
                                    if order_start + order_size <= total:
                                        # Try to read first order's trade_order (int64 at offset 0)
                                        v = struct.unpack_from('<q', buf, order_start)[0]
                                        if 1 <= v <= 1000000000:
                                            print(f"    First order trade_order={v}")

                        # Also try: maybe there's no order section at all
                        # Or maybe it's just [deal_count][deals...]
                        for ds in range(100, 1001):
                            deal_area = deal_count * ds
                            if deal_area == remaining_area:
                                print(f"  EXACT (no orders): deal_size={ds}")

                        break
            except asyncio.TimeoutError:
                pass

asyncio.run(main())
