#!/usr/bin/env python3
"""
Test cmd_id=11 (GET_RATES) candle data for ALL timeframes.
Format: [timestamp:i32][open:f64][high:f64][low:f64][close:f64][volume:i64][spread:i32] = 48 bytes/candle
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

# MT5 timeframe constants
TIMEFRAMES = {
    'M1':   1,
    'M5':   5,
    'M15':  15,
    'M30':  30,
    'H1':   16385,
    'H4':   16388,
    'D1':   16408,
    'W1':   32769,
    'MN1':  49153,
}

CANDLE_SIZE = 48  # i32 time + 4x f64 OHLC + i64 volume + i32 spread

def parse_candles(body):
    """Parse candle response: raw stream of 48-byte candles"""
    if len(body) < CANDLE_SIZE:
        return []
    num_candles = len(body) // CANDLE_SIZE
    candles = []
    off = 0
    for i in range(num_candles):
        if off + CANDLE_SIZE > len(body): break
        ts = struct.unpack_from('<i', body, off)[0]
        o = struct.unpack_from('<d', body, off+4)[0]
        h = struct.unpack_from('<d', body, off+12)[0]
        l = struct.unpack_from('<d', body, off+20)[0]
        c = struct.unpack_from('<d', body, off+28)[0]
        vol = struct.unpack_from('<q', body, off+36)[0]
        spread = struct.unpack_from('<i', body, off+44)[0]
        try:
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        except (OSError, ValueError):
            dt = None
        candles.append({
            'time': dt, 'timestamp': ts,
            'open': o, 'high': h, 'low': l, 'close': c,
            'tick_volume': vol, 'spread': spread
        })
        off += CANDLE_SIZE
    return candles

async def request_candles(ws, sk, sym_name, tf_name, tf_val, from_sec, to_sec):
    """Request candles using cmd_id=11"""
    pl = bytearray(64 + 2 + 4 + 4)
    sym_bytes = sym_name.encode('utf-16-le')
    pl[0:0+len(sym_bytes)] = sym_bytes
    struct.pack_into('<H', pl, 64, tf_val)
    struct.pack_into('<i', pl, 66, from_sec)
    struct.pack_into('<i', pl, 70, to_sec)

    await sc(ws, sk, 11, bytes(pl))
    r = await recv_cmd(ws, sk, 11, timeout=10)
    if not r:
        return None, "No response"
    body = r['res_body']
    if len(body) < CANDLE_SIZE:
        return None, f"Too small: {len(body)}B"
    return parse_candles(body), f"{len(body)}B"

def validate_candles(candles, tf_name, expected_digits):
    """Validate candle data: timestamps, OHLC relationships, price ranges"""
    errors = []
    if not candles:
        return ["Empty candle list"]

    for i, c in enumerate(candles):
        # Timestamp check
        if c['time'] is None:
            errors.append(f"[{i}] Invalid timestamp: {c['timestamp']}")
            continue

        # OHLC basic checks
        o, h, l, close = c['open'], c['high'], c['low'], c['close']

        if o <= 0 or h <= 0 or l <= 0 or close <= 0:
            errors.append(f"[{i}] Non-positive price O={o} H={h} L={l} C={close}")
            continue

        if h < l:
            errors.append(f"[{i}] High < Low: H={h:.5f} L={l:.5f}")

        if h < o or h < close:
            errors.append(f"[{i}] High < Open/Close: H={h:.5f} O={o:.5f} C={close:.5f}")

        if l > o or l > close:
            errors.append(f"[{i}] Low > Open/Close: L={l:.5f} O={o:.5f} C={close:.5f}")

        # Timestamp ordering
        if i > 0 and candles[i-1]['time'] and c['time']:
            if c['time'] <= candles[i-1]['time']:
                errors.append(f"[{i}] Timestamp not increasing: {c['time']} <= {candles[i-1]['time']}")

    return errors

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print("[*] Connecting...")
    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        print("[+] Connected!")

        # Auth + Login
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = pr(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Auth+Login OK")

        # Symbols
        await sc(ws, sk, 34)
        r = await recv_cmd(ws, sk, 34, timeout=10)
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
        if target not in sym_map:
            print(f"[-] {target} not found!"); return
        digits = sym_map[target]['digits']
        print(f"[+] {target}: id={sym_map[target]['id']} digits={digits}")

        now = int(time.time())
        results = {}

        print(f"\n{'='*70}")
        print(f"CANDLE DATA TEST - {target} - ALL TIMEFRAMES")
        print(f"{'='*70}")

        for tf_name, tf_val in TIMEFRAMES.items():
            # Adjust time range based on timeframe
            if tf_val <= 30:        # Minutes (M1-M30): last 2 hours
                from_sec = now - 7200
                range_str = "2H"
            elif tf_val < 16408:    # Hours (H1-H4): last 48 hours
                from_sec = now - 172800
                range_str = "48H"
            elif tf_val < 32769:    # Daily (D1): last 60 days
                from_sec = now - 5184000
                range_str = "60D"
            elif tf_val < 49153:    # Weekly (W1): last 6 months
                from_sec = now - 15552000
                range_str = "6M"
            else:                   # Monthly (MN1): last 2 years
                from_sec = now - 63072000
                range_str = "2Y"

            to_sec = now

            candles, fmt_info = await request_candles(ws, sk, target, tf_name, tf_val, from_sec, to_sec)

            if candles is None:
                print(f"  {tf_name:4s}: FAIL - {fmt_info}")
                results[tf_name] = {'status': 'FAIL', 'error': fmt_info}
                continue

            errors = validate_candles(candles, tf_name, digits)

            if errors:
                print(f"  {tf_name:4s}: WARN - {len(candles)} candles, {len(errors)} errors")
                for e in errors[:3]:
                    print(f"         {e}")
                results[tf_name] = {'status': 'WARN', 'count': len(candles), 'errors': len(errors)}
            else:
                # Show first and last candle
                first = candles[0]
                last = candles[-1]
                first_t = first['time'].strftime('%Y-%m-%d %H:%M') if first['time'] else 'N/A'
                last_t = last['time'].strftime('%Y-%m-%d %H:%M') if last['time'] else 'N/A'
                print(f"  {tf_name:4s}: OK   - {len(candles):3d} candles | "
                      f"{first_t} -> {last_t} | "
                      f"Last: O={last['open']:.5f} H={last['high']:.5f} L={last['low']:.5f} C={last['close']:.5f} vol={last['tick_volume']}")
                results[tf_name] = {'status': 'OK', 'count': len(candles)}

        # Summary
        print(f"\n{'='*70}")
        print("SUMMARY")
        print(f"{'='*70}")
        ok_count = sum(1 for r in results.values() if r['status'] == 'OK')
        warn_count = sum(1 for r in results.values() if r['status'] == 'WARN')
        fail_count = sum(1 for r in results.values() if r['status'] == 'FAIL')

        for tf_name in TIMEFRAMES:
            r = results.get(tf_name, {'status': 'NOT_TESTED'})
            status = r['status']
            if status == 'OK':
                extra = f" ({r['count']} candles)"
            elif status == 'WARN':
                extra = f" ({r['count']} candles, {r['errors']} errors)"
            elif status == 'FAIL':
                extra = f" ({r.get('error', 'unknown')})"
            else:
                extra = ""
            icon = "PASS" if status == 'OK' else ("WARN" if status == 'WARN' else "FAIL")
            print(f"  [{icon}] {tf_name:4s}{extra}")

        print(f"\n  Total: {ok_count} OK, {warn_count} WARN, {fail_count} FAIL out of {len(results)} timeframes")

        if ok_count == len(TIMEFRAMES):
            print("\n  ALL TIMEFRAMES PASSED!")
        elif fail_count > 0:
            print(f"\n  {fail_count} TIMEFRAME(S) FAILED")
        else:
            print(f"\n  {warn_count} TIMEFRAME(S) WITH WARNINGS")

        print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
