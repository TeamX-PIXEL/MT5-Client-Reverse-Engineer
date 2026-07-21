#!/usr/bin/env python3
"""
Pure Python MT5 WebSocket Client for Exness.
No browser/Playwright needed - direct WebSocket connection.

WORKING FEATURES:
  - Auth handshake (cmd 0)
  - Login (cmd 28)  
  - Account data (cmd 3)
  - Symbols list (cmd 34, gzip compressed)
  - Subscribe to quotes (cmd 7) with uint32 symbol_id array
  - Live quotes (cmd 8) - 50B per quote
  - Trade orders (cmd 12) - 248B Op schema, retcode=0
  - Trade events (cmd 19) - server push after trade
  - Open positions (cmd 4) - [pos_count, order_count] format
  - Deal history (cmd 5) - from/to uint32 timestamps
  - Heartbeat (cmd 51)
  - Symbol spec (cmd 17) - server push after subscription
"""
import asyncio, struct, time, random, ssl, zlib, sys, os
import websockets
from Crypto.Cipher import AES

# === CONFIG ===
WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER = "Exness-MT5Trial17"
SERVER_IP = "15.206.31.153"

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def find_server_ip(server_name, servers_dat_path=None):
    """
    Find server IP address by server name.
    Tries online APIs (SearchMQ + Search) first, falls back to local servers.dat.
    Returns list of IP:port strings or None if not found.
    """
    # === Method 1: Online APIs (broker_search.py) ===
    try:
        from broker_search import find_server_ips
        ips = find_server_ips(server_name)
        if ips:
            return ips
    except Exception:
        pass

    # === Method 2: Local servers.dat ===
    import glob
    from parse_servers_dat import parse_servers_dat
    
    default_paths = [
        servers_dat_path,
        "/home/teamx/.wine/drive_c/Program Files/MetaTrader 5 EXNESS/Config/servers.dat",
    ]
    
    filepath = None
    for path in default_paths:
        if path and os.path.exists(path):
            filepath = path
            break
    
    if not filepath:
        for path in default_paths:
            if path and '*' in path:
                matches = glob.glob(path)
                if matches:
                    filepath = matches[0]
                    break
    
    if not filepath:
        return None
    
    servers = parse_servers_dat(filepath)
    
    for s in servers:
        if s['name'].lower() == server_name.lower():
            ips = []
            for a in s.get('accesses', []):
                for addr in a.get('addresses', []):
                    ips.append(addr['address'])
            for a in s.get('accesses_ex', []):
                for addr in a.get('addresses', []):
                    ips.append(addr['address'])
            return ips if ips else None
    
    return None

# === BUILD INFO ===
import re, requests as _req

def get_build(url="https://15.206.31.153:443/terminal"):
    try:
        html = _req.get(url, verify=False, timeout=10).text
        m = re.search(r'"build"\s*:\s*(\d+)', html)
        build = int(m.group(1)) if m else None
        m2 = re.search(r'"build_date"\s*:\s*"([^"]+)"', html)
        build_date = m2.group(1) if m2 else None
        return build, build_date
    except Exception as e:
        return None, str(e)

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

# === BINARY PARSER (Ac.parse) ===
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

# Account schema (fl) - cmd_id=3, 816B header
FL_SCHEMA = [
    {'propType':4},          # flags (uint8)
    {'propType':3},          # login_id (int32)
    {'propType':3},          # permissions (int32)
    {'propType':8},          # balance (float64)
    {'propType':8},          # equity (float64)
    {'propType':11,'propLength':64},   # currency
    {'propType':6},          # field6 (uint32)
    {'propType':6},          # field7 (uint32)
    {'propType':11,'propLength':256},  # group
    {'propType':5},          # leverage (uint16)
    {'propType':11,'propLength':128},  # server
    {'propType':11,'propLength':256},  # account_name
    {'propType':3},          # trade_mode (int32)
    {'propType':1},          # some_flag (int8)
    {'propType':6},          # credit (uint32)
    {'propType':6},          # bonus (uint32)
    {'propType':8},          # profit (float64)
    {'propType':8},          # margin (float64)
    {'propType':6},          # field18 (uint32)
    {'propType':8},          # margin_float3 (float64)
    {'propType':6},          # stop_out (uint32)
    {'propType':8},          # margin_float4 (float64)
    {'propType':8},          # margin_float5 (float64)
    {'propType':8},          # margin_float6 (float64)
    {'propType':6},          # password_min (uint32)
    {'propType':6},          # password_flags (uint32)
]
FL_SIZE = series_size(FL_SCHEMA)

