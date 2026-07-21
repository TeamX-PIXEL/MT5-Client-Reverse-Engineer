#!/usr/bin/env python3
"""
Test candle range limits and datetime-based selection.
Handles connection limits gracefully.
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

CANDLE_SIZE = 48

def parse_candles(body):
    if len(body) < CANDLE_SIZE: return []
    num = len(body) // CANDLE_SIZE
    candles = []
    off = 0
    for _ in range(num):
        ts = struct.unpack_from('<i', body, off)[0]
        o = struct.unpack_from('<d', body, off+4)[0]
        h = struct.unpack_from('<d', body, off+12)[0]
        l = struct.unpack_from('<d', body, off+20)[0]
        c = struct.unpack_from('<d', body, off+28)[0]
        vol = struct.unpack_from('<q', body, off+36)[0]
        spread = struct.unpack_from('<i', body, off+44)[0]
        try:
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        except:
            dt = None
        candles.append({'time': dt, 'timestamp': ts, 'open': o, 'high': h, 'low': l, 'close': c, 'tick_volume': vol, 'spread': spread})
        off += CANDLE_SIZE
    return candles

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

class MT5Client:
    def __init__(self):
        self.ws = None
        self.sk = None
        self.connected = False

    async def connect(self):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self.ws = await websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
                additional_headers={'Origin': 'https://15.206.31.153:443'},
                max_size=10*1024*1024)  # 10MB limit
        # Auth
        await self.ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        self.sk = pr(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        # Login
        await self.ws.send(pack_data(28, aes_enc(self.sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        self.connected = True
        print("[+] Connected")

    async def sc(self, cid, pl=b''):
        await self.ws.send(pack_data(cid, aes_enc(self.sk, mk_cmd(cid, pl))))

    async def recv_cmd(self, target_cmd, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=2)
                r = pr(aes_dec(self.sk, raw[8:]))
                if r and r['cmd_id'] == target_cmd:
                    return r
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                return None
        return None

    async def get_candles(self, symbol, tf_val, from_sec, to_sec):
        pl = bytearray(64 + 2 + 4 + 4)
        sb = symbol.encode('utf-16-le')
        pl[0:0+len(sb)] = sb
        struct.pack_into('<H', pl, 64, tf_val)
        struct.pack_into('<i', pl, 66, from_sec)
        struct.pack_into('<i', pl, 70, to_sec)
        await self.sc(11, bytes(pl))
        r = await self.recv_cmd(11, timeout=15)
        if r and len(r['res_body']) >= CANDLE_SIZE:
            return parse_candles(r['res_body'])
        return None

    async def get_symbols(self):
        await self.sc(34)
        r = await self.recv_cmd(34, timeout=10)
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
        return sym_map

async def main():
    c = MT5Client()
    await c.connect()
    now = int(time.time())

    sym_map = await c.get_symbols()
    target = 'EURUSDm'
    print(f"[+] {target}: id={sym_map[target]['id']}")

    # ===== TEST 1: M15 count limits =====
    print(f"\n{'='*70}")
    print("TEST 1: M15 CANDLE COUNT vs TIME RANGE")
    print(f"{'='*70}")

    for days in [1, 3, 7, 14, 30, 60, 90, 120, 150, 180]:
        from_sec = now - days * 86400
        candles = await c.get_candles(target, 15, from_sec, now)
        if candles is not None:
            first_t = candles[0]['time'].strftime('%Y-%m-%d') if candles[0]['time'] else '?'
            last_t = candles[-1]['time'].strftime('%Y-%m-%d') if candles[-1]['time'] else '?'
            print(f"  {days:4d} days: {len(candles):6d} candles | {first_t} -> {last_t} | ~{len(candles)/days:.0f}/day")
        else:
            print(f"  {days:4d} days: FAILED (too large or timeout)")

    # ===== TEST 2: Max candles per timeframe =====
    print(f"\n{'='*70}")
    print("TEST 2: MAX CANDLES PER TIMEFRAME")
    print(f"{'='*70}")

    tf_tests = [
        ('M1', 1, 7), ('M5', 5, 30), ('M15', 15, 90), ('M30', 30, 180),
        ('H1', 16385, 365), ('H4', 16388, 730),
        ('D1', 16408, 1825), ('W1', 32769, 3650), ('MN1', 49153, 3650),
    ]

    for tf_name, tf_val, days in tf_tests:
        from_sec = now - days * 86400
        candles = await c.get_candles(target, tf_val, from_sec, now)
        if candles is not None:
            first_t = candles[0]['time'].strftime('%Y-%m-%d') if candles[0]['time'] else '?'
            last_t = candles[-1]['time'].strftime('%Y-%m-%d') if candles[-1]['time'] else '?'
            print(f"  {tf_name:4s}: {len(candles):6d} candles ({days:5d} days) | {first_t} -> {last_t}")
        else:
            print(f"  {tf_name:4s}: FAILED")

    # ===== TEST 3: Datetime-based selection =====
    print(f"\n{'='*70}")
    print("TEST 3: DATETIME-BASED RANGE SELECTION")
    print(f"{'='*70}")

    ranges = [
        ("M15: Jul 1-7", 15, datetime.datetime(2026,7,1, tzinfo=datetime.timezone.utc),
                             datetime.datetime(2026,7,7,23,59, tzinfo=datetime.timezone.utc)),
        ("H1: Jun 15-20", 16385, datetime.datetime(2026,6,15, tzinfo=datetime.timezone.utc),
                                datetime.datetime(2026,6,20,23,59, tzinfo=datetime.timezone.utc)),
        ("D1: Q2 2026", 16408, datetime.datetime(2026,4,1, tzinfo=datetime.timezone.utc),
                               datetime.datetime(2026,6,30,23,59, tzinfo=datetime.timezone.utc)),
        ("M1: specific hour", 1, datetime.datetime(2026,7,15,10,0, tzinfo=datetime.timezone.utc),
                                 datetime.datetime(2026,7,15,11,0, tzinfo=datetime.timezone.utc)),
    ]

    for label, tf_val, from_dt, to_dt in ranges:
        from_sec = int(from_dt.timestamp())
        to_sec = int(to_dt.timestamp())
        candles = await c.get_candles(target, tf_val, from_sec, to_sec)
        if candles is not None:
            first_t = candles[0]['time'].strftime('%Y-%m-%d %H:%M') if candles[0]['time'] else '?'
            last_t = candles[-1]['time'].strftime('%Y-%m-%d %H:%M') if candles[-1]['time'] else '?'
            print(f"  {label:25s}: {len(candles):5d} candles | {first_t} -> {last_t}")
        else:
            print(f"  {label:25s}: FAILED")

    # ===== TEST 4: Binary search for M15 max =====
    print(f"\n{'='*70}")
    print("TEST 4: M15 BINARY SEARCH FOR MAX CANDLES")
    print(f"{'='*70}")

    lo, hi = 100, 20000
    max_ok = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        # Each M15 candle = 900 seconds
        from_sec = now - mid * 900
        candles = await c.get_candles(target, 15, from_sec, now)
        if candles is not None and len(candles) > 0:
            max_ok = len(candles)
            lo = mid + 1
            print(f"  {mid:6d} candles: OK ({len(candles)} returned)")
        else:
            hi = mid - 1
            print(f"  {mid:6d} candles: FAIL")

    print(f"\n  MAX M15 candles in single request: ~{max_ok}")
    print(f"  (Each M15 = 900s, so {max_ok} candles = {max_ok*900/86400:.1f} days)")

    print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
