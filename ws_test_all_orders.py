#!/usr/bin/env python3
"""
Test all order types: Sell Market, Buy Limit, Sell Limit, Buy Stop, Sell Stop
Uses EURUSDm with trade_action=3 (MARKET) for all.
"""
import asyncio, struct, time, random, ssl
import websockets
from Crypto.Cipher import AES

WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER_IP = "15.206.31.153"
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)
POS_SIZE = 344
DEAL_SIZE = 356

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

def parse_pos(body, off):
    if off + POS_SIZE > len(body): return None
    r = body[off:off+POS_SIZE]
    return {
        'id': struct.unpack_from('<q', r, 0)[0],
        'order': struct.unpack_from('<q', r, 8)[0],
        'symbol': r[24:88].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        'action': struct.unpack_from('<I', r, 88)[0],
        'price_open': struct.unpack_from('<d', r, 92)[0],
        'volume': struct.unpack_from('<Q', r, 124)[0],
        'profit': struct.unpack_from('<d', r, 132)[0],
    }

def mk_op(action_id, sym, vol, digits, trade_type, price, pos_id=0, deviation=50):
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, 3)  # MARKET
    s = sym.encode('utf-16-le'); op[8:8+len(s)] = s
    struct.pack_into('<Q', op, 72, vol)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, 0)  # FOK
    struct.pack_into('<I', op, 100, 0)  # GTC
    struct.pack_into('<I', op, 104, 0)
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<I', op, 144, deviation)
    if pos_id:
        struct.pack_into('<Q', op, 228, pos_id)
    return bytes(op)

