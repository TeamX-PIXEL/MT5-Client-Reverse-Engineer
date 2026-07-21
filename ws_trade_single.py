#!/usr/bin/env python3
"""
Clean trade test with corrected price (divide by 10^digits) and proper wait.
Single trade: EURUSDm BUY, action=3(MARKET), vol=1000000 (10 lots), FOK
Then check positions.
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

def dump_hex(data, max_len=380):
    """Dump hex of data."""
    lines = []
    for off in range(0, min(len(data), max_len), 32):
        chunk = data[off:off+32]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        lines.append(f"  [{off:3d}] {hex_str}")
    return '\n'.join(lines)

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

        # Get account
        await send_cmd(ws, session_key, 3)
        # Get symbols
        await send_cmd(ws, session_key, 34)
        # Subscribe to EURUSDm (id=426)
        await send_cmd(ws, session_key, 7, struct.pack('<II', 1, 426))

        # Wait for quote
        eurusd_bid = eurusd_ask = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp and resp['cmd_id'] == 8 and len(resp['res_body']) >= 50:
                    body = resp['res_body']
                    sym_id = struct.unpack_from('<I', body, 0)[0]
                    if sym_id == 426:
                        eurusd_bid = struct.unpack_from('<d', body, 12)[0]
                        eurusd_ask = struct.unpack_from('<d', body, 20)[0]
                        break
            except asyncio.TimeoutError:
                continue

        if not eurusd_bid:
            print("[!] No quote, using fallback")
            eurusd_bid = 1.14525
            eurusd_ask = 1.14533
            digits = 5
        else:
            digits = 5
            # Raw values are multiplied by 10^digits
            actual_bid = eurusd_bid / (10 ** digits)
            actual_ask = eurusd_ask / (10 ** digits)
            print(f"[+] EURUSDm raw: bid={eurusd_bid:.5f} ask={eurusd_ask:.5f}")
            print(f"[+] EURUSDm actual: bid={actual_bid:.5f} ask={actual_ask:.5f}")
            eurusd_bid = actual_bid
            eurusd_ask = actual_ask

        # =====================================================
        # SINGLE TRADE: BUY EURUSDm, action=3(MARKET), 10 lots
        # =====================================================
        print(f"\n{'='*70}")
        print("TRADE: BUY EURUSDm, 10 lots, MARKET")
        print(f"{'='*70}")

        action_id = random.randint(1, 0x7FFFFFFE)
        op = bytearray(248)
        struct.pack_into('<I', op, 0, action_id)
        struct.pack_into('<I', op, 4, 3)  # trade_action = MARKET
        sym = 'EURUSDm'.encode('utf-16-le')
        op[8:8+len(sym)] = sym
        struct.pack_into('<Q', op, 72, 1000000)  # volume = 10 lots
        struct.pack_into('<I', op, 80, 5)  # digits
        struct.pack_into('<I', op, 92, 0)  # trade_type = BUY
        struct.pack_into('<I', op, 96, 0)  # type_filling = FOK
        struct.pack_into('<I', op, 100, 0)  # type_time = GTC
        struct.pack_into('<I', op, 104, 0)  # type_flags
        struct.pack_into('<d', op, 112, eurusd_ask)  # price = ASK (actual, not raw)
        struct.pack_into('<d', op, 120, 0.0)  # price_trigger
        struct.pack_into('<d', op, 128, 0.0)  # SL
        struct.pack_into('<d', op, 136, 0.0)  # TP
        struct.pack_into('<I', op, 144, 50)  # deviation (slippage)

        print(f"  action_id={action_id}")
        print(f"  trade_action=3(MARKET) type=0(BUY)")
        print(f"  symbol=EURUSDm volume=1000000(10 lots)")
        print(f"  price={eurusd_ask:.5f} deviation=50")
        print(f"  filling=0(FOK) time=0(GTC)")

        # Build command: [random:2][cmd_id:2][Op:248]
        rand = random.randint(0, 65535)
        cmd_bytes = struct.pack('<HH', rand, 12) + bytes(op)
        enc = aes_encrypt(session_key, cmd_bytes)
        wire = pack_data(12, enc)

        print(f"\n  Sending {len(wire)} byte wire packet...")
        await ws.send(wire)
        print(f"  Sent! Waiting for response...")

        # Wait for response - check multiple messages
        await asyncio.sleep(3)

        # Drain ALL messages and print them
        found_event = False
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=2)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if not resp:
                    continue
                cmd_names = {3:'ACCOUNT', 4:'POSITIONS', 5:'DEALS', 7:'SUBSCRIBE',
                            8:'QUOTE', 19:'TRADE_EVENT', 28:'LOGIN', 34:'SYMBOLS', 51:'HEARTBEAT'}
                cmd_name = cmd_names.get(resp['cmd_id'], f'CMD_{resp["cmd_id"]}')
                print(f"\n  [{cmd_name}] cmd={resp['cmd_id']} res_code={resp['res_code']} body_size={len(resp['res_body'])}")

                if resp['cmd_id'] == 19:  # TRADE_EVENT
                    found_event = True
                    body = resp['res_body']
                    print(f"  TRADE_EVENT body ({len(body)} bytes):")
                    print(dump_hex(body, min(len(body), 380)))

                    # Parse: [prefix:4][Op:248][Ap:128]
                    if len(body) >= 4:
                        print(f"  prefix (body[0:4]): {struct.unpack_from('<I', body, 0)[0]}")

                    if len(body) >= 4 + 248:
                        op_data = body[4:4+248]
                        echo_action_id = struct.unpack_from('<I', op_data, 0)[0]
                        echo_trade_action = struct.unpack_from('<I', op_data, 4)[0]
                        echo_sym = op_data[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        echo_vol = struct.unpack_from('<Q', op_data, 72)[0]
                        echo_digits = struct.unpack_from('<I', op_data, 80)[0]
                        echo_trade_order = struct.unpack_from('<Q', op_data, 84)[0]
                        echo_trade_type = struct.unpack_from('<I', op_data, 92)[0]
                        echo_filling = struct.unpack_from('<I', op_data, 96)[0]
                        echo_price = struct.unpack_from('<d', op_data, 112)[0]
                        echo_sl = struct.unpack_from('<d', op_data, 128)[0]
                        echo_tp = struct.unpack_from('<d', op_data, 136)[0]
                        echo_dev = struct.unpack_from('<I', op_data, 144)[0]
                        echo_comment = op_data[164:228].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        echo_pos = struct.unpack_from('<Q', op_data, 228)[0]
                        print(f"\n  ECHOED Op:")
                        print(f"    action_id: {echo_action_id} (sent: {action_id})")
                        print(f"    trade_action: {echo_trade_action}")
                        print(f"    symbol: '{echo_sym}'")
                        print(f"    volume: {echo_vol}")
                        print(f"    digits: {echo_digits}")
                        print(f"    trade_order: {echo_trade_order}")
                        print(f"    trade_type: {echo_trade_type} ({'BUY' if echo_trade_type==0 else 'SELL'})")
                        print(f"    filling: {echo_filling}")
                        print(f"    price: {echo_price}")
                        print(f"    sl: {echo_sl}")
                        print(f"    tp: {echo_tp}")
                        print(f"    deviation: {echo_dev}")
                        print(f"    comment: '{echo_comment}'")
                        print(f"    position: {echo_pos}")

                    ap_off = 4 + 248
                    if len(body) >= ap_off + 128:
                        ap_data = body[ap_off:ap_off+128]
                        retcode = struct.unpack_from('<I', ap_data, 0)[0]
                        deal = struct.unpack_from('<q', ap_data, 4)[0]
                        order = struct.unpack_from('<q', ap_data, 12)[0]
                        vol = struct.unpack_from('<q', ap_data, 20)[0]
                        price = struct.unpack_from('<d', ap_data, 28)[0]
                        comment = ap_data[64:128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        print(f"\n  SERVER Ap:")
                        print(f"    retcode: {retcode}")
                        print(f"    deal: {deal}")
                        print(f"    order: {order}")
                        print(f"    volume: {vol}")
                        print(f"    price: {price}")
                        print(f"    comment: '{comment}'")

                        retcode_names = {
                            0: 'SUCCESS',
                            10002: 'TRADE_NOT_ACCEPTED',
                            10009: 'TRADE_POSITION_CLOSED',
                            10013: 'INVALID_PARAMS',
                            10014: 'INVALID_VOLUME',
                            10015: 'INVALID_PRICE',
                            10016: 'INVALID_STOPS',
                            10017: 'TRADE_DISABLED',
                            10030: 'INVALID_TRADE_ACTION',
                            10035: 'FILLING_NOT_ALLOWED',
                        }
                        print(f"    retcode meaning: {retcode_names.get(retcode, f'UNKNOWN({retcode})')}")

            except asyncio.TimeoutError:
                continue

        if not found_event:
            print("\n  [!] No TRADE_EVENT received")

        # Check positions
        print(f"\n{'='*70}")
        print("CHECKING POSITIONS (cmd_id=4)")
        print(f"{'='*70}")

        await send_cmd(ws, session_key, 4)
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp and resp['cmd_id'] == 4:
                    body = resp['res_body']
                    print(f"  Positions response: {len(body)} bytes")
                    if len(body) >= 4:
                        pos_count = struct.unpack_from('<I', body, 0)[0]
                        print(f"  Position count: {pos_count}")

                        if pos_count > 0 and len(body) >= 4 + 344:
                            # Parse first position (344 bytes each)
                            off = 4
                            pos_id = struct.unpack_from('<q', body, off)[0]
                            print(f"  Position ID: {pos_id}")
                            # More fields...
                            pos_sym = body[off+16:off+80].decode('utf-16-le', errors='ignore').rstrip('\x00')
                            print(f"  Symbol: {pos_sym}")
                            pos_vol = struct.unpack_from('<q', body, off+80)[0]
                            print(f"  Volume: {pos_vol}")
                            pos_price = struct.unpack_from('<d', body, off+96)[0]
                            print(f"  Price: {pos_price}")
                            pos_sl = struct.unpack_from('<d', body, off+104)[0]
                            pos_tp = struct.unpack_from('<d', body, off+112)[0]
                            print(f"  SL: {pos_sl}, TP: {pos_tp}")
                            pos_profit = struct.unpack_from('<d', body, off+120)[0]
                            print(f"  Profit: {pos_profit}")
                    break
            except asyncio.TimeoutError:
                continue

        # Also check deal history
        print(f"\n{'='*70}")
        print("CHECKING RECENT DEALS (cmd_id=5)")
        print(f"{'='*70}")

        now = int(time.time())
        deal_payload = struct.pack('<II', now - 300, now)  # last 5 minutes
        await send_cmd(ws, session_key, 5, deal_payload)
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp and resp['cmd_id'] == 5:
                    body = resp['res_body']
                    print(f"  Deals response: {len(body)} bytes")
                    if len(body) >= 4:
                        deal_count = struct.unpack_from('<I', body, 0)[0]
                        print(f"  Deal count: {deal_count}")
                    break
            except asyncio.TimeoutError:
                continue

        print(f"\n{'='*70}")
        print("DONE")

if __name__ == '__main__':
    asyncio.run(main())