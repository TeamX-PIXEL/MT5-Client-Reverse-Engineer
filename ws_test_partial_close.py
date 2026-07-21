#!/usr/bin/env python3
"""
Partial Close Test v2: Use order ticket directly from TRADE_EVENT.
No reliance on cmd_id=4 position list.
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

def aes_enc(key, pt):
    pad = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad]*pad))

def aes_dec(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

def pack_data(cmd_id, enc):
    return struct.pack('<II', len(enc), 1) + enc

def mk_cmd(cmd_id, payload=b''):
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0,255); cmd[1] = random.randint(0,255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload: cmd[4:4+len(payload)] = payload
    return bytes(cmd)

def parse_resp(data):
    if len(data) < 5: return None
    return {'tag': struct.unpack('<H', data[0:2])[0], 'cmd_id': struct.unpack('<H', data[2:4])[0],
            'res_code': data[4], 'res_body': data[5:]}

def mk_login(login_id, password, url):
    h = bytearray(912)
    pw = password.encode('utf-16-le'); h[4:4+len(pw)] = pw
    struct.pack_into('<I', h, 476, len(url))
    ip = url.encode('utf-16-le'); h[480:480+len(ip)] = ip
    struct.pack_into('<Q', h, 736, login_id)
    return bytes(h)

async def send_cmd(ws, sk, cmd_id, payload=b''):
    await ws.send(pack_data(cmd_id, aes_enc(sk, mk_cmd(cmd_id, payload))))

def mk_trade_op(action_id, sym, vol, digits, trade_type, price, pos_id=0, deviation=50):
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
    """Send trade, wait for TRADE_EVENT. Returns dict with retcode, deal, order, volume, price."""
    rand = random.randint(0, 65535)
    cmd = struct.pack('<HH', rand, 12) + op
    await ws.send(pack_data(12, aes_enc(sk, cmd)))
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            r = parse_resp(aes_dec(sk, raw[8:]))
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
                    if rc != 10002:
                        return {'retcode': rc, 'deal': deal, 'order': order,
                                'volume': vol, 'price': price, 'comment': cmt}
        except asyncio.TimeoutError:
            continue
    return None

RC_NAMES = {0:'OK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOLUME',
            10015:'BAD_PRICE', 10036:'POSITION_NOT_EXISTS', 10030:'BAD_ACTION'}

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        # Auth
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = parse_resp(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        # Login
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")

        # Init
        await send_cmd(ws, sk, 3)
        await asyncio.sleep(0.5)
        await send_cmd(ws, sk, 34)
        await asyncio.sleep(0.5)
        await send_cmd(ws, sk, 7, struct.pack('<II', 1, 426))
        await asyncio.sleep(0.5)

        # Get quote
        bid = ask = None
        for _ in range(20):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
                r = parse_resp(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 8 and len(r['res_body']) >= 28:
                    b = r['res_body']
                    if struct.unpack_from('<I', b, 0)[0] == 426:
                        bid = struct.unpack_from('<d', b, 12)[0] / 100000
                        ask = struct.unpack_from('<d', b, 20)[0] / 100000
                        break
            except: pass
        print(f"[+] Quote: bid={bid:.5f} ask={ask:.5f}")

        # ── STEP 1: Open 0.10 lot BUY ──
        print("\n=== STEP 1: OPEN 0.10 LOTS BUY ===")
        open_lots = 0.10
        open_vol = int(open_lots * 100000000)
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_trade_op(aid, 'EURUSDm', open_vol, 5, 0, ask)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC_NAMES.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f}")
        
        order_ticket = res['order']  # THIS is the position identifier!
        print(f"  Position ORDER ticket: {order_ticket}")
        await asyncio.sleep(3)

        # ── STEP 2: Partial close 0.05 lots using ORDER ticket ──
        print("\n=== STEP 2: PARTIAL CLOSE 0.05 LOTS ===")
        close_lots = 0.05
        close_vol = int(close_lots * 100000000)
        close_type = 1  # SELL to close BUY
        close_price = bid
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_trade_op(aid, 'EURUSDm', close_vol, 5, close_type, close_price, pos_id=order_ticket)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC_NAMES.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} "
              f"vol={res['volume']/100000000:.2f} price={res['price']:.5f}")
        
        if res['retcode'] == 10009:
            print(f"  [OK] Partial close ACCEPTED!")
            print(f"  Closed {close_lots} lots, remaining should be {open_lots - close_lots} lots")
        else:
            print(f"  [FAIL] Partial close failed")

        await asyncio.sleep(3)

        # ── STEP 3: Verify via DEAL HISTORY ──
        print("\n=== STEP 3: VERIFY VIA DEAL HISTORY ===")
        import time as _time
        now = int(_time.time())
        from_sec = now - 300  # last 5 minutes
        payload = struct.pack('<ii', from_sec, now)
        await send_cmd(ws, sk, 5, payload)
        await asyncio.sleep(2)
        for _ in range(20):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
                r = parse_resp(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 5:
                    body = r['res_body']
                    if len(body) >= 4:
                        cnt = struct.unpack_from('<I', body, 0)[0]
                        print(f"  Recent deals: {cnt}")
                        off = 4
                        for i in range(min(cnt, 10)):
                            if off + 356 <= len(body):
                                rr = body[off:off+356]
                                deal_id = struct.unpack_from('<q', rr, 0)[0]
                                order_id = struct.unpack_from('<q', rr, 72+8)[0]
                                sym = rr[88:152].decode('utf-16-le', errors='ignore').rstrip('\x00')
                                action = struct.unpack_from('<I', rr, 152)[0]
                                entry = struct.unpack_from('<I', rr, 156)[0]
                                vol = struct.unpack_from('<Q', rr, 192)[0]
                                profit = struct.unpack_from('<d', rr, 200)[0]
                                # entry: 0=IN, 1=OUT, 2=INOUT, 3=OUT_BY
                                entry_name = {0:'IN', 1:'OUT', 2:'INOUT', 3:'OUT_BY'}.get(entry, str(entry))
                                action_name = {0:'BUY', 1:'SELL'}.get(action, str(action))
                                print(f"    deal={deal_id} order={order_id} {sym} {action_name} "
                                      f"entry={entry_name} vol={vol/100000000:.2f} profit={profit:.2f}")
                            off += 356
                    break
            except asyncio.TimeoutError:
                continue

        # ── STEP 4: Close remaining (0.05 lots) ──
        print("\n=== STEP 4: CLOSE REMAINING (0.05 lots) ===")
        close_vol = int(0.05 * 100000000)
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_trade_op(aid, 'EURUSDm', close_vol, 5, 1, bid, pos_id=order_ticket)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC_NAMES.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']}")

        print("\n=== DONE ===")

if __name__ == '__main__':
    asyncio.run(main())
