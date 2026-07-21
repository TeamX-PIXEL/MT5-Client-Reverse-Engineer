#!/usr/bin/env python3
"""
Multi-symbol trading - try different volumes for each symbol.
"""
import asyncio, struct, time, random, ssl, zlib
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

RC = {0:'OK', 10002:'ACK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOL',
      10015:'BAD_PRICE', 10016:'BAD_STOPS', 10017:'DISABLED', 10023:'UNKNOWN',
      10030:'BAD_ACTION', 10036:'NO_POSITION'}

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

# Ac.parse schema types
TYPE_UINT32 = 6
TYPE_UINT16 = 5
TYPE_UTF16 = 11

MH_SCHEMA = [
    {'type': TYPE_UTF16, 'length': 128},   # name
    {'type': TYPE_UTF16, 'length': 64},    # description
    {'type': TYPE_UINT32},                  # digits
    {'type': TYPE_UINT32},                  # symbol_id
    {'type': TYPE_UTF16, 'length': 192},   # path
    {'type': TYPE_UINT32},                  # trade_calc_mode
    {'type': TYPE_UTF16, 'length': 128},   # basis
    {'type': TYPE_UINT16},                  # sector
]

def ac_parse(buf, schema, offset=0):
    vals = []
    for field in schema:
        t = field['type']
        pl = field.get('length', 0)
        if t == TYPE_UINT32:
            vals.append(struct.unpack_from('<I', buf, offset)[0])
            offset += 4
        elif t == TYPE_UINT16:
            vals.append(struct.unpack_from('<H', buf, offset)[0])
            offset += 2
        elif t == TYPE_UTF16:
            raw = buf[offset:offset+pl]
            s = raw.decode('utf-16-le', errors='ignore').split('\x00')[0]
            vals.append(s)
            offset += pl
    return vals, offset

async def get_symbols(ws, sk):
    await sc(ws, sk, 34)
    r = await recv_cmd(ws, sk, 34, timeout=10)
    if not r:
        return {}
    body = r['res_body']
    decompressed = zlib.decompress(body[4:])
    count = struct.unpack_from('<I', decompressed, 0)[0]
    symbols = {}
    off = 4
    for _ in range(count):
        vals, off = ac_parse(decompressed, MH_SCHEMA, off)
        name = vals[0]
        if name:
            symbols[name] = {
                'name': name, 'description': vals[1], 'digits': vals[2],
                'id': vals[3], 'path': vals[4], 'trade_calc_mode': vals[5],
            }
    return symbols

async def get_quotes(ws, sk, symbol_ids, timeout=3):
    payload = struct.pack('<I', len(symbol_ids))
    for sid in symbol_ids:
        payload += struct.pack('<I', sid)
    await sc(ws, sk, 7, payload)
    quotes = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1)
            r = pr(aes_dec(sk, raw[8:]))
            if r and r['cmd_id'] == 8 and len(r['res_body']) >= 28:
                b = r['res_body']
                sid = struct.unpack_from('<I', b, 0)[0]
                if sid in symbol_ids:
                    bid = struct.unpack_from('<d', b, 12)[0]
                    ask = struct.unpack_from('<d', b, 20)[0]
                    quotes[sid] = {'bid': bid, 'ask': ask}
        except asyncio.TimeoutError:
            continue
    return quotes

