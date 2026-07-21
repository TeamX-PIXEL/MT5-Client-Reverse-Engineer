#!/usr/bin/env python3
"""Raw search for deal structure"""
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

def dump_hex(data, base_off=0):
    for i in range(0, len(data), 32):
        chunk = data[i:i+32]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'  {base_off+i:5d}: {hex_part:<96s} {ascii_part}')

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect('wss://15.206.31.153:443/terminal', ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin':'https://15.206.31.153:443'}) as ws:
        # Auth + Login
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
        print(f"Login OK")

        # Get deal history - just first 10 deals
        await ws.send(pack_data(5, aes_encrypt(sk, build_cmd(5, struct.pack('<II', 0, 10)))))

        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=2)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_resp(aes_decrypt(sk, resp[8:]))
                    if r and r['cmd_id'] == 5:
                        buf = r['res_body']
                        deal_count = struct.unpack_from('<I', buf, 0)[0]
                        print(f"\nDeal count: {deal_count}, buf size: {len(buf)} bytes")

                        # Find ALL occurrences of "Balance" in UTF-16LE
                        balance_utf16 = 'Balance'.encode('utf-16-le')
                        print(f"\nSearching for 'Balance' in UTF-16LE ({balance_utf16.hex()}):")
                        idx = 0
                        while True:
                            idx = buf.find(balance_utf16, idx)
                            if idx < 0:
                                break
                            print(f"  Found at offset {idx}")
                            # Show context 20 bytes before and after
                            start = max(0, idx - 20)
                            end = min(len(buf), idx + 40)
                            print(f"  Context [{start}:{end}]:")
                            dump_hex(buf[start:end], base_off=start)
                            idx += 1

                        # Find "XAU" occurrences
                        xau_utf16 = 'XAU'.encode('utf-16-le')
                        print(f"\nSearching for 'XAU' in UTF-16LE:")
                        idx = 0
                        xau_count = 0
                        while idx < len(buf) - 128:
                            idx = buf.find(xau_utf16, idx)
                            if idx < 0:
                                break
                            if idx % 2 == 0:  # Must be 2-byte aligned
                                xau_count += 1
                                if xau_count <= 5:
                                    s = try_utf16(buf[idx:idx+128])
                                    print(f"  Found at offset {idx}: '{s}'")
                                    start = max(0, idx - 40)
                                    dump_hex(buf[start:idx+64], base_off=start)
                            idx += 2
                        print(f"  Total XAU occurrences (2-byte aligned): {xau_count}")

                        # Try: maybe deal_id is NOT 64 bytes, maybe it's smaller
                        # Or maybe propType 11 with propLength 64 means 64 bytes total
                        # Let's scan the first record assuming different string sizes
                        print(f"\n=== Probing first record with different field sizes ===")

                        # The first field should be deal ticket (int64 = 8 bytes)
                        deal_ticket = struct.unpack_from('<q', buf, 4)[0]
                        print(f"Field 0 (int64 @ offset 4): {deal_ticket}")

                        # Field 1 is deal_id (UTF-16LE string) - try sizes
                        for str_size in [32, 36, 40, 48, 52, 56, 60, 64, 68, 72, 80, 96, 128]:
                            candidate_off = 4 + 8 + str_size  # skip deal + deal_id
                            if candidate_off + 8 <= len(buf):
                                val = struct.unpack_from('<q', buf, candidate_off)[0]
                                if 1 <= val <= 100000000:
                                    print(f"  If deal_id is {str_size}B: trade_order @ +{candidate_off} = {val}")

                        # Let's try a completely different approach: just dump the first 500 bytes
                        print(f"\n=== First 500 bytes of response body ===")
                        dump_hex(buf[:500], base_off=0)

                        # Also try to find where order_count starts
                        # After all deals, there should be a uint32 order_count
                        # deal_count * deal_size + 4 = offset of order_count
                        # Let's search for plausible order_count values
                        print(f"\n=== Searching for order_count boundary ===")
                        for trial_size in range(200, 500):
                            offset = 4 + deal_count * trial_size
                            if offset + 4 <= len(buf):
                                order_count = struct.unpack_from('<I', buf, offset)[0]
                                if 0 <= order_count <= 10000:
                                    # Check next 4 bytes too
                                    if offset + 8 <= len(buf):
                                        next_val = struct.unpack_from('<I', buf, offset+4)[0]
                                        if 0 <= next_val <= 10000:
                                            print(f"  trial_size={trial_size}: order_count={order_count}, next_u32={next_val}, total={offset+4+order_count*trial_size}")
                                            if order_count > 0 and order_count * trial_size + offset + 4 == len(buf):
                                                print(f"    *** PERFECT MATCH ***")

                        break
            except asyncio.TimeoutError:
                pass

asyncio.run(main())
