#!/usr/bin/env python3
"""
MT5 WebSocket History Test — Deals, Orders, Closed Positions

Tests:
  - cmd_id=5: Deal history + order history + closed positions
  - cmd_id=4: Open positions + pending orders
  - cmd_id=12: Place and cancel pending orders (LIMIT/STOP)
"""
import asyncio, struct, time, random, ssl, zlib
import websockets
from Crypto.Cipher import AES

# === CONFIG ===
WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER_IP = "15.206.31.153"

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

# === CRYPTO ===
def aes_encrypt(key, plaintext):
    pad_len = 16 - (len(plaintext) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(plaintext + bytes([pad_len] * pad_len))

def aes_decrypt(key, ciphertext):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

# === PROTOCOL FRAMING ===
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

# === BINARY PARSER ===
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

# === SCHEMAS ===

MH_SCHEMA = [
    {'propType':11,'propLength':64},   # name
    {'propType':11,'propLength':128},  # description
    {'propType':6},                    # digits
    {'propType':6},                    # symbol_id
    {'propType':11,'propLength':256},  # path
    {'propType':6},                    # trade_calc_mode
    {'propType':11,'propLength':64},   # basis
    {'propType':5},                    # sector
]
MH_SIZE = series_size(MH_SCHEMA)

FL_SCHEMA = [
    {'propType':4},          # flags
    {'propType':3},          # login_id
    {'propType':3},          # permissions
    {'propType':8},          # balance
    {'propType':8},          # equity
    {'propType':11,'propLength':64},   # currency
    {'propType':6},          # field6
    {'propType':6},          # field7
    {'propType':11,'propLength':256},  # group
    {'propType':5},          # leverage
    {'propType':11,'propLength':128},  # server
    {'propType':11,'propLength':256},  # account_name
    {'propType':3},          # trade_mode
    {'propType':1},          # some_flag
    {'propType':6},          # credit
    {'propType':6},          # bonus
    {'propType':8},          # profit
    {'propType':8},          # margin
]
FL_SIZE = series_size(FL_SCHEMA)

QUOTE_SCHEMA = [
    {'propType':6},          # symbol_id
    {'propType':3},          # tick_time
    {'propType':6},          # fields
    {'propType':8},          # bid (RAW)
    {'propType':8},          # ask (RAW)
    {'propType':8},          # last (RAW)
    {'propType':17},         # tick_volume
    {'propType':6},          # time_ms_delta
    {'propType':5},          # flags
]
QUOTE_SIZE = series_size(QUOTE_SCHEMA)

# Position schema (uu) - cmd_id=4, 344B each
POS_SCHEMA = [
    {'propType':17},         # [0] position_id (int64)
    {'propType':17},         # [1] trade_order (int64)
    {'propType':6},          # [2] time_create (uint32)
    {'propType':6},          # [3] time_update (uint32)
    {'propType':11,'propLength':64},   # [4] symbol
    {'propType':6},          # [5] action (0=buy, 1=sell)
    {'propType':8},          # [6] price_open
    {'propType':8},          # [7] price_close
    {'propType':8},          # [8] sl
    {'propType':8},          # [9] tp
    {'propType':18},         # [10] volume (uint64)
    {'propType':8},          # [11] profit
    {'propType':8},          # [12] rate_profit
    {'propType':8},          # [13] rate_margin
    {'propType':8},          # [14] commission
    {'propType':8},          # [15] storage
    {'propType':17},         # [16] expert (magic)
    {'propType':17},         # [17] expert_pos_id
    {'propType':11,'propLength':64},   # [18] comment
    {'propType':8},          # [19] contract_size
    {'propType':6},          # [20] digits
    {'propType':6},          # [21] digits_currency
    {'propType':6},          # [22] magic
    {'propType':11,'propLength':64},   # [23] reason
    {'propType':3},          # [24] time_create_ms
    {'propType':3},          # [25] time_update_ms
]
POS_SIZE = series_size(POS_SCHEMA)
assert POS_SIZE == 344, f"POS_SIZE={POS_SIZE}, expected 344"

# Deal schema (xd) - cmd_id=5, 356B each (28 fields)
DEAL_SCHEMA = [
    {'propType':17},         # [0] deal (int64)
    {'propType':11,'propLength':64},   # [1] deal_id
    {'propType':17},         # [2] trade_order (int64)
    {'propType':6},          # [3] time_create (uint32)
    {'propType':6},          # [4] time_update (uint32)
    {'propType':11,'propLength':64},   # [5] trade_symbol
    {'propType':6},          # [6] trade_action
    {'propType':6},          # [7] entry
    {'propType':8},          # [8] price_open
    {'propType':8},          # [9] price_close
    {'propType':8},          # [10] sl
    {'propType':8},          # [11] tp
    {'propType':18},         # [12] trade_volume
    {'propType':8},          # [13] profit
    {'propType':8},          # [14] rate_profit
    {'propType':8},          # [15] rate_margin
    {'propType':8},          # [16] commission
    {'propType':8},          # [17] storage_
    {'propType':17},         # [18] expert
    {'propType':17},         # [19] position_id
    {'propType':11,'propLength':64},   # [20] comment
    {'propType':8},          # [21] contract_size
    {'propType':6},          # [22] digits
    {'propType':6},          # [23] digits_currency
    {'propType':6},          # [24] trade_reason
    {'propType':3},          # [25] time_create_ms
    {'propType':3},          # [26] time_update_ms
    {'propType':8},          # [27] commission_fee
]
DEAL_SIZE = series_size(DEAL_SCHEMA)
assert DEAL_SIZE == 356, f"DEAL_SIZE={DEAL_SIZE}, expected 356"

# Order schema (Wd) - cmd_id=4 and cmd_id=5, 356B each (30 fields)
ORDER_SCHEMA = [
    {'propType':17},         # [0] trade_order (int64)
    {'propType':11,'propLength':64},   # [1] order_id
    {'propType':11,'propLength':64},   # [2] trade_symbol
    {'propType':6},          # [3] time_setup
    {'propType':6},          # [4] time_expiration
    {'propType':6},          # [5] time_done
    {'propType':6},          # [6] order_type
    {'propType':6},          # [7] type_filling
    {'propType':6},          # [8] type_time
    {'propType':6},          # [9] type_reason
    {'propType':8},          # [10] price_order
    {'propType':8},          # [11] price_trigger
    {'propType':8},          # [12] price_current
    {'propType':8},          # [13] price_sl
    {'propType':8},          # [14] price_tp
    {'propType':17},         # [15] volume_initial (int64)
    {'propType':17},         # [16] volume_current (int64)
    {'propType':6},          # [17] order_state
    {'propType':17},         # [18] expert (magic)
    {'propType':17},         # [19] position_id
    {'propType':11,'propLength':64},   # [20] comment
    {'propType':8},          # [21] contract_size
    {'propType':6},          # [22] digits
    {'propType':6},          # [23] digits_currency
    {'propType':8},          # [24] commission_daily
    {'propType':8},          # [25] commission_monthly
    {'propType':8},          # [26] margin_rate
    {'propType':6},          # [27] activation_mode
    {'propType':3},          # [28] time_setup_ms
    {'propType':3},          # [29] time_done_ms
]
ORDER_SIZE = series_size(ORDER_SCHEMA)
assert ORDER_SIZE == 356, f"ORDER_SIZE={ORDER_SIZE}, expected 356"

# Op schema (trade order - cmd_id=12, 248B)
OP_SCHEMA = [
    {'propType':6},          # action_id = 0
    {'propType':6},          # trade_action
    {'propType':11,'propLength':64},   # symbol
    {'propType':18},         # volume
    {'propType':6},          # digits
    {'propType':18},         # trade_order = 0 for new
    {'propType':6},          # trade_type = 0=buy, 1=sell
    {'propType':6},          # type_filling = 0=FOK
    {'propType':6},          # type_time = 0=GTC
    {'propType':6},          # type_flags = 2
    {'propType':6},          # type_reason = 0
    {'propType':8},          # price_order
    {'propType':8},          # price_trigger
    {'propType':8},          # price_sl
    {'propType':8},          # price_tp
    {'propType':6},          # price_deviation = 0
    {'propType':8},          # price_top = 0
    {'propType':8},          # price_bottom = 0
    {'propType':11,'propLength':64},   # comment
    {'propType':18},         # trade_position = 0
    {'propType':18},         # position_by = 0
    {'propType':6},          # time_expiration = 0
]
OP_SIZE = series_size(OP_SCHEMA)

# === HELPERS ===
CMD_NAMES = {
    0:'AUTH', 2:'LOGOUT', 3:'ACCOUNT', 4:'POSITIONS', 5:'DEALS',
    6:'SYMBOLS_FULL', 7:'SUBSCRIBE', 8:'QUOTES', 9:'CATEGORIES',
    11:'RATES', 12:'TRADE', 14:'ACCT_UPDATE', 15:'SYSTEM',
    17:'SYMBOL_SPEC', 19:'TRADE_EVENT', 20:'SPREADS',
    22:'POS_UPDATE', 28:'LOGIN', 34:'SYMBOLS_GZ', 42:'NOTIFY', 51:'HEARTBEAT'
}

ACTION_NAMES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}
ENTRY_NAMES = {0:'IN', 1:'OUT', 2:'IN/OUT', 3:'OUT_BY'}
ORDER_TYPE_NAMES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}
ORDER_STATE_NAMES = {0:'STARTED', 2:'CANCELED', 4:'FILLED', 5:'REJECTED'}

