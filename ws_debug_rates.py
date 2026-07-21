#!/usr/bin/env python3
"""
Test cmd_id=11 (GET_RATES) to get current price,
then place proper pending order.
"""
import asyncio, struct, time, random, ssl, zlib, datetime
import websockets
from Crypto.Cipher import AES

WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER_IP = "15.206.31.153"
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_encrypt(key, pt):
    pad_len = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad_len] * pad_len))
def aes_decrypt(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt
def pack_data(cmd_id, enc):
    return struct.pack('<II', len(enc), 1) + enc
def build_command(cmd_id, payload=b''):
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0, 255)
    cmd[1] = random.randint(0, 255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload: cmd[4:4+len(payload)] = payload
    return bytes(cmd)
def parse_response(data):
    if len(data) < 5: return None
    return {'tag': struct.unpack('<H', data[0:2])[0], 'cmd_id': struct.unpack('<H', data[2:4])[0],
            'res_code': data[4], 'res_body': data[5:]}
def build_login_payload(login_id, password, url):
    h = bytearray(912)
    pw = password.encode('utf-16-le')
    h[4:4+len(pw)] = pw
    struct.pack_into('<I', h, 476, len(url))
    ip = url.encode('utf-16-le')
    h[480:480+len(ip)] = ip
    struct.pack_into('<Q', h, 736, login_id)
    return bytes(h)

TYPE_SIZES = {1:1, 2:2, 3:4, 4:1, 5:2, 6:4, 7:4, 8:8, 17:8, 18:8}
def series_size(schema):
    size = 0
    for f in schema:
        pt = f['propType']
        if pt in TYPE_SIZES: size += TYPE_SIZES[pt]
        elif pt in (11, 12): size += f.get('propLength', 0)
    return size
def parse_series(data, schema, offset=0):
    vals = []
    for field in schema:
        pt = field['propType']
        pl = field.get('propLength', 0)
        if offset >= len(data): vals.append(None); continue
        if pt in TYPE_SIZES:
            sz = TYPE_SIZES[pt]
            if offset + sz > len(data): vals.append(None); offset = len(data); continue
            if pt == 1: vals.append(struct.unpack_from('<b', data, offset)[0])
            elif pt == 2: vals.append(struct.unpack_from('<h', data, offset)[0])
            elif pt == 3: vals.append(struct.unpack_from('<i', data, offset)[0])
            elif pt == 4: vals.append(data[offset])
            elif pt == 5: vals.append(struct.unpack_from('<H', data, offset)[0])
            elif pt == 6: vals.append(struct.unpack_from('<I', data, offset)[0])
            elif pt == 7: vals.append(struct.unpack_from('<f', data, offset)[0])
            elif pt == 8: vals.append(struct.unpack_from('<d', data, offset)[0])
            elif pt == 17: vals.append(struct.unpack_from('<q', data, offset)[0])
            elif pt == 18: vals.append(struct.unpack_from('<Q', data, offset)[0])
            else: vals.append(None)
            offset += sz
        elif pt in (11, 12):
            if offset + pl > len(data): vals.append(None); offset = len(data)
            else:
                raw = data[offset:offset+pl]
                if pt == 11:
                    try:
                        s = raw.decode('utf-16-le')
                        nul = s.find('\x00')
                        if nul >= 0: s = s[:nul]
                        vals.append(s)
                    except: vals.append(raw.hex())
                else: vals.append(raw)
                offset += pl
        else: vals.append(None)
    return vals, offset

MH_SCHEMA = [
    {'propType':11,'propLength':64},{'propType':11,'propLength':128},
    {'propType':6},{'propType':6},{'propType':11,'propLength':256},
    {'propType':6},{'propType':11,'propLength':64},{'propType':5},
]
MH_SIZE = series_size(MH_SCHEMA)

FL_SCHEMA = [
    {'propType':4},{'propType':3},{'propType':3},{'propType':8},{'propType':8},
    {'propType':11,'propLength':64},{'propType':6},{'propType':6},
    {'propType':11,'propLength':256},{'propType':5},{'propType':11,'propLength':128},
    {'propType':11,'propLength':256},{'propType':3},{'propType':1},
    {'propType':6},{'propType':6},{'propType':8},{'propType':8},
]

OP_SCHEMA = [
    {'propType':6},{'propType':6},{'propType':11,'propLength':64},
    {'propType':18},{'propType':6},{'propType':18},
    {'propType':6},{'propType':6},{'propType':6},{'propType':6},
    {'propType':6},{'propType':8},{'propType':8},{'propType':8},
    {'propType':8},{'propType':6},{'propType':8},{'propType':8},
    {'propType':11,'propLength':64},{'propType':18},{'propType':18},{'propType':6},
]
OP_SIZE = series_size(OP_SCHEMA)

POS_SCHEMA = [
    {'propType':17},{'propType':17},{'propType':6},{'propType':6},
    {'propType':11,'propLength':64},{'propType':6},
    {'propType':8},{'propType':8},{'propType':8},{'propType':8},
    {'propType':18},{'propType':8},{'propType':8},{'propType':8},
    {'propType':8},{'propType':8},{'propType':17},{'propType':17},
    {'propType':11,'propLength':64},{'propType':8},
    {'propType':6},{'propType':6},{'propType':6},
    {'propType':11,'propLength':64},{'propType':3},{'propType':3},
]
POS_SIZE = series_size(POS_SCHEMA)

DEAL_SCHEMA = [
    {'propType':17},{'propType':11,'propLength':64},{'propType':17},
    {'propType':6},{'propType':6},{'propType':11,'propLength':64},
    {'propType':6},{'propType':6},{'propType':8},{'propType':8},
    {'propType':8},{'propType':8},{'propType':18},{'propType':8},
    {'propType':8},{'propType':8},{'propType':8},{'propType':8},
    {'propType':17},{'propType':17},{'propType':11,'propLength':64},
    {'propType':8},{'propType':6},{'propType':6},{'propType':6},
    {'propType':3},{'propType':3},{'propType':8},
]
DEAL_SIZE = series_size(DEAL_SCHEMA)

ORDER_SCHEMA = [
    {'propType':17},{'propType':11,'propLength':64},{'propType':11,'propLength':64},
    {'propType':6},{'propType':6},{'propType':6},{'propType':6},
    {'propType':6},{'propType':6},{'propType':6},{'propType':8},
    {'propType':8},{'propType':8},{'propType':8},{'propType':8},
    {'propType':17},{'propType':17},{'propType':6},{'propType':17},
    {'propType':17},{'propType':11,'propLength':64},{'propType':8},
    {'propType':6},{'propType':6},{'propType':8},{'propType':8},
    {'propType':8},{'propType':6},{'propType':3},{'propType':3},
]
ORDER_SIZE = series_size(ORDER_SCHEMA)

async def send_cmd(ws, sk, cmd_id, payload=b''):
    await ws.send(pack_data(cmd_id, aes_encrypt(sk, build_command(cmd_id, payload))))

async def send_and_wait(ws, sk, cmd_id, payload, expected_cmd, timeout=5):
    await send_cmd(ws, sk, cmd_id, payload)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(resp, bytes) and len(resp) > 8:
                r = parse_response(aes_decrypt(sk, resp[8:]))
                if r and r['cmd_id'] == expected_cmd: return r
        except asyncio.TimeoutError: break
    return None

async def collect_all(ws, sk, seconds):
    results = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=0.3)
            if isinstance(resp, bytes) and len(resp) > 8:
                r = parse_response(aes_decrypt(sk, resp[8:]))
                if r: results.append(r)
        except asyncio.TimeoutError: pass
    return results

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print("[*] Connecting...")
    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        print("[+] Connected!")

        # Auth + Login
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        sk = resp['res_body'][66:]
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(sk, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
        print("[+] Auth+Login OK")

        # Account
        r = await send_and_wait(ws, sk, 3, b'', 3)
        if r:
            acct, _ = parse_series(r['res_body'], FL_SCHEMA, 0)
            print(f"\n=== ACCOUNT: balance={acct[3]:.2f} equity={acct[4]:.2f} ===")

        # Symbols
        await send_cmd(ws, sk, 34)
        r = await send_and_wait(ws, sk, 34, b'', 34, timeout=10)
        sym_map = {}
        if r:
            try: decomp = zlib.decompress(r['res_body'][4:])
            except: decomp = zlib.decompress(r['res_body'][4:], -15)
            count = struct.unpack_from('<I', decomp, 0)[0]
            off = 4
            for _ in range(count):
                if off + MH_SIZE > len(decomp): break
                vals, off = parse_series(decomp, MH_SCHEMA, off)
                sym_map[vals[0]] = {'id': vals[3], 'digits': vals[2]}

        target = 'EURUSDm'
        sym_id = sym_map[target]['id']
        digits = sym_map[target]['digits']
        print(f"[+] {target}: id={sym_id} digits={digits}")

        # ===== TEST cmd_id=11 (GET_RATES) =====
        print(f"\n{'='*60}")
        print("TEST: cmd_id=11 (GET_RATES) for EURUSDm M1")
        print(f"{'='*60}")

        # Build cmd_id=11 payload per JS:
        # [[11, symbol, 64], [5, timeframe], [3, from_ts], [3, to_ts]]
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - 3600000  # 1 hour ago
        to_ms = now_ms

        # Serialize: symbol(UTF-16LE,64B) + timeframe(uint16) + from(int32) + to(int32)
        rates_payload = bytearray(64 + 2 + 4 + 4)
        sym_bytes = target.encode('utf-16-le')
        rates_payload[0:0+len(sym_bytes)] = sym_bytes
        struct.pack_into('<H', rates_payload, 64, 1)  # M1 timeframe
        struct.pack_into('<i', rates_payload, 66, from_ms // 1000)  # from (unix seconds)
        struct.pack_into('<i', rates_payload, 70, to_ms // 1000)  # to (unix seconds)

        print(f"  Payload: {len(rates_payload)}B")
        print(f"  from={datetime.datetime.utcfromtimestamp(from_ms/1000).strftime('%H:%M:%S')} to={datetime.datetime.utcfromtimestamp(to_ms/1000).strftime('%H:%M:%S')}")

        r = await send_and_wait(ws, sk, 11, bytes(rates_payload), 11, timeout=10)
        if r:
            body = r['res_body']
            print(f"  Response: {len(body)}B")
            print(f"  Hex: {body[:60].hex()}")

            # Parse candle data
            # Each candle: open_time(i32), open(f64), high(f64), low(f64), close(f64), volume(i64), ...
            if len(body) >= 4:
                candle_count = struct.unpack_from('<I', body, 0)[0]
                print(f"  Candle count: {candle_count}")
                off = 4
                candle_size = 4 + 8*4 + 8 + 4  # time + OHLC + volume + spread
                for i in range(min(candle_count, 5)):
                    if off + 44 > len(body): break
                    ts = struct.unpack_from('<i', body, off)[0]
                    o = struct.unpack_from('<d', body, off+4)[0]
                    h = struct.unpack_from('<d', body, off+12)[0]
                    l = struct.unpack_from('<d', body, off+20)[0]
                    c = struct.unpack_from('<d', body, off+28)[0]
                    vol = struct.unpack_from('<q', body, off+36)[0]
                    print(f"  [{i}] time={datetime.datetime.utcfromtimestamp(ts).strftime('%H:%M:%S')} O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f} vol={vol}")
                    off += 44
        else:
            print("  No response for cmd_id=11!")

        # Try cmd_id=11 with different timeframe
        print(f"\n  Trying H1 timeframe...")
        rates_payload2 = bytearray(64 + 2 + 4 + 4)
        rates_payload2[0:0+len(sym_bytes)] = sym_bytes
        struct.pack_into('<H', rates_payload2, 64, 16385)  # H1 timeframe
        struct.pack_into('<i', rates_payload2, 66, from_ms // 1000)
        struct.pack_into('<i', rates_payload2, 70, to_ms // 1000)

        r = await send_and_wait(ws, sk, 11, bytes(rates_payload2), 11, timeout=10)
        if r:
            body = r['res_body']
            print(f"  Response: {len(body)}B")
            if len(body) >= 4:
                candle_count = struct.unpack_from('<I', body, 0)[0]
                print(f"  Candle count: {candle_count}")
                off = 4
                for i in range(min(candle_count, 3)):
                    if off + 44 > len(body): break
                    ts = struct.unpack_from('<i', body, off)[0]
                    o = struct.unpack_from('<d', body, off+4)[0]
                    h = struct.unpack_from('<d', body, off+12)[0]
                    l = struct.unpack_from('<d', body, off+20)[0]
                    c = struct.unpack_from('<d', body, off+28)[0]
                    vol = struct.unpack_from('<q', body, off+36)[0]
                    print(f"  [{i}] time={datetime.datetime.utcfromtimestamp(ts).strftime('%H:%M:%S')} O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f} vol={vol}")
                    off += 44

            # Use last close as current price
            if len(body) >= 4:
                candle_count = struct.unpack_from('<I', body, 0)[0]
                if candle_count > 0:
                    last_candle_off = 4 + (candle_count - 1) * 44
                    if last_candle_off + 36 <= len(body):
                        last_close = struct.unpack_from('<d', body, last_candle_off + 28)[0]
                        last_high = struct.unpack_from('<d', body, last_candle_off + 12)[0]
                        last_low = struct.unpack_from('<d', body, last_candle_off + 20)[0]
                        print(f"\n  Current price (last close): {last_close:.5f}")
                        print(f"  Last candle H={last_high:.5f} L={last_low:.5f}")
        else:
            print("  No response!")

        print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
