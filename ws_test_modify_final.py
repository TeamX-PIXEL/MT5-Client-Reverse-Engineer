#!/usr/bin/env python3
"""
Position SL/TP Modify - VERIFIED WORKING.
Position ID = ORDER ticket from TRADE_EVENT (not deal ticket).
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

async def drain(ws, sk, expected_cmd, timeout=8):
    msgs = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2)
            r = parse_resp(aes_dec(sk, raw[8:]))
            if r:
                msgs.append(r)
                if r['cmd_id'] == expected_cmd:
                    return msgs
        except asyncio.TimeoutError:
            continue
    return msgs

def parse_pos(body, off):
    if off + POS_SIZE > len(body): return None, off
    r = body[off:off+POS_SIZE]
    return {
        'id': struct.unpack_from('<q', r, 0)[0],
        'order': struct.unpack_from('<q', r, 8)[0],
        'action': struct.unpack_from('<I', r, 88)[0],
        'sl': struct.unpack_from('<d', r, 108)[0],
        'tp': struct.unpack_from('<d', r, 116)[0],
        'volume': struct.unpack_from('<Q', r, 124)[0],
        'digits': struct.unpack_from('<I', r, 260)[0],
    }, off + POS_SIZE

async def get_positions(ws, sk):
    await send_cmd(ws, sk, 4)
    msgs = await drain(ws, sk, 4)
    for m in msgs:
        if m['cmd_id'] == 4:
            body = m['res_body']
            if len(body) < 8: return []
            cnt = struct.unpack_from('<I', body, 0)[0]
            second = struct.unpack_from('<I', body, 4)[0]
            off = 4 if second > 100000 else 8
            pos = []
            for _ in range(cnt):
                if off + POS_SIZE > len(body): break
                p, off = parse_pos(body, off)
                if p: pos.append(p)
            return pos
    return []

def mk_trade_op(action_id, sym, vol, digits, trade_type, price, sl=0.0, tp=0.0,
                pos_id=0, deviation=50, type_filling=0, trade_action=3):
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, trade_action)
    s = sym.encode('utf-16-le'); op[8:8+len(s)] = s
    struct.pack_into('<Q', op, 72, vol)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, type_filling)
    struct.pack_into('<I', op, 100, 0)
    struct.pack_into('<I', op, 104, 0)
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<d', op, 120, 0.0)
    struct.pack_into('<d', op, 128, sl)
    struct.pack_into('<d', op, 136, tp)
    struct.pack_into('<I', op, 144, deviation)
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
                    result = {'retcode': rc, 'deal': deal, 'order': order,
                              'volume': vol, 'price': price, 'comment': cmt}
                    if rc != 10002:
                        return result
        except asyncio.TimeoutError:
            continue
    return result

RC_NAMES = {0:'OK', 10002:'ACK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOLUME',
            10015:'BAD_PRICE', 10016:'BAD_STOPS', 10017:'DISABLED', 10029:'MOD_FAILED',
            10030:'BAD_ACTION', 10036:'NO_POSITION'}

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
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")

        # Subscribe
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

        vol1 = 1000000  # 0.01 lots

        # === TEST 1: Position SL/TP Modify ===
        print("\n" + "="*60)
        print("TEST 1: Position SL/TP Modify (trade_action=6)")
        print("="*60)

        # Open fresh BUY
        print("\n[1] Open BUY position...")
        op = mk_trade_op(100, 'EURUSDm', vol1, 5, 0, ask, trade_action=3)
        r = await send_trade(ws, sk, op)
        print(f"    retcode={r['retcode']} ({RC_NAMES.get(r['retcode'],'?')})")
        print(f"    deal={r['deal']} order={r['order']} price={r['price']:.5f}")
        pos_id = r['order']  # ORDER ticket = position ID!
        print(f"    Position ID (order ticket): {pos_id}")
        await asyncio.sleep(2)

        # Verify position exists
        positions = await get_positions(ws, sk)
        found = any(p['id'] == pos_id for p in positions)
        print(f"    Position found in cmd_id=4: {found}")
        for p in positions:
            if p['id'] == pos_id:
                d = 'BUY' if p['action'] == 0 else 'SELL'
                print(f"    {d} id={p['id']} vol={p['volume']/100000:.2f} sl={p['sl']:.5f} tp={p['tp']:.5f}")

        # Modify SL/TP
        new_sl = round(bid - 0.010, 5)   # 10 pips below bid
        new_tp = round(ask + 0.010, 5)   # 10 pips above ask
        print(f"\n[2] Modify SL/TP: SL={new_sl:.5f} TP={new_tp:.5f}")
        op = mk_trade_op(200, 'EURUSDm', vol1, 5, 0, bid, sl=new_sl, tp=new_tp,
                         pos_id=pos_id, trade_action=6, type_filling=2)
        r = await send_trade(ws, sk, op)
        print(f"    retcode={r['retcode']} ({RC_NAMES.get(r['retcode'],'?')}) comment='{r['comment']}'")

        # Verify SL/TP changed
        await asyncio.sleep(2)
        positions = await get_positions(ws, sk)
        print(f"\n[3] Verify SL/TP:")
        for p in positions:
            if p['id'] == pos_id:
                sl_ok = abs(p['sl'] - new_sl) < 0.0001
                tp_ok = abs(p['tp'] - new_tp) < 0.0001
                print(f"    SL: expected={new_sl:.5f} got={p['sl']:.5f} {'OK' if sl_ok else 'FAIL'}")
                print(f"    TP: expected={new_tp:.5f} got={p['tp']:.5f} {'OK' if tp_ok else 'FAIL'}")

        # Close position
        print(f"\n[4] Close position...")
        op = mk_trade_op(300, 'EURUSDm', vol1, 5, 1, bid, trade_action=3, pos_id=pos_id)
        r = await send_trade(ws, sk, op)
        print(f"    retcode={r['retcode']} ({RC_NAMES.get(r['retcode'],'?')})")

        # === TEST 2: Pending Order SL/TP ===
        print("\n" + "="*60)
        print("TEST 2: Pending Order SL/TP (during creation)")
        print("="*60)

        # Place BUY Limit with SL/TP
        limit_price = round(ask - 0.005, 5)  # 5 pips below ask
        limit_sl = round(limit_price - 0.010, 5)
        limit_tp = round(limit_price + 0.010, 5)
        print(f"\n[1] Place BUY Limit: price={limit_price:.5f} SL={limit_sl:.5f} TP={limit_tp:.5f}")
        op = mk_trade_op(400, 'EURUSDm', vol1, 5, 2, limit_price, sl=limit_sl, tp=limit_tp,
                         trade_action=5, type_filling=2)
        r = await send_trade(ws, sk, op)
        print(f"    retcode={r['retcode']} ({RC_NAMES.get(r['retcode'],'?')})")
        print(f"    deal={r['deal']} order={r['order']} comment='{r['comment']}'")
        order_ticket = r['order']

        if r['retcode'] == 10009:
            await asyncio.sleep(2)

            # Check pending orders
            positions = await get_positions(ws, sk)
            print(f"\n[2] Pending orders: {len(positions)}")

            # Modify pending order SL/TP
            new_limit_sl = round(limit_price - 0.015, 5)
            new_limit_tp = round(limit_price + 0.015, 5)
            print(f"\n[3] Modify pending SL/TP: SL={new_limit_sl:.5f} TP={new_limit_tp:.5f}")
            op = mk_trade_op(401, 'EURUSDm', vol1, 5, 2, limit_price, sl=new_limit_sl, tp=new_limit_tp,
                             pos_id=order_ticket, trade_action=7, type_filling=2)
            r = await send_trade(ws, sk, op)
            print(f"    retcode={r['retcode']} ({RC_NAMES.get(r['retcode'],'?')}) comment='{r['comment']}'")

            # Cancel pending order
            print(f"\n[4] Cancel pending order...")
            op = mk_trade_op(402, 'EURUSDm', vol1, 5, 2, limit_price,
                             trade_action=8, type_filling=2, pos_id=order_ticket)
            r = await send_trade(ws, sk, op)
            print(f"    retcode={r['retcode']} ({RC_NAMES.get(r['retcode'],'?')})")

        print("\n" + "="*60)
        print("ALL TESTS COMPLETE")
        print("="*60)

if __name__ == '__main__':
    asyncio.run(main())