def ts_str(unix_sec, ms=0):
    if not unix_sec: return "N/A"
    import datetime
    t = datetime.datetime.utcfromtimestamp(unix_sec + ms/1000.0)
    return t.strftime('%Y-%m-%d %H:%M:%S') + f'.{ms:03d}'

async def drain_messages(ws, sk, seconds, filter_cmd=None, verbose=False):
    results = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=0.3)
            if isinstance(resp, bytes) and len(resp) > 8:
                r = parse_response(aes_decrypt(sk, resp[8:]))
                if r:
                    if verbose:
                        name = CMD_NAMES.get(r['cmd_id'], f"CMD{r['cmd_id']}")
                        print(f"  recv {name} cmd={r['cmd_id']} code={r['res_code']} body={len(r['res_body'])}B")
                    if filter_cmd is None or r['cmd_id'] == filter_cmd:
                        results.append(r)
        except asyncio.TimeoutError:
            pass
    return results

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
                if r and r['cmd_id'] == expected_cmd:
                    return r
        except asyncio.TimeoutError:
            break
    return None

# === PARSE FUNCTIONS ===

def parse_positions(buf):
    """Parse cmd_id=4 response: [pos_count, positions, order_count, orders]"""
    if len(buf) < 4:
        return [], []
    pos_count = struct.unpack_from('<I', buf, 0)[0]
    off = 4
    positions = []
    for i in range(pos_count):
        if off + POS_SIZE > len(buf): break
        vals, off = parse_series(buf, POS_SCHEMA, off)
        positions.append(vals)

    if off + 4 > len(buf):
        return positions, []
    order_count = struct.unpack_from('<I', buf, off)[0]
    off += 4
    orders = []
    for i in range(order_count):
        if off + ORDER_SIZE > len(buf): break
        vals, off = parse_series(buf, ORDER_SCHEMA, off)
        orders.append(vals)
    return positions, orders