async def send_trade(ws, sk, op):
    rand = random.randint(0, 65535)
    cmd = struct.pack('<HH', rand, 12) + op
    await ws.send(pack_data(12, aes_enc(sk, cmd)))
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            r = pr(aes_dec(sk, raw[8:]))
            if r and r['cmd_id'] == 19:
                body = r['res_body']
                if len(body) >= 4 + 248 + 128:
                    ap = body[4+248:4+248+128]
                    rc = struct.unpack_from('<I', ap, 0)[0]
                    deal = struct.unpack_from('<q', ap, 4)[0]
                    order = struct.unpack_from('<q', ap, 12)[0]
                    vol_raw = struct.unpack_from('<q', ap, 20)[0]
                    price_raw = struct.unpack_from('<d', ap, 28)[0]
                    cmt = ap[64:128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                    if rc != 10002:
                        return {'retcode': rc, 'deal': deal, 'order': order, 
                                'volume': vol_raw, 'price': price_raw, 'comment': cmt}
        except asyncio.TimeoutError:
            continue
    return {'retcode': -1, 'deal': 0, 'order': 0, 'comment': 'timeout'}

async def close_position(ws, sk, symbol, pos_type, order_ticket, volume, price, digits):
    aid = random.randint(1, 0x7FFFFFFE)
    op = bytearray(248)
    struct.pack_into('<I', op, 0, aid)
    struct.pack_into('<I', op, 4, 3)
    s = symbol.encode('utf-16-le'); op[8:8+len(s)] = s
    struct.pack_into('<Q', op, 72, volume)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<Q', op, 84, 0)
    struct.pack_into('<I', op, 92, 1 - pos_type)
    struct.pack_into('<I', op, 96, 0)
    struct.pack_into('<I', op, 100, 0)
    struct.pack_into('<I', op, 104, 2)
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<Q', op, 228, order_ticket)
    return await send_trade(ws, sk, bytes(op))

async def get_positions(ws, sk):
    """Parse positions with correct offsets from hex dump"""
    await sc(ws, sk, 4)
    r = await recv_cmd(ws, sk, 4, timeout=5)
    if not r:
        return []
    body = r['res_body']
    pos_cnt = struct.unpack_from('<I', body, 0)[0]
    off = 4
    positions = []
    for _ in range(pos_cnt):
        if off + 344 > len(body): break
        rec = body[off:off+344]
        # Correct offsets from hex dump analysis:
        # @0: pos_id(i64), @8: trade_order(i64), @16: time(u32)+padding(u32)
        # @24: symbol(UTF-16LE 128B), @152: action(u32)
        # @156: price_open(f64), @164: price_close(f64)
        # @172: sl(f64), @180: tp(f64), @188: volume(i64)
        # @196: profit(f64)
        # Wait, symbol is 128B starting at @24, ends at @152
        pos = {
            'pos_id': struct.unpack_from('<q', rec, 0)[0],
            'order_id': struct.unpack_from('<q', rec, 8)[0],
            'symbol': rec[24:24+128].decode('utf-16-le', errors='ignore').split('\x00')[0],
            'action': struct.unpack_from('<I', rec, 152)[0],
            'price_open': struct.unpack_from('<d', rec, 156)[0],
            'sl': struct.unpack_from('<d', rec, 172)[0],
            'tp': struct.unpack_from('<d', rec, 180)[0],
            'volume': struct.unpack_from('<q', rec, 188)[0],
            'profit': struct.unpack_from('<d', rec, 196)[0],
            'digits': struct.unpack_from('<I', rec, 260)[0],
        }
        positions.append(pos)
        off += 344
    return positions

# Known minimum volumes (in internal units = lots × 100000000)
VOLUME_MAP = {
    'BTCUSDm': 100000000,   # 1 lot minimum for crypto
    'XAUUSDm': 10000000,    # 0.1 lot for gold
    'ETHUSDm': 10000000,    # 0.1 lot for ETH
    'GBPUSDm': 1000000,     # 0.01 lots
    'USDJPYm': 1000000,     # 0.01 lots
    'EURUSDm': 1000000,     # 0.01 lots
}

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = pr(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")
        
        symbols = await get_symbols(ws, sk)
        print(f"[+] Found {len(symbols)} symbols")
        
        targets = ['BTCUSDm', 'XAUUSDm', 'ETHUSDm', 'GBPUSDm', 'USDJPYm', 'EURUSDm']
        found = {}
        for name in targets:
            if name in symbols:
                found[name] = symbols[name]
        
        symbol_ids = [s['id'] for s in found.values()]
        quotes = await get_quotes(ws, sk, symbol_ids, timeout=5)
        
        for name, info in found.items():
            if info['id'] in quotes:
                q = quotes[info['id']]
                divisor = 10 ** info['digits']
                bid = q['bid'] / divisor
                ask = q['ask'] / divisor
                vol = VOLUME_MAP.get(name, 1000000)
                print(f"  {name}: bid={bid:.{info['digits']}f} ask={ask:.{info['digits']}f} vol={vol/100000000}")
                found[name]['bid'] = bid
                found[name]['ask'] = ask
                found[name]['vol'] = vol
        
        print(f"\n{'='*60}")
        print("Place MARKET BUY on each symbol")
        print(f"{'='*60}")
        
        orders_placed = []
        for name, info in found.items():
            if 'bid' not in info:
                continue
            
            vol = info['vol']
            
            aid = random.randint(1, 0x7FFFFFFE)
            op = bytearray(248)
            struct.pack_into('<I', op, 0, aid)
            struct.pack_into('<I', op, 4, 3)
            s = name.encode('utf-16-le'); op[8:8+len(s)] = s
            struct.pack_into('<Q', op, 72, vol)
            struct.pack_into('<I', op, 80, info['digits'])
            struct.pack_into('<Q', op, 84, 0)
            struct.pack_into('<I', op, 92, 0)
            struct.pack_into('<I', op, 96, 0)
            struct.pack_into('<I', op, 100, 0)
            struct.pack_into('<I', op, 104, 2)
            struct.pack_into('<d', op, 112, info['ask'])
            
            res = await send_trade(ws, sk, bytes(op))
            print(f"  {name}: retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
                  f"deal={res['deal']} order={res['order']} comment='{res['comment']}'")
            
            if res['order'] > 0:
                orders_placed.append({
                    'symbol': name, 'type': 0, 'ticket': res['order'],
                    'volume': vol, 'digits': info['digits']
                })
            
            await asyncio.sleep(0.5)
        
        await asyncio.sleep(2)
        
        print(f"\n{'='*60}")
        print("Check positions")
        print(f"{'='*60}")
        positions = await get_positions(ws, sk)
        print(f"  Open positions: {len(positions)}")
        for pos in positions:
            divisor = 10 ** pos['digits']
            print(f"    #{pos['order_id']} {pos['symbol']} "
                  f"{'BUY' if pos['action']==0 else 'SELL'} "
                  f"vol={pos['volume']/100000000:.4f} "
                  f"price={pos['price_open']/divisor:.{pos['digits']}f} "
                  f"profit={pos['profit']:.2f}")
        
        print(f"\n{'='*60}")
        print("Close all positions")
        print(f"{'='*60}")
        
        await get_quotes(ws, sk, symbol_ids, timeout=2)
        
        for pos in orders_placed:
            sym = pos['symbol']
            if sym in found and 'bid' in found[sym]:
                res = await close_position(ws, sk, sym, pos['type'], pos['ticket'],
                                          pos['volume'], found[sym]['bid'], pos['digits'])
                print(f"  Close {sym}: retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
                      f"comment='{res['comment']}'")
            await asyncio.sleep(0.5)
        
        await asyncio.sleep(1)
        positions2 = await get_positions(ws, sk)
        print(f"\n  Final positions: {len(positions2)}")
        for pos in positions2:
            divisor = 10 ** pos['digits']
            print(f"    #{pos['order_id']} {pos['symbol']} "
                  f"{'BUY' if pos['action']==0 else 'SELL'} "
                  f"vol={pos['volume']/100000000:.4f} "
                  f"price={pos['price_open']/divisor:.{pos['digits']}f}")
        
        print("\n[+] Done")

if __name__ == '__main__':
    asyncio.run(main())