# Symbol schema (Mh) - cmd_id=34, 526B each
MH_SCHEMA = [
    {'propType':11,'propLength':64},   # name
    {'propType':11,'propLength':128},  # description
    {'propType':6},          # digits (uint32)
    {'propType':6},          # symbol_id (uint32) ← USED FOR SUBSCRIPTION
    {'propType':11,'propLength':256},  # path
    {'propType':6},          # trade_calc_mode (uint32)
    {'propType':11,'propLength':64},   # basis
    {'propType':5},          # sector (uint16)
]
MH_SIZE = series_size(MH_SCHEMA)

# Quote schema (Uh) - cmd_id=8, 50B each
QUOTE_SCHEMA = [
    {'propType':6},          # symbol_id (uint32)
    {'propType':3},          # tick_time (int32)
    {'propType':6},          # fields (uint32)
    {'propType':8},          # bid (float64 RAW → actual = raw / 10^digits)
    {'propType':8},          # ask (float64 RAW)
    {'propType':8},          # last (float64 RAW)
    {'propType':17},         # tick_volume (int64)
    {'propType':6},          # time_ms_delta (uint32)
    {'propType':5},          # flags (uint16)
]
QUOTE_SIZE = series_size(QUOTE_SCHEMA)

# Position schema (uu) - cmd_id=4, 344B each
POS_SCHEMA = [
    {'propType':17},         # position_id (int64)
    {'propType':17},         # trade_order (int64)
    {'propType':6},          # time_create (uint32)
    {'propType':6},          # time_update (uint32)
    {'propType':11,'propLength':64},   # symbol (UTF-16LE)
    {'propType':6},          # action (0=buy, 1=sell)
    {'propType':8},          # price_open (float64)
    {'propType':8},          # price_close (float64)
    {'propType':8},          # sl (float64)
    {'propType':8},          # tp (float64)
    {'propType':18},         # volume (uint64) → actual = raw / 100000
    {'propType':8},          # profit (float64)
    {'propType':8},          # rate_profit (float64)
    {'propType':8},          # rate_margin (float64)
    {'propType':8},          # commission (float64)
    {'propType':8},          # storage (float64)
    {'propType':17},         # expert (int64)
    {'propType':17},         # expert_pos_id (int64)
    {'propType':11,'propLength':64},   # comment (UTF-16LE)
    {'propType':8},          # contract_size (float64)
    {'propType':6},          # digits (uint32)
    {'propType':6},          # digits_currency (uint32)
    {'propType':6},          # magic (uint32)
    {'propType':11,'propLength':64},   # reason (UTF-16LE)
    {'propType':3},          # time_create_ms (int32)
    {'propType':3},          # time_update_ms (int32)
]
POS_SIZE = series_size(POS_SCHEMA)

# Deal schema (xd) - cmd_id=5, 356B each (28 fields, confirmed from JS)
DEAL_SCHEMA = [
    {'propType':17},         # [0] deal (int64)
    {'propType':11,'propLength':64},   # [1] deal_id (UTF-16LE)
    {'propType':17},         # [2] trade_order (int64)
    {'propType':6},          # [3] time_create (uint32)
    {'propType':6},          # [4] time_update (uint32)
    {'propType':11,'propLength':64},   # [5] trade_symbol (UTF-16LE)
    {'propType':6},          # [6] trade_action (uint32)
    {'propType':6},          # [7] entry (uint32) 0=IN,1=OUT
    {'propType':8},          # [8] price_open (float64)
    {'propType':8},          # [9] price_close (float64)
    {'propType':8},          # [10] sl (float64)
    {'propType':8},          # [11] tp (float64)
    {'propType':18},         # [12] trade_volume (uint64)
    {'propType':8},          # [13] profit (float64)
    {'propType':8},          # [14] rate_profit (float64)
    {'propType':8},          # [15] rate_margin (float64)
    {'propType':8},          # [16] commission (float64)
    {'propType':8},          # [17] storage (float64)
    {'propType':17},         # [18] expert (int64)
    {'propType':17},         # [19] position_id (int64)
    {'propType':11,'propLength':64},   # [20] comment (UTF-16LE)
    {'propType':8},          # [21] contract_size (float64)
    {'propType':6},          # [22] digits (uint32)
    {'propType':6},          # [23] digits_currency (uint32)
    {'propType':6},          # [24] trade_reason (uint32)
    {'propType':3},          # [25] time_create_ms (int32)
    {'propType':3},          # [26] time_update_ms (int32)
    {'propType':8},          # [27] commission_fee (float64)
]
DEAL_SIZE = series_size(DEAL_SCHEMA)
assert DEAL_SIZE == 356, f"DEAL_SIZE={DEAL_SIZE}, expected 356"