def parse_deals_and_orders(buf):
    """Parse cmd_id=5 response: [deal_count, deals, order_count, orders]"""
    if len(buf) < 4:
        return [], []
    deal_count = struct.unpack_from('<I', buf, 0)[0]
    off = 4
    deals = []
    for i in range(deal_count):
        if off + DEAL_SIZE > len(buf): break
        vals, off = parse_series(buf, DEAL_SCHEMA, off)
        deals.append(vals)

    if off + 4 > len(buf):
        return deals, []
    order_count = struct.unpack_from('<I', buf, off)[0]
    off += 4
    orders = []
    for i in range(order_count):
        if off + ORDER_SIZE > len(buf): break
        vals, off = parse_series(buf, ORDER_SCHEMA, off)
        orders.append(vals)
    return deals, orders

def print_deal(d, idx, sym_map=None):
    """Print a deal record"""
    deal_ticket = d[0]
    deal_id = d[1]
    order_ticket = d[2]
    time_create = d[3]
    time_update = d[4]
    symbol = d[5]
    action = d[6]
    entry = d[7]
    price_open = d[8]
    price_close = d[9]
    sl = d[10]
    tp = d[11]
    volume = d[12]
    profit = d[13]
    commission = d[16]
    storage = d[17]
    position_id = d[19]
    comment = d[20]
    digits = d[22]

    lots = volume / 100000000 if volume else 0
    action_name = ACTION_NAMES.get(action, f'UNK({action})')
    entry_name = ENTRY_NAMES.get(entry, f'UNK({entry})')

    print(f"  [{idx:3d}] ticket={deal_ticket} deal_id={deal_id}")
    print(f"        order={order_ticket} pos_id={position_id}")
    print(f"        {symbol} {action_name} {entry_name} {lots:.2f} lots")
    print(f"        open={price_open:.5f} close={price_close:.5f}")
    if sl: print(f"        SL={sl:.5f} TP={tp:.5f}")
    print(f"        profit={profit:.2f} commission={commission:.2f} swap={storage:.2f}")
    if comment: print(f"        comment={comment}")
    print(f"        time={ts_str(time_create, d[25])}")

