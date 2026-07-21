#!/usr/bin/env python3
"""
Deep TRADE_EVENT (cmd_id=19) decode + correct volume encoding
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
                else:
                    vals.append(raw)
                offset += pl
        else: vals.append(None)
    return vals, offset

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

def decode_trade_event(body):
    """Decode 380B TRADE_EVENT body field by field using hex analysis."""
    print(f"\n  === TRADE_EVENT DECODE ({len(body)}B) ===")
    
    # From hex analysis of multiple TRADE_EVENT bodies:
    # [0-7]   position_id (i64)
    # [8-11]  trade_action (u32) - 0=buy/1=sell
    # [12-75] symbol (UTF-16LE, 64B)
    # [76-83] volume (u64)
    # [84-87] action (u32) - 0=DEAL, 1=PENDING, 3=MARKET, 10=CLOSE
    # [88-91] trade_type (u32) - 0=buy, 1=sell, 2=buy_limit, 3=sell_limit, 4=buy_stop, 5=sell_stop
    # [92-95] status/type (u32)
    # [96-99] something (u32)
    # [100-103] something (u32) = 17?
    # [104-111] padding/price?
    # [112-119] price_current (f64)
    # [120-127] price_sl (f64)
    # [128-135] price_tp (f64)
    # ... more fields
    
    # But let's decode field-by-field from the actual hex patterns:
    off = 0
    
    # Field 1: position_id (i64)
    if len(body) >= 8:
        val = struct.unpack_from('<q', body, 0)[0]
        print(f"  [0-7]   position_id = {val}")
    
    # Field 2: some_flags (u32) - looks like 1 in all cases
    if len(body) >= 12:
        val = struct.unpack_from('<I', body, 8)[0]
        print(f"  [8-11]  field_8 = {val}")
    
    # Field 3: symbol (UTF-16LE, 64B)
    if len(body) >= 76:
        raw = body[12:76]
        try:
            sym = raw.decode('utf-16-le').split('\x00')[0]
            print(f"  [12-75] symbol = '{sym}'")
        except:
            print(f"  [12-75] symbol = (decode error)")
    
    # Field 4: volume (u64)
    if len(body) >= 84:
        vol = struct.unpack_from('<Q', body, 76)[0]
        print(f"  [76-83] volume = {vol} ({vol/100000000:.2f} lots)")
    
    # Field 5: action (u32) at offset 84
    if len(body) >= 88:
        val = struct.unpack_from('<I', body, 84)[0]
        print(f"  [84-87] action = {val}")
    
    # Field 6: trade_type (u32) at offset 88
    if len(body) >= 92:
        val = struct.unpack_from('<I', body, 88)[0]
        print(f"  [88-91] trade_type = {val}")
    
    # Field 7: status (u32) at offset 92
    if len(body) >= 96:
        val = struct.unpack_from('<I', body, 92)[0]
        print(f"  [92-95] status = {val}")
    
    # Field 8: (u32) at offset 96
    if len(body) >= 100:
        val = struct.unpack_from('<I', body, 96)[0]
        print(f"  [96-99] field_96 = {val}")
    
    # Field 9: (u32) at offset 100
    if len(body) >= 104:
        val = struct.unpack_from('<I', body, 100)[0]
        print(f"  [100-103] field_100 = {val}")
    
    # Fields 10-11: padding (8B)
    if len(body) >= 112:
        val = struct.unpack_from('<q', body, 104)[0]
        print(f"  [104-111] field_104 = {val}")
    
    # Field 12: price (f64) at offset 112
    if len(body) >= 120:
        val = struct.unpack_from('<d', body, 112)[0]
        print(f"  [112-119] price = {val:.5f}")
    
    # Field 13: SL (f64) at offset 120
    if len(body) >= 128:
        val = struct.unpack_from('<d', body, 120)[0]
        print(f"  [120-127] sl = {val:.5f}")
    
    # Field 14: TP (f64) at offset 128
    if len(body) >= 136:
        val = struct.unpack_from('<d', body, 128)[0]
        print(f"  [128-135] tp = {val:.5f}")
    
    # Dump remaining
    for off in range(136, len(body), 8):
        if off + 8 <= len(body):
            val_f = struct.unpack_from('<d', body, off)[0]
            val_q = struct.unpack_from('<q', body, off)[0]
            val_u = struct.unpack_from('<Q', body, off)[0]
            if val_f != 0 or val_q != 0:
                print(f"  [{off:04d}-{off+7:04d}] f64={val_f:.5f} i64={val_q} u64={val_u}")

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print("[*] Connecting...")
    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        print("[+] Connected!")

        # Auth
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        sk = resp['res_body'][66:]
        print("[+] Auth OK")

        # Login
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(sk, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
        print("[+] Login OK")

        # Account + Symbols
        await send_cmd(ws, sk, 3)
        await send_cmd(ws, sk, 34)
        msgs = await collect_all(ws, sk, 5)
        acct_data = next((m['res_body'] for m in msgs if m['cmd_id'] == 3), None)
        sym_data = next((m['res_body'] for m in msgs if m['cmd_id'] == 34), None)

        if acct_data:
            acct, _ = parse_series(acct_data, FL_SCHEMA, 0)
            print(f"\n=== ACCOUNT: balance={acct[3]:.2f} equity={acct[4]:.2f} profit={acct[16]:.2f} ===")

        sym_map = {}
        if sym_data:
            try: decomp = zlib.decompress(sym_data[4:])
            except: decomp = zlib.decompress(sym_data[4:], -15)
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

        # Subscribe to quotes + updates
        await send_cmd(ws, sk, 7, struct.pack('<II', 1, sym_id))
        await send_cmd(ws, sk, 22, struct.pack('<II', 1, sym_id))
        await asyncio.sleep(2)

        # Try to get a quote
        quote = None
        print("[*] Collecting quotes...")
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=0.5)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_response(aes_decrypt(sk, resp[8:]))
                    if r and r['cmd_id'] == 8:
                        body = r['res_body']
                        qcount = struct.unpack_from('<I', body, 0)[0]
                        p = 4
                        for _ in range(qcount):
                            if p + 50 > len(body): break
                            qv, p = parse_series(body, [
                                {'propType':6},{'propType':3},{'propType':6},
                                {'propType':8},{'propType':8},{'propType':8},
                                {'propType':17},{'propType':6},{'propType':5},
                            ], p)
                            if qv[0] == sym_id:
                                quote = {'bid': qv[3], 'ask': qv[4]}
                                break
                    if quote: break
            except asyncio.TimeoutError:
                pass

        if quote:
            print(f"[+] LIVE: bid={quote['bid']:.5f} ask={quote['ask']:.5f}")
        else:
            print("[-] No live quote. Using last known price from deal history.")
            # Get price from deal history
            r = await send_and_wait(ws, sk, 5, b'', 5, timeout=10)
            if r:
                body = r['res_body']
                deal_cnt = struct.unpack_from('<I', body, 0)[0]
                off = 4
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
                for i in range(deal_cnt):
                    if off + 356 > len(body): break
                    d, off = parse_series(body, DEAL_SCHEMA, off)
                    if d[5] == target:
                        quote = {'bid': d[8], 'ask': d[9]}  # price_open, price_close
                        print(f"  Using last EURUSDm deal: bid≈{d[8]:.5f} ask≈{d[9]:.5f}")
                        break
                if not quote:
                    print("  No EURUSDm deal found. Using 1.08000 as default.")
                    quote = {'bid': 1.08000, 'ask': 1.08010}

        # ===== TEST: Place BUY LIMIT with CORRECT volume =====
        print(f"\n{'='*60}")
        print("TEST: BUY LIMIT with correct volume (1000 = 0.01 lots)")
        print(f"{'='*60}")

        # 0.01 lots = 1000 volume units
        vol = 1000  # 0.01 lots
        
        # Place BUY LIMIT at a price slightly ABOVE ask (so it stays pending)
        limit_price = quote['ask'] + 0.50  # 50 pips above ask

        op = bytearray(248)  # OP_SIZE = 248
        struct.pack_into('<I', op, 0, 0)                    # action_id = 0
        struct.pack_into('<I', op, 4, 1)                    # trade_action = PENDING (1)
        sym_bytes = target.encode('utf-16-le')
        op[8:8+len(sym_bytes)] = sym_bytes
        struct.pack_into('<Q', op, 72, vol)                 # volume = 1000 (0.01 lots)
        struct.pack_into('<I', op, 80, digits)              # digits = 5
        struct.pack_into('<Q', op, 84, 0)                   # trade_order = 0
        struct.pack_into('<I', op, 92, 2)                   # trade_type = BUY_LIMIT (2)
        struct.pack_into('<I', op, 96, 0)                   # type_filling = FOK
        struct.pack_into('<I', op, 100, 0)                  # type_time = GTC
        struct.pack_into('<I', op, 104, 2)                  # type_flags = 2
        struct.pack_into('<I', op, 108, 0)                  # type_reason = 0
        struct.pack_into('<d', op, 116, limit_price)        # price
        struct.pack_into('<d', op, 124, 0)                  # price_trigger
        struct.pack_into('<d', op, 132, 0)                  # sl
        struct.pack_into('<d', op, 140, 0)                  # tp
        struct.pack_into('<I', op, 148, 0)                  # price_deviation
        struct.pack_into('<d', op, 152, 0)                  # price_top
        struct.pack_into('<d', op, 160, 0)                  # price_bottom
        comment = b'investigate\x00'
        op[168:168+len(comment)] = comment
        struct.pack_into('<Q', op, 232, 0)                  # trade_position = 0
        struct.pack_into('<Q', op, 240, 0)                  # position_by = 0

        print(f"  BUY LIMIT 0.01 {target} @ {limit_price:.5f} (vol={vol})")

        t0 = time.time()
        await send_cmd(ws, sk, 12, bytes(op))

        # Collect responses
        print("\n[*] Responses:")
        all_msgs = await collect_all(ws, sk, 10)
        for m in all_msgs:
            t = time.time() - t0
            if m['cmd_id'] == 12:
                retcode = struct.unpack_from('<I', m['res_body'], 0)[0] if len(m['res_body']) >= 4 else -1
                print(f"  [{t:.3f}s] TRADE retcode={retcode}")
            elif m['cmd_id'] == 19:
                print(f"  [{t:.3f}s] TRADE_EVENT ({len(m['res_body'])}B)")
                decode_trade_event(m['res_body'])
            elif m['cmd_id'] == 14:
                print(f"  [{t:.3f}s] ACCT_UPDATE ({len(m['res_body'])}B)")
            elif m['cmd_id'] == 15:
                code = struct.unpack_from('<I', m['res_body'], 0)[0] if len(m['res_body']) >= 4 else 0
                print(f"  [{t:.3f}s] SYSTEM code={code} ({len(m['res_body'])}B)")
                if len(m['res_body']) >= 4:
                    print(f"    hex: {m['res_body'][:20].hex()}")
            elif m['cmd_id'] == 22:
                print(f"  [{t:.3f}s] POS_UPDATE ({len(m['res_body'])}B)")
                if len(m['res_body']) >= 4:
                    cnt = struct.unpack_from('<I', m['res_body'], 0)[0]
                    print(f"    body hex: {m['res_body'][:40].hex()}")
            elif m['cmd_id'] not in (8, 17, 51):
                name = {3:'ACCOUNT',4:'POSITIONS',5:'DEALS',7:'SUBSCRIBE'}.get(m['cmd_id'], f"CMD{m['cmd_id']}")
                print(f"  [{t:.3f}s] {name} cmd={m['cmd_id']} ({len(m['res_body'])}B)")

        # Check positions/orders
        print(f"\n{'='*60}")
        print("Positions after:")
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            body = r['res_body']
            pos_cnt = struct.unpack_from('<I', body, 0)[0] if len(body) >= 4 else 0
            order_cnt = struct.unpack_from('<I', body, 4)[0] if len(body) >= 8 else 0
            print(f"  Positions: {pos_cnt}, Orders: {order_cnt}")

        # Check deals
        print(f"\nDeals after:")
        r = await send_and_wait(ws, sk, 5, b'', 5, timeout=10)
        if r:
            body = r['res_body']
            deal_cnt = struct.unpack_from('<I', body, 0)[0]
            print(f"  Total deals: {deal_cnt}")

        # Account
        print(f"\nAccount after:")
        r = await send_and_wait(ws, sk, 3, b'', 3)
        if r:
            acct, _ = parse_series(r['res_body'], FL_SCHEMA, 0)
            print(f"  Balance: {acct[3]:.2f}  Equity: {acct[4]:.2f}")

        print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