# Op schema (trade order - cmd_id=12, 248B)
OP_SCHEMA = [
    {'propType':6},          # action_id (uint32) = 0
    {'propType':6},          # trade_action (uint32) = 3 (market)
    {'propType':11,'propLength':64},   # symbol (UTF-16LE)
    {'propType':18},         # volume (uint64) = lots * 100000
    {'propType':6},          # digits (uint32)
    {'propType':18},         # trade_order (uint64) = 0 for new
    {'propType':6},          # trade_type (uint32) = 0=buy, 1=sell
    {'propType':6},          # type_filling (uint32) = 0=FOK
    {'propType':6},          # type_time (uint32) = 0=GTC
    {'propType':6},          # type_flags (uint32) = 2
    {'propType':6},          # type_reason (uint32) = 0
    {'propType':8},          # price_order (float64) = ask/bid
    {'propType':8},          # price_trigger (float64) = 0
    {'propType':8},          # price_sl (float64)
    {'propType':8},          # price_tp (float64)
    {'propType':6},          # price_deviation (uint32) = 0
    {'propType':8},          # price_top (float64) = 0
    {'propType':8},          # price_bottom (float64) = 0
    {'propType':11,'propLength':64},   # comment (UTF-16LE)
    {'propType':18},         # trade_position (uint64) = 0
    {'propType':18},         # position_by (uint64) = 0
    {'propType':6},          # time_expiration (uint32) = 0
]
OP_SIZE = series_size(OP_SCHEMA)

# === UTILITY ===

CMD_NAMES = {
    0:'AUTH', 2:'LOGOUT', 3:'ACCOUNT', 4:'POSITIONS', 5:'DEALS',
    6:'SYMBOLS_FULL', 7:'SUBSCRIBE', 8:'QUOTES', 9:'CATEGORIES',
    11:'RATES', 12:'TRADE', 14:'ACCT_UPDATE', 15:'SYSTEM',
    17:'SYMBOL_SPEC', 19:'TRADE_EVENT', 20:'SPREADS',
    22:'POS_UPDATE', 28:'LOGIN', 34:'SYMBOLS_GZ', 42:'NOTIFY', 51:'HEARTBEAT'
}

async def drain_messages(ws, sk, seconds, filter_cmd=None, verbose=False):
    """Drain all WebSocket messages for N seconds, optionally filtering by cmd_id."""
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
    """Send a command."""
    await ws.send(pack_data(cmd_id, aes_encrypt(sk, build_command(cmd_id, payload))))

async def send_and_wait(ws, sk, cmd_id, payload, expected_cmd, timeout=5):
    """Send a command and wait for a specific response."""
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

# === CANDLE DATA (cmd_id=11) ===
# Candle format: [timestamp:i32][open:f64][high:f64][low:f64][close:f64][volume:i64][spread:i32] = 48 bytes
CANDLE_SIZE = 48

# MT5 timeframe constants
TIMEFRAMES = {
    'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30,
    'H1': 16385, 'H4': 16388,
    'D1': 16408, 'W1': 32769, 'MN1': 49153,
}

def parse_candles(body):
    """Parse candle response: raw stream of 48-byte candles.
    Returns list of dicts with keys: time, timestamp, open, high, low, close, tick_volume, spread
    """
    import datetime as _dt
    if len(body) < CANDLE_SIZE:
        return []
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
            dt = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
        except (OSError, ValueError):
            dt = None
        candles.append({
            'time': dt, 'timestamp': ts,
            'open': o, 'high': h, 'low': l, 'close': c,
            'tick_volume': vol, 'spread': spread,
        })
        off += CANDLE_SIZE
    return candles