def print_order(o, idx):
    """Print an order record"""
    order_ticket = o[0]
    order_id = o[1]
    symbol = o[2]
    time_setup = o[3]
    time_expiration = o[4]
    time_done = o[5]
    order_type = o[6]
    type_filling = o[7]
    type_time = o[8]
    type_reason = o[9]
    price_order = o[10]
    price_trigger = o[11]
    price_current = o[12]
    price_sl = o[13]
    price_tp = o[14]
    volume_initial = o[15]
    volume_current = o[16]
    order_state = o[17]
    expert = o[18]
    position_id = o[19]
    comment = o[20]
    digits = o[22]

    lots_init = volume_initial / 100000000 if volume_initial else 0
    lots_curr = volume_current / 100000000 if volume_current else 0
    type_name = ORDER_TYPE_NAMES.get(order_type, f'TYPE({order_type})')
    state_name = ORDER_STATE_NAMES.get(order_state, f'STATE({order_state})')

    print(f"  [{idx:3d}] ticket={order_ticket} id={order_id}")
    print(f"        {symbol} {type_name} {lots_init:.2f} lots")
    print(f"        price={price_order:.5f} state={state_name}")
    if price_sl: print(f"        SL={price_sl:.5f} TP={price_tp:.5f}")
    if volume_initial != volume_current:
        print(f"        remaining={lots_curr:.2f} lots")
    if position_id: print(f"        linked_pos={position_id}")
    if comment: print(f"        comment={comment}")
    print(f"        setup={ts_str(time_setup, o[28])}")

