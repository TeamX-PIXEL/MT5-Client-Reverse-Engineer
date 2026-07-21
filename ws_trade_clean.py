#!/usr/bin/env python3
"""
Clean position management: check, close, verify, then open fresh.
Waits properly between operations and verifies via deal history.
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
    """Drain messages until we get expected_cmd, return list of all messages."""
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
    if off + POS_SIZE > len(body): return None
    r = body[off:off+POS_SIZE]
    return {
        'id': struct.unpack_from('<q', r, 0)[0],
        'order': struct.unpack_from('<q', r, 8)[0],
        'time': struct.unpack_from('<I', r, 16)[0],
        'symbol': r[24:88].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        'action': struct.unpack_from('<I', r, 88)[0],
        'price_open': struct.unpack_from('<d', r, 92)[0],
        'price_cur': struct.unpack_from('<d', r, 100)[0],
        'sl': struct.unpack_from('<d', r, 108)[0],
        'tp': struct.unpack_from('<d', r, 116)[0],
        'volume': struct.unpack_from('<Q', r, 124)[0],
        'profit': struct.unpack_from('<d', r, 132)[0],
        'digits': struct.unpack_from('<I', r, 260)[0],
    }

async def get_positions(ws, sk):
    await send_cmd(ws, sk, 4)
    msgs = await drain(ws, sk, 4)
    for m in msgs:
        if m['cmd_id'] == 4:
            body = m['res_body']
            if len(body) < 4: return []
            cnt = struct.unpack_from('<I', body, 0)[0]
            pos = []
            off = 4
            for _ in range(cnt):
                p = parse_pos(body, off)
                if p: pos.append(p)
                off += POS_SIZE
            return pos
    return []

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
    """Send trade, wait for BOTH TRADE_EVENTs (10002 ack + 10009 result)."""
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
                    if rc != 10002:  # Wait for non-ack event
                        return result
        except asyncio.TimeoutError:
            continue
    return result

RC_NAMES = {0:'OK', 10002:'ACK', 10009:'ACCEPTED', 10013:'INVALID', 10014:'BAD_VOLUME',
            10015:'BAD_PRICE', 10016:'BAD_STOPS', 10017:'DISABLED', 10030:'BAD_ACTION'}

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

        # Get account
        await send_cmd(ws, sk, 3)
        await asyncio.sleep(0.5)
        # Get symbols
        await send_cmd(ws, sk, 34)
        await asyncio.sleep(0.5)
        # Subscribe EURUSDm
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

        # Check positions
        print(f"\n=== POSITIONS ===")
        positions = await get_positions(ws, sk)
        print(f"  Count: {len(positions)}")
        for p in positions:
            d = 'BUY' if p['action'] == 0 else 'SELL'
            print(f"  #{p['id']} {p['symbol']} {d} {p["volume"]/100000000:.2f} lots "
                  f"@ {p['price_open']:.5f} P/L={p['profit']:.2f}")

        # Close all positions
        if positions:
            print(f"\n=== CLOSING {len(positions)} POSITION(S) ===")
            for p in positions:
                close_type = 1 - p['action']  # BUY->SELL, SELL->BUY
                close_price = bid if p['action'] == 0 else ask
                aid = random.randint(1, 0x7FFFFFFE)
                op = mk_trade_op(aid, p['symbol'], p['volume'], p['digits'],
                                 close_type, close_price, pos_id=p['id'])
                print(f"\n  Closing #{p['id']} ({p['volume']/100000000:.2f} lots @ {close_price:.5f})...")
                res = await send_trade(ws, sk, op)
                if res:
                    print(f"    retcode={res['retcode']}({RC_NAMES.get(res['retcode'],'?')}) "
                          f"deal={res['deal']} order={res['order']} "
                          f"vol={res['volume']} price={res['price']:.5f} "
                          f"comment='{res['comment']}'")
                else:
                    print(f"    [!] No response")
                await asyncio.sleep(2)

            # Verify closure
            print(f"\n=== VERIFYING CLOSURE ===")
            positions2 = await get_positions(ws, sk)
            print(f"  Remaining: {len(positions2)}")
            for p in positions2:
                d = 'BUY' if p['action'] == 0 else 'SELL'
                print(f"  #{p['id']} {p['symbol']} {d} {p["volume"]/100000000:.2f} lots "
                      f"@ {p['price_open']:.5f}")
        else:
            print("  No positions to close.")

        # Open fresh trade
        print(f"\n=== OPENING FRESH TRADE ===")
        aid = random.randint(1, 0x7FFFFFFE)
        vol = 1000000  # 0.01 lots (minimum)
        op = mk_trade_op(aid, 'EURUSDm', vol, 5, 0, ask)
        print(f"  BUY EURUSDm 0.01 lots @ {ask:.5f}")
        res = await send_trade(ws, sk, op)
        if res:
            print(f"  retcode={res['retcode']}({RC_NAMES.get(res['retcode'],'?')}) "
                  f"deal={res['deal']} order={res['order']} "
                  f"vol={res['volume']} price={res['price']:.5f} "
                  f"comment='{res['comment']}'")

        # Final check
        await asyncio.sleep(3)
        print(f"\n=== FINAL POSITIONS ===")
        positions3 = await get_positions(ws, sk)
        print(f"  Count: {len(positions3)}")
        for p in positions3:
            d = 'BUY' if p['action'] == 0 else 'SELL'
            print(f"  #{p['id']} {p['symbol']} {d} {p["volume"]/100000000:.2f} lots "
                  f"@ {p['price_open']:.5f} P/L={p['profit']:.2f}")

if __name__ == '__main__':
    asyncio.run(main())