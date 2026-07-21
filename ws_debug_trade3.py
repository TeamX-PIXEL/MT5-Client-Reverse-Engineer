#!/usr/bin/env python3
"""
Test trade actions based on MQL5 Trade.mqh mapping.
WS trade_action mapping (from JS code):
  0 = DEAL (market open/close)
  1 = PENDING (pending order)
  2 = INSTANT
  10 = CLOSE
  201 = MODIFY

MQL5 ORDER_TYPE:
  0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP

Key insight from Trade.mqh:
  - Market Buy: action=DEAL, type=BUY(0), price=ASK
  - Market Sell: action=DEAL, type=SELL(1), price=BID
  - Close BUY: action=DEAL, type=SELL(1), price=BID, position=ticket
  - Close SELL: action=DEAL, type=BUY(0), price=ASK, position=ticket
  - Pending: action=PENDING(1), type=LIMIT/STOP, price=trigger
  - Modify SL/TP: action=201, position=ticket
  - Delete order: action=REMOVE(not in WS?), order=ticket
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

def aes_encrypt(key, pt):
    pad_len = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad_len] * pad_len))
def aes_decrypt(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt
def pack_data(cmd_id, enc):
    return struct.pack('<II', len(enc), 1) + enc
def build_command(cmd_id, payload=b''):
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0, 255)
    cmd[1] = random.randint(0, 255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload: cmd[4:4+len(payload)] = payload
    return bytes(cmd)
def parse_response(data):
    if len(data) < 5: return None
    return {'tag': struct.unpack('<H', data[0:2])[0], 'cmd_id': struct.unpack('<H', data[2:4])[0],
            'res_code': data[4], 'res_body': data[5:]}
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

async def send_and_wait(ws, sk, cmd_id, payload, expected_cmd, timeout=5):
    await send_cmd(ws, sk, cmd_id, payload)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(resp, bytes) and len(resp) > 8:
                r = parse_response(aes_decrypt(sk, resp[8:]))
                if r and r['cmd_id'] == expected_cmd: return r
        except asyncio.TimeoutError: break
    return None

async def collect_all(ws, sk, seconds):
    results = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=0.3)
            if isinstance(resp, bytes) and len(resp) > 8:
                r = parse_response(aes_decrypt(sk, resp[8:]))
                if r: results.append(r)
        except asyncio.TimeoutError: pass
    return results

OP_SIZE = 248

ERROR_CODES = {
    0: 'OK', 1: 'OK', 2: 'common error', 3: 'invalid params', 4: 'invalid params',
    10009: 'done', 10010: 'done', 10011: 'common error',
    10013: 'invalid parameters', 10014: 'invalid volume',
    10015: 'invalid price', 10016: 'invalid SL/TP', 10017: 'account restricted',
    10021: 'no price', 10030: 'invalid trade_action', 10035: 'filling not allowed',
}

TRADE_TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        # Auth + Login
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_command(0, bytes(64)))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(STATIC_KEY, resp_raw[8:]))
        sk = resp['res_body'][66:]
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(sk, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
        print("[+] Auth+Login OK")

        # Get current price
        now_ms = int(time.time() * 1000)
        rates_payload = bytearray(74)
        target = 'EURUSDm'
        sym_bytes = target.encode('utf-16-le')
        rates_payload[0:0+len(sym_bytes)] = sym_bytes
        struct.pack_into('<H', rates_payload, 64, 1)
        struct.pack_into('<i', rates_payload, 66, (now_ms - 3600000) // 1000)
        struct.pack_into('<i', rates_payload, 70, now_ms // 1000)
        r = await send_and_wait(ws, sk, 11, bytes(rates_payload), 11, timeout=10)
        current_price = 1.14000
        if r:
            body = r['res_body']
            candle_count = len(body) // 48
            if candle_count > 0:
                last_off = (candle_count - 1) * 48
                current_price = struct.unpack_from('<d', body, last_off+28)[0]
        bid = current_price
        ask = current_price + 0.00015
        print(f"[+] Price: bid={bid:.5f} ask={ask:.5f}")

        # ===== Helper to build Op payload =====
        def build_op(trade_action, trade_type, price, sl=0, tp=0, deviation=0,
                     symbol=target, volume=1000, digits=5, filling=0, time_type=0,
                     pos_id=0, pos_by=0, order_id=0, expiration=0, comment=""):
            op = bytearray(OP_SIZE)
            struct.pack_into('<I', op, 4, trade_action)
            sym_b = symbol.encode('utf-16-le')
            op[8:8+len(sym_b)] = sym_b
            struct.pack_into('<Q', op, 72, volume)
            struct.pack_into('<I', op, 80, digits)
            struct.pack_into('<Q', op, 84, order_id)
            struct.pack_into('<I', op, 92, trade_type)
            struct.pack_into('<I', op, 96, filling)
            struct.pack_into('<I', op, 100, time_type)
            struct.pack_into('<d', op, 112, price)
            struct.pack_into('<d', op, 128, sl)
            struct.pack_into('<d', op, 136, tp)
            struct.pack_into('<I', op, 144, deviation)
            struct.pack_into('<Q', op, 228, pos_id)
            struct.pack_into('<Q', op, 236, pos_by)
            if comment:
                cb = comment.encode('utf-16-le')
                op[164:164+len(cb)] = cb
            return bytes(op)

        async def trade_test(label, op_bytes):
            print(f"\n--- {label} ---")
            t0 = time.time()
            await send_cmd(ws, sk, 12, op_bytes)
            msgs = await collect_all(ws, sk, 8)
            for m in msgs:
                t = time.time() - t0
                if m['cmd_id'] == 12:
                    retcode = struct.unpack_from('<I', m['res_body'], 0)[0] if len(m['res_body']) >= 4 else -1
                    print(f"  [{t:.3f}s] TRADE cmd retcode={retcode}")
                elif m['cmd_id'] == 19:
                    body = m['res_body']
                    op_echo_action = struct.unpack_from('<I', body, 4+4)[0]
                    op_echo_type = struct.unpack_from('<I', body, 4+92)[0]
                    op_echo_price = struct.unpack_from('<d', body, 4+112)[0]
                    op_echo_vol = struct.unpack_from('<Q', body, 4+72)[0]
                    ap_off = 4 + OP_SIZE
                    ap_retcode = struct.unpack_from('<I', body, ap_off)[0]
                    ap_deal = struct.unpack_from('<q', body, ap_off+4)[0]
                    ap_order = struct.unpack_from('<q', body, ap_off+12)[0]
                    ap_vol = struct.unpack_from('<q', body, ap_off+20)[0]
                    ap_price = struct.unpack_from('<d', body, ap_off+28)[0]
                    ap_bid = struct.unpack_from('<d', body, ap_off+48)[0]
                    ap_ask = struct.unpack_from('<d', body, ap_off+56)[0]
                    err = ERROR_CODES.get(ap_retcode, f'unknown({ap_retcode})')
                    print(f"  [{t:.3f}s] Op echo: action={op_echo_action} type={op_echo_type} "
                          f"vol={op_echo_vol} price={op_echo_price:.5f}")
                    print(f"  [{t:.3f}s] Ap: retcode={ap_retcode}({err}) "
                          f"deal={ap_deal} order={ap_order} vol={ap_vol} "
                          f"price={ap_price:.5f} bid={ap_bid:.5f} ask={ap_ask:.5f}")
            return msgs

        # ========================================
        # TEST 1: MARKET BUY via trade_action=0 (DEAL)
        # From Trade.mqh: action=DEAL, type=BUY(0), price=ASK
        # ========================================
        print(f"\n{'='*60}")
        print(f"TEST 1: MARKET BUY 0.01 via trade_action=0 (DEAL)")
        print(f"  Expected: action=0, type=0(BUY), price={ask:.5f}")
        print(f"{'='*60}")
        op = build_op(trade_action=0, trade_type=0, price=ask, deviation=10)
        await trade_test("MARKET BUY (action=0, type=0)", op)

        # Check positions
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            buf = r['res_body']
            pos_count = struct.unpack_from('<I', buf, 0)[0]
            print(f"  Positions: {pos_count}")

        # ========================================
        # TEST 2: MARKET SELL via trade_action=0 (DEAL)
        # From Trade.mqh: action=DEAL, type=SELL(1), price=BID
        # ========================================
        print(f"\n{'='*60}")
        print(f"TEST 2: MARKET SELL 0.01 via trade_action=0 (DEAL)")
        print(f"  Expected: action=0, type=1(SELL), price={bid:.5f}")
        print(f"{'='*60}")
        op = build_op(trade_action=0, trade_type=1, price=bid, deviation=10)
        await trade_test("MARKET SELL (action=0, type=1)", op)

        # Check positions
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            buf = r['res_body']
            pos_count = struct.unpack_from('<I', buf, 0)[0]
            print(f"  Positions: {pos_count}")

        # ========================================
        # TEST 3: BUY STOP via trade_action=1 (PENDING)
        # From Trade.mqh: action=PENDING, type=BUY_STOP(4), price=trigger
        # Filling: ORDER_FILLING_RETURN(2) for pending stop/limit
        # ========================================
        stop_price = ask + 0.00100
        print(f"\n{'='*60}")
        print(f"TEST 3: BUY STOP 0.01 via trade_action=1 (PENDING)")
        print(f"  Expected: action=1, type=4(BUY_STOP), price={stop_price:.5f}, filling=2(RETURN)")
        print(f"{'='*60}")
        op = build_op(trade_action=1, trade_type=4, price=stop_price, filling=2)
        await trade_test("BUY STOP (action=1, type=4, fill=2)", op)

        # Check pending orders
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            buf = r['res_body']
            pos_count = struct.unpack_from('<I', buf, 0)[0]
            off = 4 + pos_count * 344
            if off + 4 <= len(buf):
                order_count = struct.unpack_from('<I', buf, off)[0]
                print(f"  Positions: {pos_count}, Pending orders: {order_count}")

        # ========================================
        # TEST 4: BUY LIMIT via trade_action=1 (PENDING)
        # ========================================
        limit_price = bid - 0.00100
        print(f"\n{'='*60}")
        print(f"TEST 4: BUY LIMIT 0.01 via trade_action=1 (PENDING)")
        print(f"  Expected: action=1, type=2(BUY_LIMIT), price={limit_price:.5f}, filling=2(RETURN)")
        print(f"{'='*60}")
        op = build_op(trade_action=1, trade_type=2, price=limit_price, filling=2)
        await trade_test("BUY LIMIT (action=1, type=2, fill=2)", op)

        # Check pending orders
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            buf = r['res_body']
            pos_count = struct.unpack_from('<I', buf, 0)[0]
            off = 4 + pos_count * 344
            if off + 4 <= len(buf):
                order_count = struct.unpack_from('<I', buf, off)[0]
                print(f"  Positions: {pos_count}, Pending orders: {order_count}")

        # ========================================
        # TEST 5: SELL STOP via trade_action=1 (PENDING)
        # ========================================
        sell_stop_price = bid - 0.00100
        print(f"\n{'='*60}")
        print(f"TEST 5: SELL STOP 0.01 via trade_action=1 (PENDING)")
        print(f"  Expected: action=1, type=5(SELL_STOP), price={sell_stop_price:.5f}, filling=2(RETURN)")
        print(f"{'='*60}")
        op = build_op(trade_action=1, trade_type=5, price=sell_stop_price, filling=2)
        await trade_test("SELL STOP (action=1, type=5, fill=2)", op)

        # Check pending orders
        r = await send_and_wait(ws, sk, 4, b'', 4)
        if r:
            buf = r['res_body']
            pos_count = struct.unpack_from('<I', buf, 0)[0]
            off = 4 + pos_count * 344
            if off + 4 <= len(buf):
                order_count = struct.unpack_from('<I', buf, off)[0]
                print(f"  Positions: {pos_count}, Pending orders: {order_count}")

        print("\n[+] Done!")

asyncio.run(main())
