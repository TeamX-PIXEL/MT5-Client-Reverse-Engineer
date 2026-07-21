#!/usr/bin/env python3
"""Raw search for deal structure - use to=0 for all deals"""
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
    for i in range(0, min(len(data), 512), 32):
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

        # Get ALL deals (from=0, to=0)
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

                        # Find "Balance" in UTF-16LE
                        balance_utf16 = 'Balance'.encode('utf-16-le')
                        bal_idx = buf.find(balance_utf16)
                        if bal_idx >= 0:
                            # Ensure 2-byte aligned
                            if bal_idx % 2 != 0:
                                bal_idx -= 1
                            print(f"\nFound 'Balance' at raw offset {bal_idx}")
                            # Dump 200 bytes around it
                            start = max(0, bal_idx - 160)
                            dump_hex(buf[start:bal_idx+80], base_off=start)

                            # This helps us determine the record size
                            # deal_count is at offset 0-3
                            # First deal starts at offset 4
                            # The "Balance" string is the trade_symbol field (field 5)
                            # We need to figure out the actual field layout

                            # Try to find the record start by searching backwards for a reasonable deal ticket
                            print(f"\nSearching backwards from 'Balance' for record start:")
                            for rec_start in range(bal_idx - 160, bal_idx - 40, -4):
                                if rec_start < 4:
                                    break
                                # Try reading first field as int64
                                v0 = struct.unpack_from('<q', buf, rec_start)[0]
                                if 1 <= v0 <= 100000000:
                                    # Try reading deal_id as UTF-16 at offset 8
                                    deal_id = try_utf16(buf[rec_start+8:rec_start+72])
                                    if deal_id and len(deal_id) > 0 and len(deal_id) <= 32:
                                        print(f"  Candidate rec_start={rec_start}: deal={v0}, deal_id='{deal_id}'")
                                        # Try various offsets for trade_symbol
                                        for sym_off in [72, 76, 80, 84, 88, 92, 96, 100, 104, 108, 112]:
                                            sym = try_utf16(buf[rec_start+sym_off:rec_start+sym_off+64])
                                            if sym and len(sym) >= 3:
                                                print(f"    symbol at field_offset={sym_off}: '{sym}'")

                        # Now find the order_count boundary
                        # Method: scan for where the buffer transitions from deal records to order_count
                        print(f"\n=== Finding record size via order_count boundary ===")
                        for trial_size in range(100, 600):
                            offset = 4 + deal_count * trial_size
                            if offset + 4 <= len(buf):
                                order_count = struct.unpack_from('<I', buf, offset)[0]
                                if 0 <= order_count <= 10000:
                                    remaining = len(buf) - offset - 4
                                    if order_count > 0 and remaining >= order_count * 4:
                                        # See if there's a valid order_count that uses remaining buffer
                                        if remaining % max(order_count, 1) == 0 or order_count == 0:
                                            order_size = remaining // max(order_count, 1)
                                            if order_size < 10 or order_count == 0:
                                                print(f"  trial={trial_size}: deal_area={deal_count*trial_size}, order_count={order_count}, remaining={remaining}")
                                                if trial_size == 368:
                                                    print(f"    *** 368 matches! ***")
                            # Also try: deal area + 4 + order_count(4) + orders = total
                            for trial_osize in [200, 250, 300, 336, 340, 344, 350, 368, 400, 464]:
                                offset2 = 4 + deal_count * trial_size
                                if offset2 + 8 <= len(buf):
                                    oc = struct.unpack_from('<I', buf, offset2)[0]
                                    if 0 <= oc <= 10000:
                                        expected = offset2 + 4 + oc * trial_osize
                                        if expected == len(buf):
                                            print(f"  EXACT MATCH: deal_size={trial_size}, order_size={trial_osize}, order_count={oc}")

                        break
            except asyncio.TimeoutError:
                pass

asyncio.run(main())
