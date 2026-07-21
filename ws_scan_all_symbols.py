#!/usr/bin/env python3
"""
Scan multiple symbols' full spec (cmd_id=18) to find trade_mode.
EURUSDm has trade_mode=0 (DISABLED) - find symbols with trade_mode != 0.
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

async def send_cmd(ws, sk, cmd_id, payload=b''):
    await ws.send(pack_data(cmd_id, aes_encrypt(sk, build_command(cmd_id, payload))))

async def send_and_wait(ws, sk, cmd_id, payload, expected_cmd, timeout=10):
    await send_cmd(ws, sk, cmd_id, payload)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
            if resp and resp['cmd_id'] == expected_cmd:
                return resp
        except asyncio.TimeoutError:
            continue
    return None

# Symbol schema (Mh) - cmd_id=34, 526B each
MH_SCHEMA = [
    {'propType':11,'propLength':64},   # name
    {'propType':11,'propLength':128},  # description
    {'propType':6},          # digits (uint32)
    {'propType':6},          # symbol_id (uint32)
    {'propType':11,'propLength':256},  # path
    {'propType':6},          # trade_calc_mode (uint32)
    {'propType':11,'propLength':64},   # basis
    {'propType':5},          # sector (uint16)
]

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

MH_SIZE = series_size(MH_SCHEMA)

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

# Trade settings offsets within the trade config section
# From brute-force scan of EURUSDm:
#   trade settings section starts at sym_data offset 1536
#   trade_mode is at trade_settings_start + 264
#   trade_exemode is at trade_settings_start + 276
#   trade_fill_flags is at trade_settings_start + 280
#   trade_order_flags is at trade_settings_start + 288
#   volume_min is at trade_settings_start + 336 (u64)
TRADE_SETTINGS_OFFSET = 1536
TRADE_MODE_OFFSET = TRADE_SETTINGS_OFFSET + 264
TRADE_EXEMODE_OFFSET = TRADE_SETTINGS_OFFSET + 276
TRADE_FILL_FLAGS_OFFSET = TRADE_SETTINGS_OFFSET + 280
TRADE_ORDER_FLAGS_OFFSET = TRADE_SETTINGS_OFFSET + 288
TRADE_STOPS_LEVEL_OFFSET = TRADE_SETTINGS_OFFSET + 292
TRADE_FREEZE_LEVEL_OFFSET = TRADE_SETTINGS_OFFSET + 296
VOLUME_MIN_OFFSET = TRADE_SETTINGS_OFFSET + 336
VOLUME_MAX_OFFSET = TRADE_SETTINGS_OFFSET + 344
VOLUME_STEP_OFFSET = TRADE_SETTINGS_OFFSET + 348

TRADE_MODE_MAP = {0: 'DISABLED', 1: 'LONGONLY', 2: 'SHORTONLY', 3: 'CLOSEONLY'}
TRADE_EXEMODE_MAP = {0: 'REQUEST', 1: 'INSTANT', 2: 'MARKET', 3: 'EXCHANGE'}

async def scan_symbol_spec(ws, sk, sym_id, sym_name):
    """Request full symbol spec and scan for trade_mode."""
    payload = struct.pack('<II', 1, sym_id)
    resp = await send_and_wait(ws, sk, 18, payload, 18, timeout=10)
    if not resp:
        return None
    
    body = resp['res_body']
    if len(body) < 4:
        return None
    
    count = struct.unpack_from('<I', body, 0)[0]
    sym_data = body[4:]
    
    if len(sym_data) < TRADE_SETTINGS_OFFSET + 400:
        return None
    
    result = {
        'name': sym_name,
        'id': sym_id,
        'data_size': len(sym_data),
    }
    
    # Extract trade_mode (u32)
    if len(sym_data) >= TRADE_MODE_OFFSET + 4:
        result['trade_mode'] = struct.unpack_from('<I', sym_data, TRADE_MODE_OFFSET)[0]
    
    # Extract trade_exemode (u32)
    if len(sym_data) >= TRADE_EXEMODE_OFFSET + 4:
        result['trade_exemode'] = struct.unpack_from('<I', sym_data, TRADE_EXEMODE_OFFSET)[0]
    
    # Extract trade_fill_flags (u32)
    if len(sym_data) >= TRADE_FILL_FLAGS_OFFSET + 4:
        result['trade_fill_flags'] = struct.unpack_from('<I', sym_data, TRADE_FILL_FLAGS_OFFSET)[0]
    
    # Extract trade_order_flags (u32)
    if len(sym_data) >= TRADE_ORDER_FLAGS_OFFSET + 4:
        result['trade_order_flags'] = struct.unpack_from('<I', sym_data, TRADE_ORDER_FLAGS_OFFSET)[0]
    
    # Extract volume_min (u64)
    if len(sym_data) >= VOLUME_MIN_OFFSET + 8:
        result['volume_min'] = struct.unpack_from('<Q', sym_data, VOLUME_MIN_OFFSET)[0]
    
    # Extract volume_max (u32)
    if len(sym_data) >= VOLUME_MAX_OFFSET + 4:
        result['volume_max'] = struct.unpack_from('<I', sym_data, VOLUME_MAX_OFFSET)[0]
    
    # Extract volume_step (u32)
    if len(sym_data) >= VOLUME_STEP_OFFSET + 4:
        result['volume_step'] = struct.unpack_from('<I', sym_data, VOLUME_STEP_OFFSET)[0]
    
    return result

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(
        WS_URL, ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin': 'https://15.206.31.153:443'},
    ) as ws:
        print("[+] Connected")

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

        # Get symbol list
        await send_cmd(ws, session_key, 34)
        
        # Drain messages until we get cmd_id=34 (symbol list)
        sym_data = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp:
                    cmd_name = {3:'ACCOUNT', 4:'POSITIONS', 5:'DEALS', 7:'SUBSCRIBE', 
                               19:'TRADE_EVENT', 28:'LOGIN', 34:'SYMBOLS', 42:'NOTIFY', 51:'HEARTBEAT'
                               }.get(resp['cmd_id'], f'CMD_{resp["cmd_id"]}')
                    print(f"  recv cmd={resp['cmd_id']} ({cmd_name}) res_code={resp['res_code']} size={len(resp['res_body'])}")
                    if resp['cmd_id'] == 34:
                        sym_data = resp['res_body']
                        break
            except asyncio.TimeoutError:
                continue
        
        if not sym_data:
            print("[-] No symbol list received!")
            return
        
        print(f"[+] Got symbol data: {len(sym_data)} bytes")
        
        # Debug: dump first 32 bytes
        print(f"  hex: {sym_data[:32].hex()}")
        
        # The response format: might be gzip or raw
        # Check if it's gzip (magic bytes 1f 8b)
        if sym_data[:2] == b'\x1f\x8b':
            print("[+] Data is gzip compressed")
            decomp = zlib.decompress(sym_data)
        else:
            # Try skipping a 4-byte count header
            try:
                decomp = zlib.decompress(sym_data[4:])
            except:
                try:
                    decomp = zlib.decompress(sym_data[4:], -15)
                except:
                    # Maybe it's not compressed?
                    print(f"[-] Cannot decompress, using raw data")
                    decomp = sym_data
        
        count = struct.unpack_from('<I', decomp, 0)[0]
        print(f"[+] Total symbols: {count}")
        
        # Parse all symbols
        symbols = []
        off = 4
        for _ in range(count):
            if off + MH_SIZE > len(decomp): break
            vals, off = parse_series(decomp, MH_SCHEMA, off)
            symbols.append({'name': vals[0], 'id': vals[3], 'digits': vals[2]})
        
        # Target symbols to scan (commonly traded ones)
        target_names = [
            'EURUSDm', 'GBPUSDm', 'USDJPYm', 'AUDUSDm', 'USDCADm',
            'NZDUSDm', 'USDCHFm', 'EURGBPm', 'EURJPYm', 'GBPJPYm',
            'XAUUSDm', 'XAGUSDm', 'BTCUSDm', 'BTCUSD', 'ETHUSDm',
            'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD',
            'EURUSDpro', 'GBPUSDpro', 'USDJPYpro', 'AUDUSDpro',
            'XAUUSD', 'XAUUSDpro', 'BTCUSDpro',
        ]
        
        # Also scan symbols from group "Standard", "Pro", "Raw" etc.
        # Get all unique names matching patterns
        all_names = [s['name'] for s in symbols]
        
        # Build scan list: targets first, then other interesting ones
        scan_list = []
        for name in target_names:
            if name in all_names:
                s = next(x for x in symbols if x['name'] == name)
                scan_list.append(s)
        
        # Add other interesting symbols (indices, commodities)
        interesting_patterns = ['EUR', 'GBP', 'USD', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD',
                               'XAU', 'XAG', 'BTC', 'ETH', 'SILVER', 'GOLD', 'NAS', 'SPX', 'DJ']
        seen = {s['name'] for s in scan_list}
        for s in symbols:
            if s['name'] not in seen:
                for pat in interesting_patterns:
                    if pat in s['name']:
                        scan_list.append(s)
                        seen.add(s['name'])
                        break
        
        # If still not many, just scan first 50
        if len(scan_list) < 20:
            for s in symbols:
                if s['name'] not in seen:
                    scan_list.append(s)
                    seen.add(s['name'])
                    if len(scan_list) >= 50:
                        break
        
        print(f"\n[*] Scanning {len(scan_list)} symbols for trade_mode...\n")
        
        tradeable = []
        disabled = []
        
        for i, s in enumerate(scan_list):
            result = await scan_symbol_spec(ws, session_key, s['id'], s['name'])
            if not result:
                print(f"  [{i+1}/{len(scan_list)}] {s['name']}: FAILED (no response)")
                continue
            
            trade_mode = result.get('trade_mode', -1)
            trade_mode_str = TRADE_MODE_MAP.get(trade_mode, f'UNKNOWN({trade_mode})')
            trade_exemode = result.get('trade_exemode', -1)
            trade_exemode_str = TRADE_EXEMODE_MAP.get(trade_exemode, f'UNKNOWN({trade_exemode})')
            fill_flags = result.get('trade_fill_flags', 0)
            order_flags = result.get('trade_order_flags', 0)
            vol_min = result.get('volume_min', 0)
            vol_max = result.get('volume_max', 0)
            vol_step = result.get('volume_step', 0)
            
            # Convert volume from internal units to lots
            vol_min_lots = vol_min / 100000000 if vol_min else 0
            vol_max_lots = vol_max / 100000000 if vol_max else 0
            vol_step_lots = vol_step / 100000000 if vol_step else 0
            
            status = "TRADEABLE" if trade_mode != 0 else "DISABLED"
            marker = ">>>" if trade_mode != 0 else "   "
            
            print(f"  {marker} [{i+1}/{len(scan_list)}] {s['name']:<12s} "
                  f"id={s['id']:<5d} "
                  f"mode={trade_mode_str:<12s} "
                  f"exec={trade_exemode_str:<10s} "
                  f"fill=0x{fill_flags:02x} "
                  f"order=0x{order_flags:03x} "
                  f"vol_min={vol_min_lots:<8.2f} "
                  f"vol_max={vol_max_lots:<8.2f} "
                  f"vol_step={vol_step_lots:<6.2f} "
                  f"data={result['data_size']}B")
            
            if trade_mode != 0:
                tradeable.append(result)
            else:
                disabled.append(result)
            
            # Small delay to not overwhelm server
            await asyncio.sleep(0.1)
        
        print(f"\n{'='*80}")
        print(f"RESULTS: {len(tradeable)} tradeable, {len(disabled)} disabled out of {len(scan_list)} scanned")
        print(f"{'='*80}")
        
        if tradeable:
            print(f"\nTRADEABLE SYMBOLS:")
            for t in tradeable:
                print(f"  {t['name']:<12s} id={t['id']:<5d} "
                      f"mode={TRADE_MODE_MAP.get(t['trade_mode'], '?'):<12s} "
                      f"exec={TRADE_EXEMODE_MAP.get(t['trade_exemode'], '?'):<10s} "
                      f"fill=0x{t.get('trade_fill_flags',0):02x} "
                      f"order=0x{t.get('trade_order_flags',0):03x} "
                      f"vol_min={t.get('volume_min',0)/100000000:.2f} "
                      f"vol_max={t.get('volume_max',0)/100000000:.2f}")
        else:
            print(f"\nNO TRADEABLE SYMBOLS FOUND!")
            print(f"All symbols have trade_mode=0 (DISABLED).")
            print(f"This may be because the market is closed.")

if __name__ == '__main__':
    asyncio.run(main())