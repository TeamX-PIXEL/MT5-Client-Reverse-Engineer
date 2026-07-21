#!/usr/bin/env python3
"""
Complete STOP-LIMIT order test:
- BUY STOP LIMIT (type=6): Stop above, Limit <= Stop
- SELL STOP LIMIT (type=7): Stop below, Limit >= Stop
- Modify SL/TP
- Cancel
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

RC = {0:'OK', 10002:'ACK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOL',
      10015:'BAD_PRICE', 10016:'BAD_STOPS', 10017:'DISABLED', 10023:'UNKNOWN',
      10030:'BAD_ACTION', 10036:'NO_POSITION'}
TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP',
         6:'BUY_STOP_LIMIT', 7:'SELL_STOP_LIMIT', 8:'CANCEL'}

def parse_order(rec):
    return {
        'ticket': struct.unpack_from('<q', rec, 0)[0],
        'symbol': rec[72:72+128].decode('utf-16-le', errors='ignore').split('\x00')[0],
        'type': struct.unpack_from('<I', rec, 148)[0],
        'price': struct.unpack_from('<d', rec, 164)[0],
        'price_trigger': struct.unpack_from('<d', rec, 172)[0],
        'sl': struct.unpack_from('<d', rec, 188)[0],
        'tp': struct.unpack_from('<d', rec, 196)[0],
        'state': struct.unpack_from('<I', rec, 220)[0],
    }

async def get_pending_orders(ws, sk):
    await sc(ws, sk, 4)
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            r = pr(aes_dec(sk, raw[8:]))
            if r and r['cmd_id'] == 4:
                body = r['res_body']
                pos_cnt = struct.unpack_from('<I', body, 0)[0]
                off = 4 + pos_cnt * 344
                ord_cnt = struct.unpack_from('<I', body, off)[0]
                off += 4
                orders = []
                for _ in range(ord_cnt):
                    if off + 356 > len(body): break
                    orders.append(parse_order(body[off:off+356]))
                    off += 356
                return orders
        except asyncio.TimeoutError:
            continue

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
                    cmt = ap[64:128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                    result = {'retcode': rc, 'deal': deal, 'order': order, 'comment': cmt}
                    if rc != 10002:
                        return result
        except asyncio.TimeoutError:
            continue
    return result

async def place_stoplimit(ws, sk, trade_type, stop, limit, sl=0.0, tp=0.0):
    """Place stop-limit order. trade_type: 6=BUY, 7=SELL"""
    aid = random.randint(1, 0x7FFFFFFE)
    op = bytearray(248)
    struct.pack_into('<I', op, 0, aid)
    struct.pack_into('<I', op, 4, 5)  # PENDING
    s = 'EURUSDm'.encode('utf-16-le'); op[8:8+len(s)] = s
    struct.pack_into('<Q', op, 72, 1000000)
    struct.pack_into('<I', op, 80, 5)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, 2)  # RETURN
    struct.pack_into('<I', op, 100, 0)
    struct.pack_into('<I', op, 104, 2)
    struct.pack_into('<d', op, 112, stop)    # stop price
    struct.pack_into('<d', op, 120, limit)   # limit price
    struct.pack_into('<d', op, 128, sl)
    struct.pack_into('<d', op, 136, tp)
    return await send_trade(ws, sk, bytes(op))

async def cancel_order(ws, sk, ticket, trade_type):
    aid = random.randint(1, 0x7FFFFFFE)
    op = bytearray(248)
    struct.pack_into('<I', op, 0, aid)
    struct.pack_into('<I', op, 4, 8)  # CANCEL
    s = 'EURUSDm'.encode('utf-16-le'); op[8:8+len(s)] = s
    struct.pack_into('<Q', op, 72, 1000000)
    struct.pack_into('<I', op, 80, 5)
    struct.pack_into('<Q', op, 84, ticket)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, 2)
    return await send_trade(ws, sk, bytes(op))

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
        
        await sc(ws, sk, 34); await asyncio.sleep(0.5)
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
        
        # =========================================
        print(f"\n{'='*60}")
        print("TEST 1: BUY STOP LIMIT - Place with SL/TP")
        print(f"{'='*60}")
        buy_stop = round(ask + 200*POINT, 5)
        buy_limit = round(buy_stop - 10*POINT, 5)  # Limit <= Stop for BUY
        buy_sl = round(buy_stop - 100*POINT, 5)
        buy_tp = round(buy_stop + 200*POINT, 5)
        print(f"  Stop={buy_stop:.5f} Limit={buy_limit:.5f}")
        print(f"  SL={buy_sl:.5f} TP={buy_tp:.5f}")
        
        res = await place_stoplimit(ws, sk, 6, buy_stop, buy_limit, buy_sl, buy_tp)
        buy_ticket = res['order']
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) order={buy_ticket}")
        
        await asyncio.sleep(1)
        
        # =========================================
        print(f"\n{'='*60}")
        print("TEST 2: SELL STOP LIMIT - Place with SL/TP")
        print(f"{'='*60}")
        sell_stop = round(bid - 200*POINT, 5)
        sell_limit = round(sell_stop + 10*POINT, 5)  # Limit >= Stop for SELL
        sell_sl = round(sell_stop + 100*POINT, 5)
        sell_tp = round(sell_stop - 200*POINT, 5)
        print(f"  Stop={sell_stop:.5f} Limit={sell_limit:.5f}")
        print(f"  SL={sell_sl:.5f} TP={sell_tp:.5f}")
        
        res = await place_stoplimit(ws, sk, 7, sell_stop, sell_limit, sell_sl, sell_tp)
        sell_ticket = res['order']
        print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) order={sell_ticket}")
        
        await asyncio.sleep(2)
        
        # =========================================
        print(f"\n{'='*60}")
        print("TEST 3: Check pending orders")
        print(f"{'='*60}")
        orders = await get_pending_orders(ws, sk)
        for o in orders:
            print(f"  #{o['ticket']} {TYPES.get(o['type'],'?')} stop={o['price']:.5f} trigger={o['price_trigger']:.5f} sl={o['sl']:.5f} tp={o['tp']:.5f}")
        
        # =========================================
        print(f"\n{'='*60}")
        print("TEST 4: Modify BUY STOP LIMIT SL/TP")
        print(f"{'='*60}")
        if buy_ticket:
            new_sl = round(buy_stop - 150*POINT, 5)
            new_tp = round(buy_stop + 250*POINT, 5)
            print(f"  Order #{buy_ticket}")
            print(f"  New SL={new_sl:.5f} TP={new_tp:.5f}")
            
            aid = random.randint(1, 0x7FFFFFFE)
            op = bytearray(248)
            struct.pack_into('<I', op, 0, aid)
            struct.pack_into('<I', op, 4, 7)  # MODIFY_ORDER
            s = 'EURUSDm'.encode('utf-16-le'); op[8:8+len(s)] = s
            struct.pack_into('<Q', op, 72, 1000000)
            struct.pack_into('<I', op, 80, 5)
            struct.pack_into('<Q', op, 84, buy_ticket)
            struct.pack_into('<I', op, 92, 6)  # BUY STOP LIMIT type
            struct.pack_into('<I', op, 96, 2)
            struct.pack_into('<d', op, 112, buy_stop)
            struct.pack_into('<d', op, 120, buy_limit)
            struct.pack_into('<d', op, 128, new_sl)
            struct.pack_into('<d', op, 136, new_tp)
            
            res = await send_trade(ws, sk, bytes(op))
            print(f"  retcode={res['retcode']}({RC.get(res['retcode'],'?')}) comment='{res['comment']}'")
        
        await asyncio.sleep(1)
        
        # =========================================
        print(f"\n{'='*60}")
        print("TEST 5: Verify modification")
        print(f"{'='*60}")
        orders2 = await get_pending_orders(ws, sk)
        for o in orders2:
            if o['ticket'] == buy_ticket:
                print(f"  #{o['ticket']} sl={o['sl']:.5f} tp={o['tp']:.5f}")
                if o['sl'] > 0 and o['tp'] > 0:
                    print("  [+] SL/TP MODIFIED!")
                else:
                    print("  [-] SL/TP not modified")
        
        # =========================================
        print(f"\n{'='*60}")
        print("TEST 6: Cancel both orders")
        print(f"{'='*60}")
        orders3 = await get_pending_orders(ws, sk)
        for o in orders3:
            res = await cancel_order(ws, sk, o['ticket'], o['type'])
            print(f"  Cancel #{o['ticket']} ({TYPES.get(o['type'],'?')}): retcode={res['retcode']}({RC.get(res['retcode'],'?')})")
            await asyncio.sleep(1)
        
        orders4 = await get_pending_orders(ws, sk)
        print(f"\n  Final: {len(orders4)} pending orders remaining")
        
        print("\n[+] Done")

if __name__ == '__main__':
    asyncio.run(main())