async def send_trade(ws, sk, op):
    rand = random.randint(0, 65535)
    cmd = struct.pack('<HH', rand, 12) + op
    await ws.send(pack_data(12, aes_enc(sk, cmd)))
    
    result = None
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
                    vol = struct.unpack_from('<q', ap, 20)[0]
                    price = struct.unpack_from('<d', ap, 28)[0]
                    cmt = ap[64:128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                    result = {'retcode': rc, 'deal': deal, 'order': order,
                              'volume': vol, 'price': price, 'comment': cmt}
                    if rc != 10002:  # Wait for final event
                        return result
        except asyncio.TimeoutError:
            continue
    return result

async def get_positions(ws, sk):
    await sc(ws, sk, 4)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            r = pr(aes_dec(sk, raw[8:]))
            if r and r['cmd_id'] == 4:
                body = r['res_body']
                cnt = struct.unpack_from('<I', body, 0)[0]
                pos = []
                off = 4
                for _ in range(cnt):
                    p = parse_pos(body, off)
                    if p: pos.append(p)
                    off += POS_SIZE
                return pos
        except asyncio.TimeoutError:
            continue
    return []

RC = {0:'OK', 10002:'ACK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOL',
      10015:'BAD_PRICE', 10017:'DISABLED', 10030:'BAD_ACTION'}
TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = pr(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")

        await sc(ws, sk, 34)
        await asyncio.sleep(0.5)
        await sc(ws, sk, 7, struct.pack('<II', 1, 426))

        bid = ask = None
        for _ in range(20):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 8 and len(r['res_body']) >= 28:
                    b = r['res_body']
                    if struct.unpack_from('<I', b, 0)[0] == 426:
                        bid = struct.unpack_from('<d', b, 12)[0] / 100000
                        ask = struct.unpack_from('<d', b, 20)[0] / 100000
                        break
            except: pass
        print(f"[+] Quote: bid={bid:.5f} ask={ask:.5f}\n")

        vol = 1000000  # 0.01 lots

        # =========================================================
        # TEST 1: SELL MARKET
        # =========================================================
        print("="*60)
        print("TEST 1: SELL MARKET (trade_action=3, type=1)")
        print("="*60)
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 'EURUSDm', vol, 5, 1, bid)  # SELL, price=BID
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f} "
              f"comment='{res['comment']}'")
        await asyncio.sleep(2)

        # =========================================================
        # TEST 2: BUY LIMIT (price below current)
        # =========================================================
        print(f"\n{'='*60}")
        print("TEST 2: BUY LIMIT (trade_action=3, type=2)")
        print(f"{'='*60}")
        limit_price = round(bid - 0.00500, 5)  # 50 pips below
        print(f"  Setting limit at {limit_price:.5f} (current bid={bid:.5f})")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 'EURUSDm', vol, 5, 2, limit_price)  # BUY LIMIT
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f} "
              f"comment='{res['comment']}'")
        await asyncio.sleep(2)

        # =========================================================
        # TEST 3: SELL LIMIT (price above current)
        # =========================================================
        print(f"\n{'='*60}")
        print("TEST 3: SELL LIMIT (trade_action=3, type=3)")
        print(f"{'='*60}")
        limit_price = round(ask + 0.00500, 5)  # 50 pips above
        print(f"  Setting limit at {limit_price:.5f} (current ask={ask:.5f})")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 'EURUSDm', vol, 5, 3, limit_price)  # SELL LIMIT
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f} "
              f"comment='{res['comment']}'")
        await asyncio.sleep(2)

        # =========================================================
        # TEST 4: BUY STOP (price above current)
        # =========================================================
        print(f"\n{'='*60}")
        print("TEST 4: BUY STOP (trade_action=3, type=4)")
        print(f"{'='*60}")
        stop_price = round(ask + 0.00500, 5)  # 50 pips above
        print(f"  Setting stop at {stop_price:.5f} (current ask={ask:.5f})")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 'EURUSDm', vol, 5, 4, stop_price)  # BUY STOP
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f} "
              f"comment='{res['comment']}'")
        await asyncio.sleep(2)

        # =========================================================
        # TEST 5: SELL STOP (price below current)
        # =========================================================
        print(f"\n{'='*60}")
        print("TEST 5: SELL STOP (trade_action=3, type=5)")
        print(f"{'='*60}")
        stop_price = round(bid - 0.00500, 5)  # 50 pips below
        print(f"  Setting stop at {stop_price:.5f} (current bid={bid:.5f})")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 'EURUSDm', vol, 5, 5, stop_price)  # SELL STOP
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f} "
              f"comment='{res['comment']}'")
        await asyncio.sleep(2)

        # =========================================================
        # CHECK POSITIONS & ORDERS
        # =========================================================
        print(f"\n{'='*60}")
        print("FINAL STATE")
        print(f"{'='*60}")
        positions = await get_positions(ws, sk)
        print(f"  Open positions: {len(positions)}")
        for p in positions:
            d = 'BUY' if p['action'] == 0 else 'SELL'
            print(f"    #{p['id']} {p['symbol']} {d} {p['volume']/100000000:.2f} lots "
                  f"@ {p['price_open']:.5f} P/L={p['profit']:.2f}")

        # Check deal history for today
        now = int(time.time())
        await sc(ws, sk, 5, struct.pack('<II', now - 3600, now))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 5:
                    body = r['res_body']
                    cnt = struct.unpack_from('<I', body, 0)[0]
                    print(f"\n  Recent deals: {cnt}")
                    off = 4
                    for i in range(min(cnt, 10)):
                        if off + DEAL_SIZE > len(body): break
                        rec = body[off:off+DEAL_SIZE]
                        deal_id = struct.unpack_from('<q', rec, 0)[0]
                        sym = rec[88:152].decode('utf-16-le', errors='ignore').split('\x00')[0]
                        action = struct.unpack_from('<I', rec, 152)[0]
                        entry = struct.unpack_from('<I', rec, 156)[0]
                        vol_r = struct.unpack_from('<Q', rec, 192)[0]
                        price = struct.unpack_from('<d', rec, 160)[0]
                        profit = struct.unpack_from('<d', rec, 200)[0]
                        entry_s = {0:'IN',1:'OUT'}.get(entry, entry)
                        print(f"    #{deal_id} {sym} {TYPES.get(action,'?')} {entry_s} "
                              f"{vol_r/100000000:.2f} lots @ {price:.5f} P/L={profit:.2f}")
                        off += DEAL_SIZE
                    break
            except asyncio.TimeoutError:
                continue

        print(f"\n{'='*60}")
        print("ALL TESTS COMPLETE")

if __name__ == '__main__':
    asyncio.run(main())