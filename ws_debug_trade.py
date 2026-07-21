#!/usr/bin/env python3
"""
Full trade test with correct price from cmd_id=11.
Tests: BUY STOP (above market), BUY LIMIT (below market), and MARKET order.
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

OP_SCHEMA = [
    {'propType':6},{'propType':6},{'propType':11,'propLength':64},
    {'propType':18},{'propType':6},{'propType':18},
    {'propType':6},{'propType':6},{'propType':6},{'propType':6},
    {'propType':6},{'propType':8},{'propType':8},{'propType':8},
    {'propType':8},{'propType':6},{'propType':8},{'propType':8},
    {'propType':11,'propLength':64},{'propType':18},{'propType':18},{'propType':6},
]
OP_SIZE = series_size(OP_SCHEMA)

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

def get_current_price(ws, sk, sym_id, sym_name, digits):
    """Get current price via cmd_id=11 (M1 candles)."""
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - 3600000
    
    rates_payload = bytearray(74)
    sym_bytes = sym_name.encode('utf-16-le')
    rates_payload[0:0+len(sym_bytes)] = sym_bytes
    struct.pack_into('<H', rates_payload, 64, 1)  # M1
    struct.pack_into('<i', rates_payload, 66, from_ms // 1000)
    struct.pack_into('<i', rates_payload, 70, now_ms // 1000)
    
    r = send_and_wait(ws, sk, 11, bytes(rates_payload), 11, timeout=10)
    # Note: this is sync, need to be called from async context
    return r

def place_order(sym_name, sym_id, digits, trade_action, trade_type, price, sl=0, tp=0, vol=1000, comment=b''):
    """Build Op payload for a trade order."""
    op = bytearray(OP_SIZE)
    struct.pack_into('<I', op, 0, 0)                    # action_id
    struct.pack_into('<I', op, 4, trade_action)          # trade_action
    sym_bytes = sym_name.encode('utf-16-le')
    op[8:8+len(sym_bytes)] = sym_bytes
    struct.pack_into('<Q', op, 72, vol)                  # volume
    struct.pack_into('<I', op, 80, digits)               # digits
    struct.pack_into('<Q', op, 84, 0)                    # trade_order = 0
    struct.pack_into('<I', op, 92, trade_type)           # trade_type
    struct.pack_into('<I', op, 96, 0)                    # type_filling = FOK
    struct.pack_into('<I', op, 100, 0)                   # type_time = GTC
    struct.pack_into('<I', op, 104, 2)                   # type_flags = 2
    struct.pack_into('<I', op, 108, 0)                   # type_reason = 0
    struct.pack_into('<d', op, 112, price)               # price_order
    struct.pack_into('<d', op, 120, 0)                   # price_trigger
    struct.pack_into('<d', op, 128, sl)                  # sl
    struct.pack_into('<d', op, 136, tp)                  # tp
    struct.pack_into('<I', op, 144, 0)                   # price_deviation
    struct.pack_into('<d', op, 148, 0)                   # price_top
    struct.pack_into('<d', op, 156, 0)                   # price_bottom
    if comment:
        op[168:168+len(comment)] = comment
    struct.pack_into('<Q', op, 228, 0)                   # trade_position
    struct.pack_into('<Q', op, 236, 0)                   # position_by
    return bytes(op)

def parse_positions(buf):
    if len(buf) < 4: return [], []
    pos_count = struct.unpack_from('<I', buf, 0)[0]
    off = 4
    positions = []
    for i in range(pos_count):
        if off + POS_SIZE > len(buf): break
        vals, off = parse_series(buf, POS_SCHEMA, off)
        positions.append(vals)
    if off + 4 > len(buf): return positions, []
    order_count = struct.unpack_from('<I', buf, off)[0]
    off += 4
    orders = []
    for i in range(order_count):
        if off + ORDER_SIZE > len(buf): break
        vals, off = parse_series(buf, ORDER_SCHEMA, off)
        orders.append(vals)
    return positions, orders

def parse_deals(buf):
    if len(buf) < 4: return []
    deal_count = struct.unpack_from('<I', buf, 0)[0]
    off = 4
    deals = []
    for i in range(deal_count):
        if off + DEAL_SIZE > len(buf): break
        vals, off = parse_series(buf, DEAL_SCHEMA, off)
        deals.append(vals)
    return deals

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
        bal = 0
        if r:
            acct, _ = parse_series(r['res_body'], FL_SCHEMA, 0)
            bal = acct[3]
            print(f"\n=== ACCOUNT: balance={bal:.2f} ===")

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
        print(f"[+] {target}: id={sym_id}")

        # Get current price via cmd_id=11
        print(f"\n{'='*60}")
        print("Getting current price via cmd_id=11...")
        print(f"{'='*60}")

        now_ms = int(time.time() * 1000)
        rates_payload = bytearray(74)
        sym_bytes = target.encode('utf-16-le')
        rates_payload[0:0+len(sym_bytes)] = sym_bytes
        struct.pack_into('<H', rates_payload, 64, 1)  # M1
        struct.pack_into('<i', rates_payload, 66, (now_ms - 3600000) // 1000)
        struct.pack_into('<i', rates_payload, 70, now_ms // 1000)

        r = await send_and_wait(ws, sk, 11, bytes(rates_payload), 11, timeout=10)
        current_price = 1.08000
        if r:
            body = r['res_body']
            # Candles are 48B each, continuous stream (no count prefix)
            candle_count = len(body) // 48
            print(f"  Got {candle_count} candles ({len(body)}B)")
            if candle_count > 0:
                last_off = (candle_count - 1) * 48
                ts = struct.unpack_from('<I', body, last_off)[0]
                o = struct.unpack_from('<d', body, last_off+4)[0]
                h = struct.unpack_from('<d', body, last_off+12)[0]
                l = struct.unpack_from('<d', body, last_off+20)[0]
                c = struct.unpack_from('<d', body, last_off+28)[0]
                current_price = c
                print(f"  Last candle: O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f}")
                print(f"  Current price: {current_price:.5f}")

        # ===== TEST 1: BUY STOP (above market) =====
        print(f"\n{'='*60}")
        print(f"TEST 1: BUY STOP 0.01 lots @ {current_price + 0.00500:.5f} (50 pips above)")
        print(f"{'='*60}")

        op = place_order(target, sym_id, digits, 1, 4, current_price + 0.00500, vol=1000, comment=b'stop_test\x00')
        t0 = time.time()
        await send_cmd(ws, sk, 12, op)

        msgs = await collect_all(ws, sk, 10)
        for m in msgs:
            t = time.time() - t0
            if m['cmd_id'] == 12:
                retcode = struct.unpack_from('<I', m['res_body'], 0)[0] if len(m['res_body']) >= 4 else -1
                print(f"  [{t:.3f}s] TRADE retcode={retcode}")
            elif m['cmd_id'] == 19:
                pid = struct.unpack_from('<q', m['res_body'], 0)[0] if len(m['res_body']) >= 8 else 0
                # Decode key fields
                if len(m['res_body']) >= 88:
                    action_field = struct.unpack_from('<I', m['res_body'], 84)[0]
                    trade_type_field = struct.unpack_from('<I', m['res_body'], 88)[0]
                    print(f"  [{t:.3f}s] TRADE_EVENT pos_id={pid} field84={action_field} field88={trade_type_field}")
                else:
                    print(f"  [{t:.3f}s] TRADE_EVENT pos_id={pid}")
            elif m['cmd_id'] == 22:
                print(f"  [{t:.3f}s] POS_UPDATE ({len(m['res_body'])}B) hex={m['res_body'][:20].hex()}")
            elif m['cmd_id'] == 14:
                print(f"  [{t:.3f}s] ACCT_UPDATE ({len(m['res_body'])}B)")

        # Check positions/orders
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            positions, orders = parse_positions(r['res_body'])
            print(f"  Positions: {len(positions)}, Pending orders: {len(orders)}")
            for i, o in enumerate(orders):
                state = {0:'STARTED', 2:'CANCELED', 4:'FILLED', 5:'REJECTED'}.get(o[17], f"?{o[17]}")
                print(f"  Order[{i}]: {o[2]} type={o[6]} state={state} price={o[10]:.5f}")

        # ===== TEST 2: BUY LIMIT (below market) =====
        print(f"\n{'='*60}")
        print(f"TEST 2: BUY LIMIT 0.01 lots @ {current_price - 0.00500:.5f} (50 pips below)")
        print(f"{'='*60}")

        op = place_order(target, sym_id, digits, 1, 2, current_price - 0.00500, vol=1000, comment=b'limit_test\x00')
        t0 = time.time()
        await send_cmd(ws, sk, 12, op)

        msgs = await collect_all(ws, sk, 10)
        for m in msgs:
            t = time.time() - t0
            if m['cmd_id'] == 12:
                retcode = struct.unpack_from('<I', m['res_body'], 0)[0] if len(m['res_body']) >= 4 else -1
                print(f"  [{t:.3f}s] TRADE retcode={retcode}")
            elif m['cmd_id'] == 19:
                pid = struct.unpack_from('<q', m['res_body'], 0)[0] if len(m['res_body']) >= 8 else 0
                if len(m['res_body']) >= 88:
                    action_field = struct.unpack_from('<I', m['res_body'], 84)[0]
                    print(f"  [{t:.3f}s] TRADE_EVENT pos_id={pid} field84={action_field}")
                else:
                    print(f"  [{t:.3f}s] TRADE_EVENT pos_id={pid}")
            elif m['cmd_id'] == 22:
                print(f"  [{t:.3f}s] POS_UPDATE ({len(m['res_body'])}B)")

        # Check positions/orders
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            positions, orders = parse_positions(r['res_body'])
            print(f"  Positions: {len(positions)}, Pending orders: {len(orders)}")
            for i, o in enumerate(orders):
                state = {0:'STARTED', 2:'CANCELED', 4:'FILLED', 5:'REJECTED'}.get(o[17], f"?{o[17]}")
                print(f"  Order[{i}]: {o[2]} type={o[6]} state={state} price={o[10]:.5f}")

        # ===== TEST 3: MARKET BUY =====
        print(f"\n{'='*60}")
        print(f"TEST 3: MARKET BUY 0.01 lots @ {current_price:.5f}")
        print(f"{'='*60}")

        op = place_order(target, sym_id, digits, 3, 0, current_price, vol=1000, comment=b'market_test\x00')
        t0 = time.time()
        await send_cmd(ws, sk, 12, op)

        msgs = await collect_all(ws, sk, 10)
        for m in msgs:
            t = time.time() - t0
            if m['cmd_id'] == 12:
                retcode = struct.unpack_from('<I', m['res_body'], 0)[0] if len(m['res_body']) >= 4 else -1
                print(f"  [{t:.3f}s] TRADE retcode={retcode}")
            elif m['cmd_id'] == 19:
                pid = struct.unpack_from('<q', m['res_body'], 0)[0] if len(m['res_body']) >= 8 else 0
                if len(m['res_body']) >= 88:
                    action_field = struct.unpack_from('<I', m['res_body'], 84)[0]
                    print(f"  [{t:.3f}s] TRADE_EVENT pos_id={pid} field84={action_field}")
                else:
                    print(f"  [{t:.3f}s] TRADE_EVENT pos_id={pid}")
            elif m['cmd_id'] == 22:
                print(f"  [{t:.3f}s] POS_UPDATE ({len(m['res_body'])}B)")
            elif m['cmd_id'] == 14:
                print(f"  [{t:.3f}s] ACCT_UPDATE ({len(m['res_body'])}B)")

        # Check everything
        print(f"\n--- Final state ---")
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            positions, orders = parse_positions(r['res_body'])
            print(f"  Positions: {len(positions)}, Pending orders: {len(orders)}")
            for p in positions:
                act = 'BUY' if p[5] == 0 else 'SELL'
                print(f"  Position: {p[4]} {act} vol={p[10]/100000000:.2f} price={p[6]:.5f} profit={p[11]:.2f}")
            for o in orders:
                state = {0:'STARTED', 2:'CANCELED', 4:'FILLED', 5:'REJECTED'}.get(o[17], f"?{o[17]}")
                print(f"  Order: {o[2]} type={o[6]} state={state} price={o[10]:.5f}")

        r = await send_and_wait(ws, sk, 5, b'', 5, timeout=10)
        if r:
            deals = parse_deals(r['res_body'])
            print(f"  Deals: {len(deals)}")
            for d in deals[-3:]:
                entry = {0:'IN', 1:'OUT', 2:'IN/OUT', 3:'OUT_BY'}.get(d[7], f"?{d[7]}")
                print(f"  Deal: {d[5]} {entry} vol={d[12]/100000000:.2f} profit={d[13]:.2f}")

        r = await send_and_wait(ws, sk, 3, b'', 3)
        if r:
            acct, _ = parse_series(r['res_body'], FL_SCHEMA, 0)
            print(f"  Balance: {acct[3]:.2f}")

        print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
