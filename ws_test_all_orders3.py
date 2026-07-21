#!/usr/bin/env python3
"""
Test pending orders with trade_action=5 (TRADE_ACTION_PENDING).
From JS: orderOpen sets trade_action = 5 for pending types.
type_filling = 2 (RETURN), type_flags = 2.
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

def mk_op(action_id, trade_action, sym, vol, digits, trade_type, price_order,
          sl=0.0, tp=0.0, pos_id=0, type_filling=2, type_flags=2):
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, trade_action)
    s = sym.encode('utf-16-le'); op[8:8+len(s)] = s
    struct.pack_into('<Q', op, 72, vol)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<I', op, 84, 0)  # trade_order = 0
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, type_filling)  # 2 = RETURN
    struct.pack_into('<I', op, 100, 0)  # type_time = GTC
    struct.pack_into('<I', op, 104, type_flags)  # 2
    struct.pack_into('<I', op, 108, 0)  # type_reason = 0
    struct.pack_into('<d', op, 112, price_order)
    struct.pack_into('<d', op, 120, 0.0)  # price_trigger = 0
    struct.pack_into('<d', op, 128, sl)
    struct.pack_into('<d', op, 136, tp)
    struct.pack_into('<I', op, 144, 0)  # deviation = 0
    struct.pack_into('<d', op, 148, 0.0)  # price_top
    struct.pack_into('<d', op, 156, 0.0)  # price_bottom
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
                    if rc != 10002:
                        return result
        except asyncio.TimeoutError:
            continue
    return result

RC = {0:'OK', 10002:'ACK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOL',
      10015:'BAD_PRICE', 10016:'BAD_STOPS', 10017:'DISABLED', 10030:'BAD_ACTION'}
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
        print(f"[+] Quote: bid={bid:.5f} ask={ask:.5f}")

        POINT = 0.00001
        OFFSET = 100 * POINT
        vol = 1000000  # 0.01 lots
        print(f"[+] 100*_Point = {OFFSET:.5f}\n")

        results = []

        # =========================================================
        # TEST 1: BUY LIMIT — Ask - 100*_Point, trade_action=5
        # =========================================================
        price = round(ask - OFFSET, 5)
        print(f"{'='*60}")
        print(f"TEST 1: BUY LIMIT  action=5 type=2 price={price:.5f}")
        print(f"{'='*60}")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 5, 'EURUSDm', vol, 5, 2, price)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} comment='{res['comment']}'")
        results.append(('BUY_LIMIT', res))
        await asyncio.sleep(2)

        # =========================================================
        # TEST 2: SELL LIMIT — Bid + 100*_Point, trade_action=5
        # =========================================================
        price = round(bid + OFFSET, 5)
        print(f"\n{'='*60}")
        print(f"TEST 2: SELL LIMIT  action=5 type=3 price={price:.5f}")
        print(f"{'='*60}")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 5, 'EURUSDm', vol, 5, 3, price)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} comment='{res['comment']}'")
        results.append(('SELL_LIMIT', res))
        await asyncio.sleep(2)

        # =========================================================
        # TEST 3: BUY STOP — Ask + 100*_Point, trade_action=5
        # =========================================================
        price = round(ask + OFFSET, 5)
        print(f"\n{'='*60}")
        print(f"TEST 3: BUY STOP  action=5 type=4 price={price:.5f}")
        print(f"{'='*60}")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 5, 'EURUSDm', vol, 5, 4, price)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} comment='{res['comment']}'")
        results.append(('BUY_STOP', res))
        await asyncio.sleep(2)

        # =========================================================
        # TEST 4: SELL STOP — Bid - 100*_Point, trade_action=5
        # =========================================================
        price = round(bid - OFFSET, 5)
        print(f"\n{'='*60}")
        print(f"TEST 4: SELL STOP  action=5 type=5 price={price:.5f}")
        print(f"{'='*60}")
        aid = random.randint(1, 0x7FFFFFFE)
        op = mk_op(aid, 5, 'EURUSDm', vol, 5, 5, price)
        res = await send_trade(ws, sk, op)
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
              f"deal={res['deal']} order={res['order']} comment='{res['comment']}'")
        results.append(('SELL_STOP', res))
        await asyncio.sleep(2)

        # =========================================================
        # CHECK ALL PENDING ORDERS via cmd_id=4
        # =========================================================
        print(f"\n{'='*60}")
        print("PENDING ORDERS & POSITIONS")
        print(f"{'='*60}")
        await sc(ws, sk, 4)
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 4:
                    body = r['res_body']
                    pos_cnt = struct.unpack_from('<I', body, 0)[0]
                    off = 4
                    print(f"  Open positions: {pos_cnt}")
                    for _ in range(pos_cnt):
                        if off + 344 > len(body): break
                        rec = body[off:off+344]
                        pid = struct.unpack_from('<q', rec, 0)[0]
                        sym = rec[24:88].decode('utf-16-le', errors='ignore').split('\x00')[0]
                        action = struct.unpack_from('<I', rec, 88)[0]
                        price = struct.unpack_from('<d', rec, 92)[0]
                        vol_r = struct.unpack_from('<Q', rec, 124)[0]
                        d = 'BUY' if action == 0 else 'SELL'
                        print(f"    #{pid} {d} {sym} {vol_r/100000000:.2f} lots @ {price:.5f}")
                        off += 344

                    ord_cnt = struct.unpack_from('<I', body, off)[0]
                    off += 4
                    print(f"  Pending orders: {ord_cnt}")
                    # ORDER_SCHEMA: trade_order(i64)+order_id(UTF16,64B)+trade_symbol(UTF16,64B)+...
                    for i in range(ord_cnt):
                        if off + 356 > len(body): break
                        rec = body[off:off+356]
                        oid = struct.unpack_from('<q', rec, 0)[0]
                        sym = rec[8+64:8+64+64].decode('utf-16-le', errors='ignore').split('\x00')[0]
                        time_setup = struct.unpack_from('<I', rec, 8+64+64)[0]
                        time_exp = struct.unpack_from('<I', rec, 8+64+64+4)[0]
                        time_done = struct.unpack_from('<I', rec, 8+64+64+8)[0]
                        otype = struct.unpack_from('<I', rec, 8+64+64+12)[0]
                        ofilling = struct.unpack_from('<I', rec, 8+64+64+16)[0]
                        otime = struct.unpack_from('<I', rec, 8+64+64+20)[0]
                        oreason = struct.unpack_from('<I', rec, 8+64+64+24)[0]
                        oprice = struct.unpack_from('<d', rec, 8+64+64+28)[0]
                        otrigger = struct.unpack_from('<d', rec, 8+64+64+36)[0]
                        ocurrent = struct.unpack_from('<d', rec, 8+64+64+44)[0]
                        ovol_i = struct.unpack_from('<q', rec, 8+64+64+52)[0]
                        ovol_c = struct.unpack_from('<q', rec, 8+64+64+60)[0]
                        ostate = struct.unpack_from('<I', rec, 8+64+64+68)[0]
                        state_name = {0:'started',2:'canceled',4:'filled',5:'rejected'}.get(ostate, ostate)
                        print(f"    #{oid} {sym} {TYPES.get(otype,otype)} "
                              f"price={oprice:.5f} state={state_name} "
                              f"vol={ovol_i/100000000:.2f}")
                        off += 356
                    break
            except asyncio.TimeoutError:
                continue

        # =========================================================
        # SUMMARY
        # =========================================================
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for label, res in results:
            print(f"  {label:12s} retcode={res['retcode']}({RC.get(res['retcode'],'?')}) "
                  f"order={res['order']} comment='{res['comment']}'")

if __name__ == '__main__':
    asyncio.run(main())