#!/usr/bin/env python3
"""
Diagnostic: Load full symbol spec via cmd_id=18 to get trade_exemode,
then try every trade_action value with correct parameters.
"""
import asyncio, struct, time, random, ssl, zlib
import websockets
from Crypto.Cipher import AES

WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER = "Exness-MT5Trial17"
SERVER_IP = "15.206.31.153"
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_encrypt(key, plaintext):
    pad_len = 16 - (len(plaintext) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(plaintext + bytes([pad_len] * pad_len))

def aes_decrypt(key, ciphertext):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

def pack_data(cmd_id, encrypted_data):
    return struct.pack('<II', len(encrypted_data), 1) + encrypted_data

def build_command(cmd_id, payload=b''):
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0, 255)
    cmd[1] = random.randint(0, 255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload:
        cmd[4:4+len(payload)] = payload
    return bytes(cmd)

def parse_response(data):
    if len(data) < 5:
        return None
    return {
        'tag': struct.unpack('<H', data[0:2])[0],
        'cmd_id': struct.unpack('<H', data[2:4])[0],
        'res_code': data[4],
        'res_body': data[5:]
    }

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
    for field in schema:
        pt = field['propType']
        if pt in TYPE_SIZES:
            size += TYPE_SIZES[pt]
        elif pt in (11, 12):
            size += field.get('propLength', 0)
    return size

def parse_series(data, schema, offset=0):
    vals = []
    for field in schema:
        pt = field['propType']
        pl = field.get('propLength', 0)
        if offset >= len(data):
            vals.append(None)
            continue
        if pt in TYPE_SIZES:
            sz = TYPE_SIZES[pt]
            if offset + sz > len(data):
                vals.append(None); offset = len(data); continue
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
        elif pt == 11:
            if offset + pl > len(data):
                vals.append(None); offset = len(data)
            else:
                raw = data[offset:offset+pl]
                try:
                    s = raw.decode('utf-16-le')
                    nul = s.find('\x00')
                    if nul >= 0: s = s[:nul]
                    vals.append(s)
                except:
                    vals.append(raw.hex())
                offset += pl
        elif pt == 12:
            if offset + pl > len(data):
                vals.append(None); offset = len(data)
            else:
                vals.append(data[offset:offset+pl])
                offset += pl
        else:
            vals.append(None)
    return vals, offset

# Compact symbol schema (cmd_id=34)
MH_SCHEMA = [
    {'propType':11,'propLength':64},
    {'propType':11,'propLength':128},
    {'propType':6},
    {'propType':6},
    {'propType':11,'propLength':256},
    {'propType':6},
    {'propType':11,'propLength':64},
    {'propType':5},
]
MH_SIZE = series_size(MH_SCHEMA)

# Account schema
FL_SCHEMA = [
    {'propType':4}, {'propType':3}, {'propType':3},
    {'propType':8}, {'propType':8},
    {'propType':11,'propLength':64},
    {'propType':6}, {'propType':6},
    {'propType':11,'propLength':256},
    {'propType':5},
    {'propType':11,'propLength':128},
    {'propType':11,'propLength':256},
    {'propType':3}, {'propType':1},
    {'propType':6}, {'propType':6},
    {'propType':8}, {'propType':8},
    {'propType':6}, {'propType':8},
    {'propType':6}, {'propType':8},
    {'propType':8}, {'propType':8},
    {'propType':6}, {'propType':6},
]

CMD_NAMES = {
    0:'AUTH', 2:'LOGOUT', 3:'ACCOUNT', 4:'POSITIONS', 5:'DEALS',
    6:'SYMBOLS_FULL', 7:'SUBSCRIBE', 8:'QUOTES', 9:'CATEGORIES',
    11:'RATES', 12:'TRADE', 14:'ACCT_UPDATE', 15:'SYSTEM',
    17:'SYMBOL_SPEC', 19:'TRADE_EVENT', 20:'SPREADS',
    22:'POS_UPDATE', 28:'LOGIN', 34:'SYMBOLS_GZ', 42:'NOTIFY', 51:'HEARTBEAT'
}

Kc = {'NONE':0, 'FOK':1, 'IOC':2, 'BOC':4, 'ALL':7}
Jc = {'NONE':0, 'GTC':1, 'DAY':2, 'SPECIFIED':4, 'SPECIFIED_DAY':8, 'ALL':15}
Zc = {'NONE':0, 'MARKET':1, 'LIMIT':2, 'STOP':4, 'STOP_LIMIT':8, 'SL':16, 'TP':32, 'CLOSEBY':64, 'ALL':127}

TRADE_EXEMODE_NAMES = {0:'REQUEST', 1:'INSTANT', 2:'MARKET', 3:'EXCHANGE'}
TRADE_MODE_NAMES = {0:'DISABLED', 1:'LONGONLY', 2:'SHORTONLY', 3:'CLOSEONLY'}

async def send_cmd(ws, sk, cmd_id, payload=b''):
    await ws.send(pack_data(cmd_id, aes_encrypt(sk, build_command(cmd_id, payload))))

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print(f"[*] Connecting to {WS_URL}...")
    async with websockets.connect(
        WS_URL, ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin': 'https://15.206.31.153:443'},
    ) as ws:
        print("[+] Connected!")

        # Auth
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        session_key = resp['res_body'][66:]
        print(f"[+] Auth OK")

        # Login
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(session_key, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
        print(f"[+] Login OK")

        # Get account + symbols
        await send_cmd(ws, session_key, 3)
        await send_cmd(ws, session_key, 34)
        acct_data = sym_data = None
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                if isinstance(resp_raw, bytes) and len(resp_raw) > 8:
                    r = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                    if r:
                        if r['cmd_id'] == 3: acct_data = r['res_body']
                        elif r['cmd_id'] == 34: sym_data = r['res_body']
            except asyncio.TimeoutError:
                pass

        # Parse account
        if acct_data:
            acct, _ = parse_series(acct_data, FL_SCHEMA, 0)
            print(f"\n=== ACCOUNT ===")
            print(f"  Balance:    {acct[3]:.2f}")
            print(f"  Group:      {acct[8]}")
            print(f"  Leverage:   1/{acct[9]}")

        # Parse symbols
        sym_map = {}
        if sym_data:
            try:
                decomp = zlib.decompress(sym_data[4:])
            except:
                decomp = zlib.decompress(sym_data[4:], -15)
            count = struct.unpack_from('<I', decomp, 0)[0]
            off = 4
            for _ in range(count):
                if off + MH_SIZE > len(decomp): break
                vals, off = parse_series(decomp, MH_SCHEMA, off)
                sym_map[vals[0]] = {'id': vals[3], 'digits': vals[2], 'calc_mode': vals[5]}

        target = 'EURUSDm'
        if target not in sym_map:
            print(f"[-] {target} not found!")
            return
        sym_id = sym_map[target]['id']
        digits = sym_map[target]['digits']
        print(f"\n[+] Target: {target} (id={sym_id}, digits={digits}, calc_mode={sym_map[target]['calc_mode']})")

        # === CRITICAL: Load full symbol spec via cmd_id=18 ===
        # Kl(t) = Uint32Array.from([t.length].concat(t)).buffer
        payload_18 = struct.pack('<I', 1) + struct.pack('<I', sym_id)
        print(f"\n[*] Loading full symbol spec (cmd_id=18, symbol_id={sym_id})...")
        await send_cmd(ws, session_key, 18, payload_18)

        # Wait for response - could be cmd_id=18 or cmd_id=13 (push)
        spec_data = None
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                if isinstance(resp_raw, bytes) and len(resp_raw) > 8:
                    r = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                    if r and r['cmd_id'] in (13, 18):
                        spec_data = r['res_body']
                        print(f"  Got spec response: cmd_id={r['cmd_id']}, body={len(spec_data)}B")
                        break
            except asyncio.TimeoutError:
                pass

        if not spec_data:
            print("[-] No spec response. Trying cmd_id=6 (full symbols fallback)...")
            await send_cmd(ws, session_key, 6)
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    if isinstance(resp_raw, bytes) and len(resp_raw) > 8:
                        r = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                        if r and r['cmd_id'] == 6:
                            spec_data = r['res_body']
                            print(f"  Got full symbols: cmd_id=6, body={len(spec_data)}B")
                            break
                except asyncio.TimeoutError:
                    pass

        if spec_data:
            print(f"\n=== FULL SYMBOL SPEC RAW ({len(spec_data)}B) ===")
            # Dump hex
            for i in range(0, min(len(spec_data), 512), 16):
                hex_str = ' '.join(f'{b:02x}' for b in spec_data[i:i+16])
                print(f"  [{i:3d}] {hex_str}")

            # The full symbol spec uses Ph schema from JS
            # Ph = [{id:i32}, {digits:u32}, {name:UTF16,64}, {desc:UTF16,256}, {path:UTF16,256},
            #        {calc_mode:u32}, {basis:UTF16,64}, {sector:u16}, {currency_margin:UTF16,64},
            #        {currency_base:UTF16,64}, {currency_profit:UTF16,64}, {currency_liq:UTF16,64},
            #        {ticks_flags:u32}, {calc_value:d64}, {margin_initial:d64}, {margin_maintenance:d64},
            #        {margin_long:d64}, {margin_short:d64}, ...]
            # Then trade config follows as a nested structure

            # Let's try to parse the known fields up to trade config
            FULL_SYM_SCHEMA = [
                {'propType':3},                          # 0: id (int32)
                {'propType':6},                          # 1: digits (uint32)
                {'propType':11,'propLength':64},         # 2: name (UTF-16LE)
                {'propType':11,'propLength':256},        # 3: description (UTF-16LE)
                {'propType':11,'propLength':256},        # 4: path (UTF-16LE)
                {'propType':6},                          # 5: calc_mode (uint32)
                {'propType':11,'propLength':64},         # 6: basis (UTF-16LE)
                {'propType':5},                          # 7: sector (uint16)
                {'propType':11,'propLength':64},         # 8: currency_margin (UTF-16LE)
                {'propType':11,'propLength':64},         # 9: currency_base (UTF-16LE)
                {'propType':11,'propLength':64},         # 10: currency_profit (UTF-16LE)
                {'propType':11,'propLength':64},         # 11: currency_liq (UTF-16LE)
                {'propType':6},                          # 12: ticks_flags (uint32)
                {'propType':8},                          # 13: calc_value (float64)
                {'propType':8},                          # 14: margin_initial (float64)
                {'propType':8},                          # 15: margin_maintenance (float64)
                {'propType':8},                          # 16: margin_long (float64)
                {'propType':8},                          # 17: margin_short (float64)
                {'propType':8},                          # 18: margin_limit (float64)
                {'propType':8},                          # 19: margin_stop (float64)
                {'propType':8},                          # 20: margin_stoplimit (float64)
                {'propType':6},                          # 21: trade_calc_mode (uint32)
                {'propType':6},                          # 22: trade_mode (uint32)  -- 0=disabled,1=long,2=short,3=close
                {'propType':6},                          # 23: trade_exemode (uint32) -- 0=request,1=instant,2=market,3=exchange
                {'propType':6},                          # 24: trade_fill_flags (uint32)
                {'propType':6},                          # 25: trade_time_flags (uint32)
                {'propType':6},                          # 26: trade_order_flags (uint32)
                {'propType':6},                          # 27: trade_stops_level (uint32)
                {'propType':6},                          # 28: trade_freeze_level (uint32)
                {'propType':6},                          # 29: trade_flags (uint32)
                {'propType':8},                          # 30: volume_min (float64)
                {'propType':8},                          # 31: volume_max (float64)
                {'propType':8},                          # 32: volume_step (float64)
                {'propType':6},                          # 33: volume_type (uint32)
            ]

            vals, end_off = parse_series(spec_data, FULL_SYM_SCHEMA, 0)
            print(f"\n=== PARSED FULL SYMBOL SPEC ===")
            field_names = ['id', 'digits', 'name', 'description', 'path', 'calc_mode', 'basis', 'sector',
                          'currency_margin', 'currency_base', 'currency_profit', 'currency_liq',
                          'ticks_flags', 'calc_value', 'margin_initial', 'margin_maintenance',
                          'margin_long', 'margin_short', 'margin_limit', 'margin_stop', 'margin_stoplimit',
                          'trade_calc_mode', 'trade_mode', 'trade_exemode', 'trade_fill_flags',
                          'trade_time_flags', 'trade_order_flags', 'trade_stops_level', 'trade_freeze_level',
                          'trade_flags', 'volume_min', 'volume_max', 'volume_step', 'volume_type']
            for i, v in enumerate(vals):
                name = field_names[i] if i < len(field_names) else f'field_{i}'
                if isinstance(v, float):
                    print(f"  [{i:2d}] {name:25s} = {v}")
                elif isinstance(v, str) and len(v) > 60:
                    print(f"  [{i:2d}] {name:25s} = {v[:60]}...")
                else:
                    print(f"  [{i:2d}] {name:25s} = {v}")

            if len(vals) > 23 and vals[23] is not None:
                exemode = vals[23]
                print(f"\n*** trade_exemode = {exemode} ({TRADE_EXEMODE_NAMES.get(exemode, 'UNKNOWN')}) ***")
                if vals[22] is not None:
                    print(f"*** trade_mode = {vals[22]} ({TRADE_MODE_NAMES.get(vals[22], 'UNKNOWN')}) ***")
                if vals[24] is not None:
                    ff = vals[24]
                    print(f"*** trade_fill_flags = {ff} (FOK={bool(ff&1)}, IOC={bool(ff&2)}, BOC={bool(ff&4)}) ***")
                if vals[25] is not None:
                    tf = vals[25]
                    print(f"*** trade_time_flags = {tf} (GTC={bool(tf&1)}, DAY={bool(tf&2)}, SPEC={bool(tf&4)}) ***")
                if vals[26] is not None:
                    of = vals[26]
                    print(f"*** trade_order_flags = {of} (MARKET={bool(of&1)}, LIMIT={bool(of&2)}, STOP={bool(of&4)}) ***")
                if vals[30] is not None:
                    print(f"*** volume_min={vals[30]} max={vals[31]} step={vals[32]} ***")
            else:
                print(f"\n[-] Could not parse trade fields. Raw at expected trade offset:")
                # Dump from end_off to show trade data
                for i in range(end_off, min(len(spec_data), end_off + 256), 16):
                    hex_str = ' '.join(f'{b:02x}' for b in spec_data[i:i+16])
                    print(f"  [{i:3d}] {hex_str}")
        else:
            print("[-] No spec data received")

        # === Subscribe to quotes ===
        sub_pl = struct.pack('<II', 1, sym_id)
        await send_cmd(ws, session_key, 7, sub_pl)
        print("\n[*] Subscribed to quotes, waiting for tick...")

        quote = None
        for i in range(40):
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                if isinstance(resp_raw, bytes) and len(resp_raw) > 8:
                    r = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                    if r and r['cmd_id'] == 8:
                        body = r['res_body']
                        qcount = struct.unpack_from('<I', body, 0)[0]
                        p = 4
                        for _ in range(qcount):
                            if p + 50 > len(body): break
                            sym_id_q = struct.unpack_from('<I', body, p)[0]
                            if sym_id_q == sym_id:
                                raw_bid = struct.unpack_from('<d', body, p + 16)[0]
                                raw_ask = struct.unpack_from('<d', body, p + 24)[0]
                                quote = {'bid': raw_bid / 10**digits, 'ask': raw_ask / 10**digits}
                                break
                            p += 50
                        if quote: break
            except asyncio.TimeoutError:
                if i > 0 and i % 10 == 0:
                    await send_cmd(ws, session_key, 7, sub_pl)

        if not quote:
            print("[-] No quote. Market may be closed.")
            return

        bid, ask = quote['bid'], quote['ask']
        print(f"[+] LIVE: bid={bid:.5f} ask={ask:.5f}")

        # === Try every trade_action value ===
        # Based on JS positionOpen/exemode mapping:
        # exemode=0 (Request): trade_action=0 (no price) or 1 (with price)
        # exemode=1 (Instant): trade_action=2
        # exemode=2 (Market):  trade_action=3
        # exemode=3 (Exchange): trade_action=4

        trade_tests = [
            ("action=0 BUY type_filling=0", 0, 0, 0, ask, 'FOK'),
            ("action=0 BUY type_filling=1", 0, 0, 1, ask, 'IOC'),
            ("action=0 BUY type_filling=2", 0, 0, 2, ask, 'RETURN'),
            ("action=1 BUY type_filling=0", 1, 0, 0, ask, 'FOK'),
            ("action=1 BUY type_filling=2", 1, 0, 2, ask, 'RETURN'),
            ("action=2 BUY type_filling=0", 2, 0, 0, ask, 'FOK'),
            ("action=2 BUY type_filling=1", 2, 0, 1, ask, 'IOC'),
            ("action=2 BUY type_filling=2", 2, 0, 2, ask, 'RETURN'),
            ("action=3 BUY type_filling=0", 3, 0, 0, ask, 'FOK'),
            ("action=3 BUY type_filling=1", 3, 0, 1, ask, 'IOC'),
            ("action=3 BUY type_filling=2", 3, 0, 2, ask, 'RETURN'),
        ]

        for label, trade_action, trade_type, type_filling, price, fill_name in trade_tests:
            op = bytearray(248)
            struct.pack_into('<I', op, 0, 0)                          # action_id
            struct.pack_into('<I', op, 4, trade_action)               # trade_action
            sym_bytes = target.encode('utf-16-le')
            op[8:8+len(sym_bytes)] = sym_bytes
            struct.pack_into('<Q', op, 72, 1000)                     # volume = 0.01 lots
            struct.pack_into('<I', op, 80, digits)                    # digits
            struct.pack_into('<Q', op, 84, 0)                         # trade_order
            struct.pack_into('<I', op, 92, trade_type)                # trade_type = BUY
            struct.pack_into('<I', op, 96, type_filling)              # type_filling
            struct.pack_into('<I', op, 100, 0)                        # type_time = GTC
            struct.pack_into('<I', op, 104, 0)                        # type_flags = 0
            struct.pack_into('<I', op, 108, 0)                        # type_reason
            struct.pack_into('<d', op, 112, price)                    # price_order
            struct.pack_into('<d', op, 120, 0)                        # price_trigger
            struct.pack_into('<d', op, 128, 0)                        # sl
            struct.pack_into('<d', op, 136, 0)                        # tp
            struct.pack_into('<I', op, 144, 20)                       # price_deviation
            struct.pack_into('<d', op, 148, 0)                        # price_top
            struct.pack_into('<d', op, 156, 0)                        # price_bottom
            struct.pack_into('<Q', op, 228, 0)                        # trade_position
            struct.pack_into('<Q', op, 236, 0)                        # position_by

            await send_cmd(ws, session_key, 12, bytes(op))

            # Collect responses
            t0 = time.time()
            retcode = None
            while time.time() - t0 < 3:
                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    if isinstance(resp_raw, bytes) and len(resp_raw) > 8:
                        r = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                        if r and r['cmd_id'] == 19:
                            body = r['res_body']
                            if len(body) >= 256:
                                retcode = struct.unpack_from('<I', body, 252)[0]
                            elif len(body) >= 4:
                                retcode = struct.unpack_from('<I', body, 0)[0]
                            break
                        elif r and r['cmd_id'] == 12:
                            if len(r['res_body']) >= 4:
                                retcode = struct.unpack_from('<I', r['res_body'], 0)[0]
                except asyncio.TimeoutError:
                    pass

            status = "OK" if retcode == 0 else f"FAIL({retcode})"
            print(f"  [{label:40s}] retcode={retcode} {status}")

        print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