# === MAIN ===
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

        # 1. Auth
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        session_key = resp['res_body'][66:]
        print(f"[+] Auth OK")

        # 2. Login
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(session_key, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
        login_body = resp['res_body']
        account_id = struct.unpack_from('<Q', login_body, 160)[0]
        print(f"[+] Login OK (account={account_id})")

        # 3. Get account + symbols
        await send_cmd(ws, session_key, 3)
        await send_cmd(ws, session_key, 34)
        msgs = await drain_messages(ws, session_key, 5)
        acct_data = sym_data = None
        for m in msgs:
            if m['cmd_id'] == 3: acct_data = m['res_body']
            elif m['cmd_id'] == 34: sym_data = m['res_body']

        if acct_data:
            acct, _ = parse_series(acct_data, FL_SCHEMA, 0)
            print(f"\n=== ACCOUNT ===")
            print(f"  Balance:  {acct[3]:.2f}")
            print(f"  Equity:   {acct[4]:.2f}")
            print(f"  Currency: {acct[5]}")

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
                sym_map[vals[0]] = {'id': vals[3], 'digits': vals[2]}
            print(f"  Symbols: {len(sym_map)} total")

        # === TEST 1: GET OPEN POSITIONS + PENDING ORDERS (cmd_id=4) ===
        print(f"\n{'='*60}")
        print(f"  TEST 1: OPEN POSITIONS & PENDING ORDERS (cmd_id=4)")
        print(f"{'='*60}")

        r = await send_and_wait(ws, session_key, 4, b'', 4)
        if r:
            positions, orders = parse_positions(r['res_body'])
            print(f"\n  Open Positions: {len(positions)}")
            for i, pos in enumerate(positions):
                print_position(pos, i, sym_map)
            print(f"\n  Pending Orders: {len(orders)}")
            for i, order in enumerate(orders):
                print_order(order, i)
        else:
            print("  [-] No response for cmd_id=4")

        # === TEST 2: GET DEAL HISTORY (cmd_id=5) ===
        print(f"\n{'='*60}")
        print(f"  TEST 2: DEAL HISTORY (cmd_id=5)")
        print(f"{'='*60}")

        r = await send_and_wait(ws, session_key, 5, struct.pack('<II', 0, 0), 5)
        if r:
            deals, hist_orders = parse_deals_and_orders(r['res_body'])
            print(f"\n  Deals: {len(deals)}")
            for i, deal in enumerate(deals):
                print_deal(deal, i, sym_map)

            print(f"\n  History Orders: {len(hist_orders)}")
            for i, order in enumerate(hist_orders):
                print_order(order, i)
        else:
            print("  [-] No response for cmd_id=5")

        # === TEST 3: CLOSE POSITION (if any) ===
        if positions:
            print(f"\n{'='*60}")
            print(f"  TEST 3: CLOSE FIRST POSITION")
            print(f"{'='*60}")

            pos = positions[0]
            pos_id = pos[0]
            symbol = pos[4]
            action = pos[5]  # 0=buy, 1=sell
            volume = pos[10]
            digits = sym_map.get(symbol, {}).get('digits', 5)

            print(f"  Closing: {symbol} {'BUY' if action==0 else 'SELL'} pos_id={pos_id}")

            # Build close order
            op = bytearray(OP_SIZE)
            struct.pack_into('<I', op, 0, 0)           # action_id
            struct.pack_into('<I', op, 4, 10)           # trade_action = CLOSE
            sym_bytes = symbol.encode('utf-16-le')
            op[8:8+len(sym_bytes)] = sym_bytes           # symbol
            struct.pack_into('<Q', op, 72, volume)       # volume
            struct.pack_into('<I', op, 80, digits)       # digits
            struct.pack_into('<Q', op, 84, 0)            # trade_order = 0
            struct.pack_into('<I', op, 92, 1 if action == 0 else 0)  # opposite direction
            struct.pack_into('<I', op, 96, 0)            # FOK
            struct.pack_into('<I', op, 100, 0)           # GTC
            struct.pack_into('<I', op, 104, 2)           # flags
            struct.pack_into('<Q', op, 228, pos_id)      # trade_position = pos_id to close

            await send_cmd(ws, session_key, 12, bytes(op))
            msgs = await drain_messages(ws, session_key, 5, verbose=True)

            for m in msgs:
                if m['cmd_id'] == 12:
                    retcode = struct.unpack_from('<I', m['res_body'], 0)[0]
                    print(f"  CLOSE retcode={retcode}")
                elif m['cmd_id'] == 19:
                    pid = struct.unpack_from('<q', m['res_body'], 0)[0]
                    print(f"  TRADE_EVENT pos_id={pid}")

            # Check positions after close
            r = await send_and_wait(ws, session_key, 4, b'', 4)
            if r:
                pos_after, ord_after = parse_positions(r['res_body'])
                print(f"  Positions after close: {len(pos_after)}")
        else:
            print(f"\n  [!] No open positions to close")

        # === TEST 4: PLACE PENDING ORDER (BUY LIMIT) ===
        print(f"\n{'='*60}")
        print(f"  TEST 4: PLACE BUY LIMIT ORDER")
        print(f"{'='*60}")

        target = 'EURUSDm' if 'EURUSDm' in sym_map else list(sym_map.keys())[0]
        digits = sym_map[target]['digits']
        sub_pl = struct.pack('<II', 1, sym_map[target]['id'])
        await send_cmd(ws, session_key, 7, sub_pl)
        quote = None
        for i in range(20):
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=0.5)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r2 = parse_response(aes_decrypt(session_key, resp[8:]))
                    if r2 and r2['cmd_id'] == 8:
                        body = r2['res_body']
                        qcount = struct.unpack_from('<I', body, 0)[0]
                        p = 4
                        for _ in range(qcount):
                            if p + QUOTE_SIZE > len(body): break
                            qv, p = parse_series(body, QUOTE_SCHEMA, p)
                            if qv[0] == sym_map[target]['id']:
                                quote = {'bid': qv[3] / 10**digits, 'ask': qv[4] / 10**digits}
                                break
                        if quote: break
            except asyncio.TimeoutError:
                pass

        if quote:
            limit_price = quote['bid'] - 0.00500  # 50 pips below current bid
            print(f"  Current: bid={quote['bid']:.5f} ask={quote['ask']:.5f}")
            print(f"  Placing BUY LIMIT @ {limit_price:.5f} (50 pips below bid)")

            op = bytearray(OP_SIZE)
            struct.pack_into('<I', op, 0, 0)           # action_id
            struct.pack_into('<I', op, 4, 1)           # trade_action = PENDING
            sym_bytes = target.encode('utf-16-le')
            op[8:8+len(sym_bytes)] = sym_bytes           # symbol
            struct.pack_into('<Q', op, 72, 100000)      # volume = 0.01 lots
            struct.pack_into('<I', op, 80, digits)       # digits
            struct.pack_into('<Q', op, 84, 0)            # trade_order
            struct.pack_into('<I', op, 92, 2)            # trade_type = BUY LIMIT
            struct.pack_into('<I', op, 96, 0)            # FOK
            struct.pack_into('<I', op, 100, 0)           # GTC
            struct.pack_into('<I', op, 104, 2)           # flags
            struct.pack_into('<d', op, 112, limit_price) # price = limit price

            await send_cmd(ws, session_key, 12, bytes(op))
            msgs = await drain_messages(ws, session_key, 5, verbose=True)
            for m in msgs:
                if m['cmd_id'] == 12:
                    retcode = struct.unpack_from('<I', m['res_body'], 0)[0]
                    print(f"  PENDING ORDER retcode={retcode}")
                elif m['cmd_id'] == 19:
                    pid = struct.unpack_from('<q', m['res_body'], 0)[0]
                    print(f"  TRADE_EVENT order_id={pid}")

            # Check pending orders
            r = await send_and_wait(ws, session_key, 4, b'', 4)
            if r:
                pos_after, ord_after = parse_positions(r['res_body'])
                print(f"  Positions: {len(pos_after)}, Pending Orders: {len(ord_after)}")
                for i, o in enumerate(ord_after):
                    print_order(o, i)
        else:
            print("  [-] No quote available, skipping pending order test")

        print(f"\n{'='*60}")
        print(f"  ALL TESTS COMPLETE")
        print(f"{'='*60}")

def print_position(pos, idx, sym_map=None):
    pos_id = pos[0]
    trade_order = pos[1]
    time_create = pos[2]
    symbol = pos[4]
    action = pos[5]
    price_open = pos[6]
    price_close = pos[7]
    sl = pos[8]
    tp = pos[9]
    volume = pos[10]
    profit = pos[11]
    comment = pos[18]
    digits = pos[20]

    lots = volume / 100000000 if volume else 0
    act = 'BUY' if action == 0 else 'SELL'

    print(f"  [{idx}] {symbol} {act} pos_id={pos_id}")
    print(f"      vol={lots:.2f} open={price_open:.5f} close={price_close:.5f} profit={profit:.2f}")
    if sl: print(f"      SL={sl:.5f} TP={tp:.5f}")
    if comment: print(f"      comment={comment}")

if __name__ == '__main__':
    asyncio.run(main())
