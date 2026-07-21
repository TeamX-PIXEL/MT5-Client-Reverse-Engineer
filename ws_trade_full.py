#!/usr/bin/env python3
"""
Check positions, close any existing EURUSDm positions, then open a clean trade.
Uses correct 344-byte position schema with proper field offsets.
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

def parse_position(body, offset):
    """Parse a single 344-byte position record."""
    if offset + POS_SIZE > len(body):
        return None
    rec = body[offset:offset+POS_SIZE]
    return {
        'position_id': struct.unpack_from('<q', rec, 0)[0],
        'trade_order': struct.unpack_from('<q', rec, 8)[0],
        'time_create': struct.unpack_from('<I', rec, 16)[0],
        'time_update': struct.unpack_from('<I', rec, 20)[0],
        'symbol': rec[24:88].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        'action': struct.unpack_from('<I', rec, 88)[0],
        'price_open': struct.unpack_from('<d', rec, 92)[0],
        'price_close': struct.unpack_from('<d', rec, 100)[0],
        'sl': struct.unpack_from('<d', rec, 108)[0],
        'tp': struct.unpack_from('<d', rec, 116)[0],
        'volume': struct.unpack_from('<Q', rec, 124)[0],
        'profit': struct.unpack_from('<d', rec, 132)[0],
        'comment': rec[188:252].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        'digits': struct.unpack_from('<I', rec, 260)[0],
    }

async def get_positions(ws, sk):
    """Request positions (cmd_id=4) and parse."""
    await send_cmd(ws, sk, 4)
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
            resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
            if resp and resp['cmd_id'] == 4:
                body = resp['res_body']
                if len(body) < 4:
                    return [], []
                pos_count = struct.unpack_from('<I', body, 0)[0]
                positions = []
                off = 4
                for _ in range(pos_count):
                    pos = parse_position(body, off)
                    if pos:
                        positions.append(pos)
                    off += POS_SIZE
                # Order count
                order_count = struct.unpack_from('<I', body, off)[0] if off + 4 <= len(body) else 0
                return positions, list(range(order_count))
        except asyncio.TimeoutError:
            continue
    return [], []

def build_close_op(action_id, pos, price, deviation=50):
    """Build Op to close a position (trade_action=3 MARKET, opposite type)."""
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, 3)  # trade_action = MARKET
    sym = pos['symbol'].encode('utf-16-le')
    op[8:8+len(sym)] = sym
    struct.pack_into('<Q', op, 72, pos['volume'])  # full volume
    struct.pack_into('<I', op, 80, pos['digits'])  # digits
    close_type = 1 if pos['action'] == 0 else 0  # opposite: BUY→SELL, SELL→BUY
    struct.pack_into('<I', op, 92, close_type)  # trade_type
    struct.pack_into('<I', op, 96, 0)  # type_filling = FOK
    struct.pack_into('<I', op, 100, 0)  # type_time = GTC
    struct.pack_into('<I', op, 104, 0)  # type_flags
    struct.pack_into('<d', op, 112, price)  # price
    struct.pack_into('<I', op, 144, deviation)
    struct.pack_into('<Q', op, 228, pos['position_id'])  # position ticket
    return bytes(op)

def build_open_op(action_id, symbol, volume, digits, trade_type, price, deviation=50):
    """Build Op to open a position."""
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, 3)  # trade_action = MARKET
    sym = symbol.encode('utf-16-le')
    op[8:8+len(sym)] = sym
    struct.pack_into('<Q', op, 72, volume)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<I', op, 92, trade_type)  # 0=BUY, 1=SELL
    struct.pack_into('<I', op, 96, 0)  # FOK
    struct.pack_into('<I', op, 100, 0)  # GTC
    struct.pack_into('<I', op, 104, 0)
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<I', op, 144, deviation)
    return bytes(op)

async def send_trade(ws, sk, op):
    """Send trade command and wait for TRADE_EVENT."""
    rand = random.randint(0, 65535)
    cmd_bytes = struct.pack('<HH', rand, 12) + op
    enc = aes_encrypt(sk, cmd_bytes)
    await ws.send(pack_data(12, enc))

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
            resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
            if resp and resp['cmd_id'] == 19:
                body = resp['res_body']
                if len(body) >= 4 + 248 + 128:
                    ap = body[4+248:4+248+128]
                    return {
                        'retcode': struct.unpack_from('<I', ap, 0)[0],
                        'deal': struct.unpack_from('<q', ap, 4)[0],
                        'order': struct.unpack_from('<q', ap, 12)[0],
                        'volume': struct.unpack_from('<q', ap, 20)[0],
                        'price': struct.unpack_from('<d', ap, 28)[0],
                        'comment': ap[64:128].decode('utf-16-le', errors='ignore').rstrip('\x00'),
                    }
        except asyncio.TimeoutError:
            continue
    return None

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
        print("[+] Auth OK")

        # Login
        login_pl = build_login_payload(LOGIN, PASSWORD, SERVER_IP)
        await ws.send(pack_data(28, aes_encrypt(session_key, build_command(28, login_pl))))
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
        print("[+] Login OK")

        # Get account
        await send_cmd(ws, session_key, 3)
        await asyncio.sleep(1)
        # Get symbols
        await send_cmd(ws, session_key, 34)
        await asyncio.sleep(1)
        # Subscribe to EURUSDm quotes
        await send_cmd(ws, session_key, 7, struct.pack('<II', 1, 426))

        # Collect quote
        bid = ask = None
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=2)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp and resp['cmd_id'] == 8 and len(resp['res_body']) >= 50:
                    body = resp['res_body']
                    sym_id = struct.unpack_from('<I', body, 0)[0]
                    if sym_id == 426:
                        raw_bid = struct.unpack_from('<d', body, 12)[0]
                        raw_ask = struct.unpack_from('<d', body, 20)[0]
                        bid = raw_bid / 100000
                        ask = raw_ask / 100000
                        break
            except asyncio.TimeoutError:
                continue

        if bid:
            print(f"[+] EURUSDm: bid={bid:.5f} ask={ask:.5f}")
        else:
            print("[!] No quote, using fallback")
            bid, ask = 1.14500, 1.14515

        # =====================================================
        # STEP 1: Check existing positions
        # =====================================================
        print(f"\n{'='*70}")
        print("STEP 1: CHECKING POSITIONS")
        print(f"{'='*70}")

        positions, orders = await get_positions(ws, session_key)
        print(f"  Found {len(positions)} positions, {len(orders)} pending orders")

        for i, pos in enumerate(positions):
            action_str = 'BUY' if pos['action'] == 0 else 'SELL'
            print(f"  Position {i}:")
            print(f"    ID: {pos['position_id']}")
            print(f"    Symbol: {pos['symbol']}")
            print(f"    Direction: {action_str}")
            print(f"    Volume: {pos['volume']} ({pos['volume']/100000000:.2f} lots)")
            print(f"    Open Price: {pos['price_open']:.5f}")
            print(f"    Current Price: {pos['price_close']:.5f}")
            print(f"    SL: {pos['sl']:.5f}, TP: {pos['tp']:.5f}")
            print(f"    Profit: {pos['profit']:.2f}")
            print(f"    Comment: '{pos['comment']}'")
            print(f"    Digits: {pos['digits']}")

        # =====================================================
        # STEP 2: Close all EURUSDm positions
        # =====================================================
        if positions:
            print(f"\n{'='*70}")
            print("STEP 2: CLOSING EXISTING POSITIONS")
            print(f"{'='*70}")

            for pos in positions:
                close_type = 1 if pos['action'] == 0 else 0  # opposite
                close_price = bid if pos['action'] == 0 else ask  # opposite side
                action_id = random.randint(1, 0x7FFFFFFE)

                print(f"\n  Closing position {pos['position_id']} ({pos['symbol']} "
                      f"{'BUY' if pos['action']==0 else 'SELL'} {pos['volume']/100000000:.2f} lots)...")

                op = build_close_op(action_id, pos, close_price)
                result = await send_trade(ws, session_key, op)

                if result:
                    retcode_names = {0:'SUCCESS', 10002:'NOT_ACCEPTED', 10009:'ACCEPTED',
                                    10013:'INVALID_PARAMS', 10014:'INVALID_VOLUME',
                                    10015:'INVALID_PRICE', 10017:'DISABLED'}
                    print(f"    retcode={result['retcode']} ({retcode_names.get(result['retcode'], '?')})")
                    print(f"    deal={result['deal']} order={result['order']}")
                    print(f"    volume={result['volume']} price={result['price']:.5f}")
                    print(f"    comment='{result['comment']}'")
                else:
                    print(f"    [!] No TRADE_EVENT received")

                await asyncio.sleep(1)

            # Re-check positions
            print(f"\n  Re-checking positions after close...")
            positions2, _ = await get_positions(ws, session_key)
            print(f"  Remaining positions: {len(positions2)}")
            for pos in positions2:
                print(f"    {pos['symbol']} {'BUY' if pos['action']==0 else 'SELL'} "
                      f"{pos['volume']/100000000:.2f} lots @ {pos['price_open']:.5f}")
        else:
            print("  No positions to close.")

        # =====================================================
        # STEP 3: Open a new trade
        # =====================================================
        print(f"\n{'='*70}")
        print("STEP 3: OPENING NEW TRADE")
        print(f"{'='*70}")

        # BUY 10 lots EURUSDm
        action_id = random.randint(1, 0x7FFFFFFE)
        vol = 1000000  # 10 lots
        print(f"  BUY EURUSDm {vol/100000000:.2f} lots @ {ask:.5f}")

        op = build_open_op(action_id, 'EURUSDm', vol, 5, 0, ask)
        result = await send_trade(ws, session_key, op)

        if result:
            retcode_names = {0:'SUCCESS', 10002:'NOT_ACCEPTED', 10009:'ACCEPTED',
                            10013:'INVALID_PARAMS', 10014:'INVALID_VOLUME',
                            10015:'INVALID_PRICE', 10017:'DISABLED'}
            print(f"  retcode={result['retcode']} ({retcode_names.get(result['retcode'], '?')})")
            print(f"  deal={result['deal']} order={result['order']}")
            print(f"  volume={result['volume']} price={result['price']:.5f}")
            print(f"  comment='{result['comment']}'")
        else:
            print("  [!] No TRADE_EVENT received")

        # =====================================================
        # STEP 4: Check positions again
        # =====================================================
        await asyncio.sleep(2)
        print(f"\n{'='*70}")
        print("STEP 4: FINAL POSITION CHECK")
        print(f"{'='*70}")

        positions3, _ = await get_positions(ws, session_key)
        print(f"  Found {len(positions3)} positions")
        for pos in positions3:
            action_str = 'BUY' if pos['action'] == 0 else 'SELL'
            print(f"  {pos['symbol']} {action_str} {pos['volume']/100000000:.2f} lots "
                  f"@ {pos['price_open']:.5f} profit={pos['profit']:.2f}")

        print(f"\n{'='*70}")
        print("DONE")

if __name__ == '__main__':
    asyncio.run(main())