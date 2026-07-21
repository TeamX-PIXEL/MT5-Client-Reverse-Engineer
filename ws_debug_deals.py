#!/usr/bin/env python3
"""Debug deal schema - dump raw bytes of deposit deal and first trading deal"""
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
    return {'tag':struct.unpack('<H',data[0:2])[0],
            'cmd_id':struct.unpack('<H',data[2:4])[0],
            'res_code':data[4],'res_body':data[5:]}

def try_decode_utf16(raw_bytes):
    try:
        s = raw_bytes.decode('utf-16-le')
        nul = s.find('\x00')
        return s[:nul] if nul >= 0 else s.rstrip('\x00')
    except:
        return None

def dump_hex_ascii(data, offset=0, length=None):
    """Print hex + ASCII dump of bytes"""
    if length is None:
        length = len(data)
    for i in range(0, min(length, len(data)), 16):
        chunk = data[i:i+16]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'  {offset+i:4d}: {hex_part:<48s} {ascii_part}')

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect('wss://15.206.31.153:443/terminal', ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin':'https://15.206.31.153:443'}) as ws:
        # Auth
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_cmd(0, bytes(64)))))
        r = parse_resp(aes_decrypt(STATIC_KEY, (await asyncio.wait_for(ws.recv(), timeout=10))[8:]))
        sk = r['res_body'][66:]

        # Login
        h = bytearray(912)
        pw = 'Trade@123'.encode('utf-16-le')
        h[4:4+len(pw)] = pw
        struct.pack_into('<I', h, 476, len('15.206.31.153'))
        ip_enc = '15.206.31.153'.encode('utf-16-le')
        h[480:480+len(ip_enc)] = ip_enc
        struct.pack_into('<Q', h, 736, 463558919)
        await ws.send(pack_data(28, aes_encrypt(sk, build_cmd(28, bytes(h)))))
        r = parse_resp(aes_decrypt(sk, (await asyncio.wait_for(ws.recv(), timeout=10))[8:]))
        print(f"Login OK, account_id={struct.unpack('<Q', r['res_body'][:8])[0]}")

        # Get deal history
        await ws.send(pack_data(5, aes_encrypt(sk, build_cmd(5, struct.pack('<II', 0, 0)))))

        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=2)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_resp(aes_decrypt(sk, resp[8:]))
                    if r and r['cmd_id'] == 5:
                        buf = r['res_body']
                        deal_count = struct.unpack_from('<I', buf, 0)[0]
                        print(f"\n=== Deal count: {deal_count}, total buf size: {len(buf)} bytes ===")

                        # Dump raw hex of the deal count + first 3 deals
                        # deal_count=4B, then each deal is some size
                        # We need to figure out the actual deal size

                        # First, scan for the second uint32 (order_count) to find actual deal size
                        # The format is: [deal_count:4][deals...][order_count:4][orders...]
                        # If all records are equal size, we can calculate: deal_size = (total_deals_area / deal_count)
                        # But first let's just look at the raw bytes

                        print(f"\n=== First 100 bytes of response body (after deal_count) ===")
                        dump_hex_ascii(buf[:100], offset=0)

                        # The deposit deal should have "Balance" in UTF-16LE somewhere
                        # Search for "Balance" in UTF-16LE in first 1000 bytes
                        balance_utf16 = 'Balance'.encode('utf-16-le')
                        idx = buf.find(balance_utf16)
                        if idx >= 0:
                            print(f"\n=== Found 'Balance' at offset {idx} in response body ===")
                            # Dump context around it
                            start = max(0, idx - 64)
                            dump_hex_ascii(buf[start:idx+128], offset=start)

                        # Try different deal sizes - scan for "Balance" at regular intervals
                        # to find the actual record size
                        print(f"\n=== Searching for record size by finding 'Balance' pattern ===")
                        # The deposit deal has "Balance" at field[5] (trade_symbol)
                        # In our schema, trade_symbol is at offset 8+64+8+4+4=88 from record start
                        # So if "Balance" is at buf[idx], then record_start = idx - 88
                        # But that assumes our schema is right... which it's not.
                        # Let's just search for "Balance" and check nearby offsets
                        for test_size in range(200, 500):
                            for start in range(4, len(buf), test_size):
                                if start + test_size > len(buf):
                                    break
                                record = buf[start:start+test_size]
                                bal_pos = record.find(balance_utf16)
                                if bal_pos >= 0 and bal_pos < 200:
                                    # Check if this is the first record
                                    if start <= 4 + test_size:
                                        print(f"  Candidate: size={test_size}, 'Balance' at offset {bal_pos} within record (record starts at {start})")

                        # Now let's try a smarter approach: scan every possible record start
                        # for a valid structure
                        print(f"\n=== Scanning for records containing valid deal values ===")
                        # A deal ticket should be a reasonable number (1-10000000)
                        # A deal_id should be "0" + digits or just digits
                        for rec_start in range(4, min(2000, len(buf)), 1):
                            if rec_start + 20 > len(buf):
                                break
                            # Try reading as int64
                            val = struct.unpack_from('<q', buf, rec_start)[0]
                            if 1 <= val <= 50000000:
                                # Check next few fields
                                if rec_start + 72 + 8 <= len(buf):
                                    val2 = struct.unpack_from('<q', buf, rec_start + 72)[0]
                                    if 1 <= val2 <= 50000000:
                                        # Try to read a UTF-16 string at offset 8
                                        s = try_decode_utf16(buf[rec_start+8:rec_start+72])
                                        if s and len(s) > 0 and all(c.isdigit() for c in s):
                                            # Check for "Balance" or "XAU" etc at various offsets
                                            for sym_off in [88, 152, 80, 96, 100, 104, 112, 120, 128, 136, 144, 148]:
                                                if sym_off + 128 <= len(buf) - rec_start:
                                                    sym = try_decode_utf16(buf[rec_start+sym_off:rec_start+sym_off+128])
                                                    if sym and len(sym) >= 3 and sym[0].isalpha():
                                                        print(f"  rec_start={rec_start}: deal={val}, deal_id='{s}', possible_symbol='{sym}' at field_offset={sym_off}")
                                                        break
                                                break

                        break
            except asyncio.TimeoutError:
                pass

asyncio.run(main())
