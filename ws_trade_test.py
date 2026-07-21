#!/usr/bin/env python3
"""
Pure Python trade test - no Playwright needed.
Tests various trade_action values and payload formats.
Key findings:
  - trade_mode=4 (FULL) for EURUSDm, exemode=2 (MARKET), fill_flags=3 (FOK|IOC)
  - volume_min=1000000 (10 lots) for EURUSDm
  - JS positionOpen: exemode=2 → trade_action=3, type_filling=0 (FOK)
  - JS Do transformer: exemode=2 → action=3, exemode=1 → action=2
  - JS $o (action=0 validator): requires exemode==2
  - JS Ho (action=3 validator): requires exemode==3?? or maybe != 0x7f
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

async def send_and_drain(ws, sk, cmd_id, payload, expected_cmd, timeout=10):
    await send_cmd(ws, sk, cmd_id, payload)
    deadline = time.time() + timeout
    results = []
    while time.time() < deadline:
        try:
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = parse_response(aes_decrypt(sk, resp_raw[8:]))
            if resp:
                if resp['cmd_id'] == expected_cmd:
                    return resp
                results.append(resp)
        except asyncio.TimeoutError:
            continue
    return None

def parse_quote(raw, sym_id):
    """Parse 50-byte quote: [symbol_id:u32][tick_time:i32][fields:u32][bid:f64][ask:f64][last:f64][vol:i64][delta:u32][flags:u16]"""
    if len(raw) < 50:
        return None
    return {
        'symbol_id': struct.unpack_from('<I', raw, 0)[0],
        'bid': struct.unpack_from('<d', raw, 12)[0],
        'ask': struct.unpack_from('<d', raw, 20)[0],
    }

def build_op_trade(action_id, trade_action, symbol, volume, digits,
                   trade_type=0, type_filling=0, type_time=0, type_flags=0,
                   price=0.0, sl=0.0, tp=0.0, deviation=0, comment=''):
    """Build Op payload (248 bytes) per JS getTradeRequest."""
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, trade_action)
    sym_enc = symbol.encode('utf-16-le')
    op[8:8+len(sym_enc)] = sym_enc
    struct.pack_into('<Q', op, 72, volume)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, type_filling)
    struct.pack_into('<I', op, 100, type_time)
    struct.pack_into('<I', op, 104, type_flags)
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<d', op, 120, 0.0)  # price_trigger
    struct.pack_into('<d', op, 128, sl)
    struct.pack_into('<d', op, 136, tp)
    struct.pack_into('<I', op, 144, deviation)
    # comment at 164
    cmt_enc = comment.encode('utf-16-le')
    op[164:164+len(cmt_enc)] = cmt_enc
    return bytes(op)

def build_pp(action_id, op_payload, ap=None):
    """Build Pp: [action_id:u32][Op:248][Ap:128] = 380 bytes."""
    if ap is None:
        ap = bytes(128)
    return struct.pack('<I', action_id) + op_payload + ap

def send_trade_cmd(sk, op_payload):
    """Wrap Op (248B) into command and encrypt."""
    action_id = struct.unpack_from('<I', op_payload, 0)[0]
    rand = random.randint(0, 65535)
    cmd_bytes = struct.pack('<HH', rand, 12) + op_payload
    enc = aes_encrypt(sk, cmd_bytes)
    return pack_data(12, enc)

def send_trade_cmd_pp(sk, pp_payload):
    """Wrap Pp (380B) into command and encrypt."""
    action_id = struct.unpack_from('<I', pp_payload, 0)[0]
    rand = random.randint(0, 65535)
    cmd_bytes = struct.pack('<HH', rand, 12) + pp_payload
    enc = aes_encrypt(sk, cmd_bytes)
    return pack_data(12, enc)

def parse_trade_event(body):
    """Parse TRADE_EVENT (cmd_id=19) response body."""
    result = {}
    if len(body) >= 4:
        result['action_id'] = struct.unpack_from('<I', body, 0)[0]

    # Try Op at body[4:252]
    if len(body) >= 4 + 248:
        op = body[4:4+248]
        result['op'] = {
            'trade_action': struct.unpack_from('<I', op, 4)[0],
            'symbol': op[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00'),
            'volume': struct.unpack_from('<Q', op, 72)[0],
            'digits': struct.unpack_from('<I', op, 80)[0],
            'trade_order': struct.unpack_from('<Q', op, 84)[0],
            'trade_type': struct.unpack_from('<I', op, 92)[0],
            'type_filling': struct.unpack_from('<I', op, 96)[0],
            'price_order': struct.unpack_from('<d', op, 112)[0],
            'price_sl': struct.unpack_from('<d', op, 128)[0],
            'price_tp': struct.unpack_from('<d', op, 136)[0],
        }

    # Try Ap at body[252:380]
    ap_off = 4 + 248
    if len(body) >= ap_off + 128:
        ap = body[ap_off:ap_off+128]
        result['ap'] = {
            'retcode': struct.unpack_from('<I', ap, 0)[0],
            'deal': struct.unpack_from('<q', ap, 4)[0],
            'order': struct.unpack_from('<q', ap, 12)[0],
            'volume': struct.unpack_from('<q', ap, 20)[0],
            'price': struct.unpack_from('<d', ap, 28)[0],
            'comment': ap[64:128].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        }
    elif len(body) >= 4:
        # Maybe Ap starts at body[4] (Op-only, no action_id prefix)
        for try_off in [0, 4]:
            if try_off + 64 <= len(body):
                rc = struct.unpack_from('<I', body, try_off)[0]
                if rc in [0, 10013, 10014, 10015, 10016, 10017, 10030, 10035]:
                    deal = struct.unpack_from('<q', body, try_off+4)[0]
                    order = struct.unpack_from('<q', body, try_off+12)[0]
                    if f'ap_{try_off}' not in result:
                        result[f'ap_{try_off}'] = {
                            'retcode': rc, 'deal': deal, 'order': order,
                        }
    return result

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
        # Subscribe to EURUSDm (id=426) quotes
        sub_payload = struct.pack('<II', 1, 426)  # count=1, symbol_id=426
        await send_cmd(ws, session_key, 7, sub_payload)

        # Drain messages
        eurusd_sym_id = 426
        eurusd_bid = eurusd_ask = None
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=2)
                resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                if resp and resp['cmd_id'] == 8 and len(resp['res_body']) >= 50:
                    q = parse_quote(resp['res_body'], eurusd_sym_id)
                    if q and q['symbol_id'] == eurusd_sym_id:
                        eurusd_bid = q['bid']
                        eurusd_ask = q['ask']
                        break
            except asyncio.TimeoutError:
                continue

        if not eurusd_bid:
            print("[!] No EURUSDm quote received, using fallback price")
            eurusd_bid = 1.14500
            eurusd_ask = 1.14515
        else:
            print(f"[+] EURUSDm: bid={eurusd_bid:.5f} ask={eurusd_ask:.5f}")

        # =====================================================
        # TRADE TESTS - try various configurations
        # =====================================================
        print(f"\n{'='*70}")
        print("TRADE TESTS - EURUSDm (exemode=2 MARKET, fill_flags=3 FOK|IOC)")
        print(f"{'='*70}")

        # volume_min=1000000 (10 lots) - try with correct volume
        vol_10lots = 1000000
        vol_001lots = 1000
        vol_1lot = 100000

        test_cases = [
            # (label, trade_action, trade_type, price, volume, filling, flags)
            ("A: action=3(MARKET) BUY vol=100000", 3, 0, eurusd_ask, vol_1lot, 0, 0),
            ("B: action=3(MARKET) BUY vol=1000000", 3, 0, eurusd_ask, vol_10lots, 0, 0),
            ("C: action=0(DEAL) BUY vol=100000", 0, 0, eurusd_ask, vol_1lot, 0, 0),
            ("D: action=0(DEAL) BUY vol=1000000", 0, 0, eurusd_ask, vol_10lots, 0, 0),
            ("E: action=2(INSTANT) BUY vol=100000", 2, 0, eurusd_ask, vol_1lot, 0, 0),
            ("F: action=3(MARKET) BUY vol=100000 filling=1(IOC)", 3, 0, eurusd_ask, vol_1lot, 1, 0),
            ("G: action=3(MARKET) BUY vol=100000 filling=2(RETURN)", 3, 0, eurusd_ask, vol_1lot, 2, 0),
            ("H: action=3(MARKET) BUY vol=100000 flags=2", 3, 0, eurusd_ask, vol_1lot, 0, 2),
        ]

        for label, ta, tt, price, vol, filling, flags in test_cases:
            print(f"\n--- Test {label} ---")
            action_id = random.randint(1, 0x7FFFFFFE)
            op = build_op_trade(
                action_id=action_id,
                trade_action=ta,
                symbol='EURUSDm',
                volume=vol,
                digits=5,
                trade_type=tt,
                type_filling=filling,
                type_time=0,
                type_flags=flags,
                price=price,
                deviation=50,
            )

            # Send as Op-only (248 bytes)
            wire = send_trade_cmd(session_key, op)
            await ws.send(wire)

            # Wait for TRADE_EVENT
            resp = await send_and_drain(ws, session_key, 12, b'', 19, timeout=5)
            if resp:
                ev = parse_trade_event(resp['res_body'])
                op_info = ev.get('op', {})
                ap_info = ev.get('ap', ev.get('ap_0', ev.get('ap_4', {})))
                print(f"  action_id sent={action_id} echoed={ev.get('action_id', '?')}")
                print(f"  op: action={op_info.get('trade_action', '?')} sym='{op_info.get('symbol', '?')}' "
                      f"vol={op_info.get('volume', '?')} price={op_info.get('price_order', 0):.5f}")
                print(f"  ap: retcode={ap_info.get('retcode', '?')} deal={ap_info.get('deal', 0)} "
                      f"order={ap_info.get('order', 0)} comment='{ap_info.get('comment', '')}'")
            else:
                # Check for direct response (cmd 12 response)
                print(f"  No TRADE_EVENT received (timeout)")
                # Try to read any response
                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    resp = parse_response(aes_decrypt(session_key, resp_raw[8:]))
                    if resp:
                        print(f"  Got cmd_id={resp['cmd_id']} res_code={resp['res_code']} size={len(resp['res_body'])}")
                        if resp['cmd_id'] == 19:
                            ev = parse_trade_event(resp['res_body'])
                            ap_info = ev.get('ap', ev.get('ap_0', {}))
                            print(f"  retcode={ap_info.get('retcode', '?')}")
                except:
                    pass

        # Also try via Pp (380 bytes) for comparison
        print(f"\n{'='*70}")
        print("Pp FORMAT TEST (380 bytes)")
        print(f"{'='*70}")

        action_id = random.randint(1, 0x7FFFFFFE)
        op = build_op_trade(
            action_id=action_id,
            trade_action=3,
            symbol='EURUSDm',
            volume=vol_1lot,
            digits=5,
            trade_type=0,
            type_filling=0,
            type_time=0,
            price=eurusd_ask,
            deviation=50,
        )
        pp = build_pp(action_id, op)
        wire = send_trade_cmd_pp(session_key, pp)
        await ws.send(wire)

        resp = await send_and_drain(ws, session_key, 12, b'', 19, timeout=5)
        if resp:
            ev = parse_trade_event(resp['res_body'])
            ap_info = ev.get('ap', ev.get('ap_0', ev.get('ap_4', {})))
            print(f"  Pp: retcode={ap_info.get('retcode', '?')} deal={ap_info.get('deal', 0)} "
                  f"order={ap_info.get('order', 0)} comment='{ap_info.get('comment', '')}'")
        else:
            print(f"  No response received")

        print(f"\n{'='*70}")
        print("DONE")

if __name__ == '__main__':
    asyncio.run(main())