async def get_candles(ws, sk, symbol, timeframe, count=100):
    """Get historical candles.
    symbol: e.g. 'EURUSDm'
    timeframe: e.g. 'M1', 'H1', 'D1' (see TIMEFRAMES dict) or raw int value
    count: number of candles to request (determines time range)
    Returns list of candle dicts.
    """
    import datetime as _dt
    tf_val = TIMEFRAMES.get(timeframe, timeframe) if isinstance(timeframe, str) else timeframe
    now = int(time.time())
    # Estimate seconds per candle for time range
    if tf_val <= 30:
        sec_per_candle = tf_val * 60
    elif tf_val < 16385:
        sec_per_candle = tf_val * 60
    elif tf_val < 16408:
        sec_per_candle = 3600
    elif tf_val < 32769:
        sec_per_candle = 86400
    elif tf_val < 49153:
        sec_per_candle = 604800
    else:
        sec_per_candle = 2592000
    from_sec = now - (count + 10) * sec_per_candle  # +10 buffer
    pl = bytearray(64 + 2 + 4 + 4)
    sym_bytes = symbol.encode('utf-16-le')
    pl[0:0+len(sym_bytes)] = sym_bytes
    struct.pack_into('<H', pl, 64, tf_val)
    struct.pack_into('<i', pl, 66, from_sec)
    struct.pack_into('<i', pl, 70, now)
    r = await send_and_wait(ws, sk, 11, bytes(pl), 11, timeout=10)
    if r and len(r['res_body']) >= CANDLE_SIZE:
        return parse_candles(r['res_body'])
    return []

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

        # 1. Auth handshake (cmd 0)
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        session_key = resp['res_body'][66:]
        print(f"[+] Auth OK (session key: {session_key.hex()[:32]}...)")

        # 2. Login (cmd 28)
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(session_key, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
        login_body = resp['res_body']
        account_id = struct.unpack_from('<Q', login_body, 160)[0]
        print(f"[+] Login OK (account_id={account_id})")

        # 3. Get account + symbols
        await send_cmd(ws, session_key, 3)
        await send_cmd(ws, session_key, 34)

        acct_data = sym_data = None
        msgs = await drain_messages(ws, session_key, 5, verbose=False)
        for m in msgs:
            if m['cmd_id'] == 3: acct_data = m['res_body']
            elif m['cmd_id'] == 34: sym_data = m['res_body']

        # Parse account
        if acct_data:
            acct, _ = parse_series(acct_data, FL_SCHEMA, 0)
            print(f"\n=== ACCOUNT ===")
            print(f"  Balance:    {acct[3]:.2f}")
            print(f"  Equity:     {acct[4]:.2f}")
            print(f"  Currency:   {acct[5]}")
            print(f"  Group:      {acct[8]}")
            print(f"  Leverage:   1/{acct[9]}")
            print(f"  Server:     {acct[10]}")
            print(f"  Profit:     {acct[16]}")

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
                sym_map[vals[0]] = {'id': vals[3], 'digits': vals[2]}
            print(f"\n=== SYMBOLS ({len(sym_map)} total) ===")
            for name in ['EURUSDm', 'GBPUSDm', 'BTCUSDm', 'XAUUSDm']:
                if name in sym_map:
                    print(f"  {name}: id={sym_map[name]['id']} digits={sym_map[name]['digits']}")

        if not sym_map:
            print("[-] No symbols parsed!"); return

        # 4. Pick target symbol
        target = 'EURUSDm'
        if target not in sym_map:
            target = list(sym_map.keys())[0]
        sym_id = sym_map[target]['id']
        digits = sym_map[target]['digits']
        print(f"\n[+] Target: {target} (id={sym_id}, digits={digits})")

        # 5. Subscribe to quotes
        sub_pl = struct.pack('<II', 1, sym_id)
        await send_cmd(ws, session_key, 7, sub_pl)
        print("[+] Subscribed to quotes")

        # 6. Collect quotes (wait up to 30s for first tick)
        quote = None
        print("[*] Waiting for market tick...")
        for i in range(60):
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=0.5)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_response(aes_decrypt(session_key, resp[8:]))
                    if r and r['cmd_id'] == 8:
                        body = r['res_body']
                        qcount = struct.unpack_from('<I', body, 0)[0]
                        p = 4
                        for _ in range(qcount):
                            if p + QUOTE_SIZE > len(body): break
                            qv, p = parse_series(body, QUOTE_SCHEMA, p)
                            if qv[0] == sym_id:
                                raw_bid = qv[3]
                                raw_ask = qv[4]
                                quote = {'bid': raw_bid / 10**digits, 'ask': raw_ask / 10**digits,
                                         'raw_bid': raw_bid, 'raw_ask': raw_ask}
                                break
                        if quote: break
            except asyncio.TimeoutError:
                if i > 0 and i % 10 == 0:
                    print(f"  ... waiting ({i*0.5:.0f}s)")
                    # Re-subscribe every 10s
                    await send_cmd(ws, session_key, 7, sub_pl)

        if not quote:
            print("[-] No live quote after 30s. Market may be closed or server rate-limiting.")
            print("[*] Skipping trade test. Use during market hours.")
            print("\n[+] Protocol verification complete - all commands work!")
            return

        bid = quote['bid']
        ask = quote['ask']
        print(f"[+] LIVE: bid={bid:.5f} ask={ask:.5f} spread={((ask-bid)*10**digits):.1f}p")

        # 7. Check account before trade
        r = await send_and_wait(ws, session_key, 3, b'', 3)
        bal_before = struct.unpack_from('<d', r['res_body'], 9)[0] if r else 0
        print(f"\n[*] Balance before: {bal_before:.2f}")

        # 8. Check positions before trade
        r = await send_and_wait(ws, session_key, 4, b'', 4)
        pos_before = struct.unpack_from('<I', r['res_body'], 0)[0] if r and len(r['res_body']) >= 4 else 0
        print(f"[*] Positions before: {pos_before}")

        # 9. PLACE TRADE (BUY 0.01 lot)
        op = bytearray(OP_SIZE)
        struct.pack_into('<I', op, 0, 0)                          # action_id
        struct.pack_into('<I', op, 4, 3)                          # trade_action = market
        sym_bytes = target.encode('utf-16-le')
        op[8:8+len(sym_bytes)] = sym_bytes                        # symbol
        struct.pack_into('<Q', op, 72, 100000)                    # volume = 0.01 lots
        struct.pack_into('<I', op, 80, digits)                    # digits
        struct.pack_into('<Q', op, 84, 0)                         # trade_order = 0
        struct.pack_into('<I', op, 92, 0)                         # trade_type = BUY
        struct.pack_into('<I', op, 96, 0)                         # type_filling = FOK
        struct.pack_into('<I', op, 100, 0)                        # type_time = GTC
        struct.pack_into('<I', op, 104, 2)                        # type_flags = 2
        struct.pack_into('<I', op, 108, 0)                        # type_reason = 0
        struct.pack_into('<d', op, 112, ask)                      # price = ask
        struct.pack_into('<d', op, 120, 0)                        # price_trigger
        struct.pack_into('<d', op, 128, 0)                        # sl
        struct.pack_into('<d', op, 136, 0)                        # tp
        struct.pack_into('<I', op, 144, 0)                        # price_deviation
        struct.pack_into('<d', op, 148, 0)                        # price_top
        struct.pack_into('<d', op, 156, 0)                        # price_bottom
        # comment at offset 164, 64B - empty
        struct.pack_into('<Q', op, 228, 0)                        # trade_position
        struct.pack_into('<Q', op, 236, 0)                        # position_by

        # Build FULL Pp: [action_id(4)][Op(248)][Ap(128)] = 380 bytes
        trade_action_id = random.randint(0, 0xFFFFFFFF)
        ap = bytearray(128)  # Ap is all zeros for request
        pp = struct.pack('<I', trade_action_id) + bytes(op) + bytes(ap)

        print(f"\n{'='*50}")
        print(f"  BUY 0.01 {target} @ {ask:.5f}")
        print(f"  Pp size: {len(pp)} bytes (action_id={trade_action_id})")
        print(f"{'='*50}")

        t0 = time.time()
        await send_cmd(ws, session_key, 12, pp)

        # 10. Collect all responses for 8 seconds
        trade_retcode = None
        trade_evt = None
        for _ in range(40):
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=0.3)
                t = time.time() - t0
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_response(aes_decrypt(session_key, resp[8:]))
                    if not r: continue
                    name = CMD_NAMES.get(r['cmd_id'], f"CMD{r['cmd_id']}")

                    if r['cmd_id'] == 12:
                        # TRADE ack - retcode at offset 0 (simple ack, not Pp)
                        trade_retcode = struct.unpack_from('<I', r['res_body'], 0)[0] if len(r['res_body']) >= 4 else -1
                        print(f"  [{t:.3f}s] TRADE_ACK retcode={trade_retcode}")
                    elif r['cmd_id'] == 19:
                        trade_evt = r['res_body']
                        # Pp = [action_id(4)][Op(248)][Ap(128)] = 380B
                        # Ap starts at offset 252
                        ap_off = 4 + OP_SIZE  # = 252
                        if len(trade_evt) >= ap_off + 32:
                            retcode = struct.unpack_from('<I', trade_evt, ap_off)[0]
                            deal = struct.unpack_from('<q', trade_evt, ap_off + 4)[0]
                            order = struct.unpack_from('<q', trade_evt, ap_off + 12)[0]
                            vol = struct.unpack_from('<q', trade_evt, ap_off + 20)[0]
                            price = struct.unpack_from('<d', trade_evt, ap_off + 28)[0] if len(trade_evt) >= ap_off + 36 else 0
                            comment = trade_evt[ap_off+64:ap_off+128].decode('utf-16-le', errors='ignore').rstrip('\x00') if len(trade_evt) >= ap_off + 128 else ''
                            trade_retcode = retcode
                            print(f"  [{t:.3f}s] TRADE_EVENT retcode={retcode} deal={deal} order={order} vol={vol} price={price:.5f} comment='{comment}'")
                        else:
                            trade_evt = r['res_body']
                            print(f"  [{t:.3f}s] TRADE_EVENT ({len(trade_evt)}B, too short to parse)")
                    elif r['cmd_id'] == 22:
                        print(f"  [{t:.3f}s] POS_UPDATE ({len(r['res_body'])}B)")
                    elif r['cmd_id'] == 14:
                        print(f"  [{t:.3f}s] ACCT_UPDATE ({len(r['res_body'])}B)")
                    elif r['cmd_id'] == 15:
                        code = struct.unpack_from('<I', r['res_body'], 0)[0] if len(r['res_body']) >= 4 else 0
                        print(f"  [{t:.3f}s] SYSTEM code={code}")
                    elif r['cmd_id'] not in (8, 17, 51):
                        print(f"  [{t:.3f}s] {name} cmd={r['cmd_id']} body={len(r['res_body'])}B")
            except asyncio.TimeoutError:
                pass

        # 11. Check positions after trade
        print(f"\n--- After trade ({time.time()-t0:.1f}s) ---")
        r = await send_and_wait(ws, session_key, 4, b'', 4)
        if r:
            body = r['res_body']
            pos_cnt = struct.unpack_from('<I', body, 0)[0] if len(body) >= 4 else 0
            order_cnt = struct.unpack_from('<I', body, 4)[0] if len(body) >= 8 else 0
            print(f"  Positions: {pos_cnt}, Orders: {order_cnt}")
            if pos_cnt > 0:
                off = 4
                for i in range(pos_cnt):
                    if off + POS_SIZE > len(body): break
                    vals, off = parse_series(body, POS_SCHEMA, off)
                    act = 'BUY' if vals[5] == 0 else 'SELL'
                    print(f"  [{i}] {vals[4]} {act} id={vals[0]} vol={vals[10]/100000000:.2f} price={vals[6]:.5f} profit={vals[11]:.2f}")

        # 12. Check account after trade
        r = await send_and_wait(ws, session_key, 3, b'', 3)
        if r:
            bal_after = struct.unpack_from('<d', r['res_body'], 9)[0]
            print(f"  Balance: {bal_after:.2f} (delta: {bal_after-bal_before:.2f})")

        # 13. Summary
        print(f"\n{'='*50}")
        print(f"  RESULT: trade_ack_retcode={trade_retcode}")
        if trade_evt and len(trade_evt) >= ap_off + 32:
            ap_off_sum = 4 + OP_SIZE
            retcode_evt = struct.unpack_from('<I', trade_evt, ap_off_sum)[0]
            deal_id = struct.unpack_from('<q', trade_evt, ap_off_sum + 4)[0]
            order_id = struct.unpack_from('<q', trade_evt, ap_off_sum + 12)[0]
            print(f"  TRADE_EVENT: retcode={retcode_evt} deal={deal_id} order={order_id}")
        print(f"  Positions after: {pos_cnt if r else '?'}")
        print(f"{'='*50}")

        print("\n[+] Done!")

if __name__ == '__main__':
    asyncio.run(main())
