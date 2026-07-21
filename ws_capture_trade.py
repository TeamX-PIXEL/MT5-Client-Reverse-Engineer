#!/usr/bin/env python3
"""
Final attempt: decrypt ALL frames with session key after capturing it.
"""
import asyncio, struct, time
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)
OP_SIZE = 248

def aes_decrypt(key, ct):
    if not ct or len(ct) % 16 != 0: return None
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

def parse_cmd(dec):
    if len(dec) < 4: return None
    return {'rand': struct.unpack_from('<H', dec, 0)[0],
            'cmd_id': struct.unpack_from('<H', dec, 2)[0],
            'payload': dec[4:]}

TRADE_ACTIONS = {0:'DEAL', 1:'PENDING', 2:'INSTANT', 3:'MARKET', 10:'CLOSE', 201:'MODIFY'}
TRADE_TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}
FILLING = {0:'FOK', 1:'IOC', 2:'RETURN'}
CMD_NAMES = {0:'AUTH', 1:'HEARTBEAT', 2:'AUTH_RESP', 4:'ACCOUNT', 7:'SUBSCRIBE_QUOTE',
             8:'QUOTE', 11:'RATES', 12:'TRADE', 17:'SYMBOL_SPEC', 19:'TRADE_EVENT',
             28:'LOGIN', 34:'SYMBOLS', 37:'NEWS'}

def decrypt_frame(hex_str, sk):
    data = bytes.fromhex(hex_str)
    if len(data) < 8: return None, None, None
    enc_len = struct.unpack_from('<I', data, 0)[0]
    version = struct.unpack_from('<I', data, 4)[0]
    enc_data = data[8:]
    dec = aes_decrypt(sk, enc_data)
    if dec is None: return None, None, None
    cmd = parse_cmd(dec)
    return enc_len, version, cmd

def parse_op(payload):
    if len(payload) < OP_SIZE: return None
    op = payload[:OP_SIZE]
    sym = op[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00')
    comment = op[164:228].decode('utf-16-le', errors='ignore').rstrip('\x00')
    return {
        'action_id': struct.unpack_from('<I', op, 0)[0],
        'trade_action': struct.unpack_from('<I', op, 4)[0],
        'symbol': sym, 'volume': struct.unpack_from('<Q', op, 72)[0],
        'digits': struct.unpack_from('<I', op, 80)[0],
        'trade_order': struct.unpack_from('<Q', op, 84)[0],
        'trade_type': struct.unpack_from('<I', op, 92)[0],
        'type_filling': struct.unpack_from('<I', op, 96)[0],
        'type_time': struct.unpack_from('<I', op, 100)[0],
        'type_flags': struct.unpack_from('<I', op, 104)[0],
        'type_reason': struct.unpack_from('<I', op, 108)[0],
        'price_order': struct.unpack_from('<d', op, 112)[0],
        'price_trigger': struct.unpack_from('<d', op, 120)[0],
        'price_sl': struct.unpack_from('<d', op, 128)[0],
        'price_tp': struct.unpack_from('<d', op, 136)[0],
        'price_deviation': struct.unpack_from('<I', op, 144)[0],
        'price_top': struct.unpack_from('<d', op, 148)[0],
        'price_bottom': struct.unpack_from('<d', op, 156)[0],
        'comment': comment,
        'trade_position': struct.unpack_from('<Q', op, 228)[0],
        'position_by': struct.unpack_from('<Q', op, 236)[0],
        'time_expiration': struct.unpack_from('<I', op, 244)[0],
    }

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        hook_js = """
        window.__ws_sent = [];
        window.__ws_recv = [];
        const _origSend = WebSocket.prototype.send;
        WebSocket.prototype.send = function(data) {
            let hex = '';
            if (data instanceof ArrayBuffer) hex = Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join('');
            else if (data && data.buffer instanceof ArrayBuffer) hex = Array.from(new Uint8Array(data.buffer)).map(b=>b.toString(16).padStart(2,'0')).join('');
            if (hex) window.__ws_sent.push({ t: Date.now(), hex: hex });
            return _origSend.call(this, data);
        };
        const _origWS = WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            let _onmsg = null;
            Object.defineProperty(ws, 'onmessage', {
                get: () => _onmsg,
                set: (fn) => { _onmsg = (evt) => {
                    if (evt.data instanceof ArrayBuffer) {
                        const hex = Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                        window.__ws_recv.push({ t: Date.now(), hex: hex });
                    }
                    return fn.call(ws, evt);
                }; }
            });
            const origAdd = ws.addEventListener.bind(ws);
            ws.addEventListener = function(type, listener, opts) {
                if (type === 'message') return origAdd(type, (evt) => {
                    if (evt.data instanceof ArrayBuffer) {
                        const hex = Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                        window.__ws_recv.push({ t: Date.now(), hex: hex });
                    }
                    return listener.call(ws, evt);
                }, opts);
                return origAdd(type, listener, opts);
            };
            return ws;
        };
        window.WebSocket.prototype = _origWS.prototype;
        window.WebSocket.CONNECTING = _origWS.CONNECTING;
        window.WebSocket.OPEN = _origWS.OPEN;
        window.WebSocket.CLOSING = _origWS.CLOSING;
        window.WebSocket.CLOSED = _origWS.CLOSED;
        """
        await context.add_init_script(hook_js)
        page = await context.new_page()

        print("[*] Opening web terminal...")
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(12)

        # Extract session key from recv frame 0 (auth response)
        all_recv = await page.evaluate("window.__ws_recv.map(f => f.hex)")
        session_key = None
        for hex_str in all_recv:
            data = bytes.fromhex(hex_str)
            enc_data = data[8:]
            dec = aes_decrypt(STATIC_KEY, enc_data)
            if dec:
                cmd = parse_cmd(dec)
                if cmd and len(cmd['payload']) > 98:
                    # Response format: [tag:2][cmd_id:2][res_code:1][resBody]
                    # payload = dec[4:] = [res_code:1][resBody]
                    # resBody = payload[1:]
                    # session_key = resBody[66:66+32] = payload[1+66:1+66+32] = payload[67:99]
                    res_code = cmd['payload'][0]
                    if res_code == 0:
                        session_key = cmd['payload'][67:99]
                        print(f"[+] Auth response found, res_code=0, session_key={session_key.hex()}")
                        break

        if not session_key:
            print("[!] FAILED to get session key")
            await browser.close()
            return

        print(f"[+] Session key: {session_key.hex()[:32]}...")
        print(f"[+] Session key length: {len(session_key)} bytes")

        # Decode ALL recv frames with session key
        print(f"\n{'='*70}")
        print(f"ALL {len(all_recv)} RECV FRAMES (decrypted with session key):")
        print(f"{'='*70}")
        for i, hex_str in enumerate(all_recv):
            enc_len, version, cmd = decrypt_frame(hex_str, session_key)
            if cmd:
                name = CMD_NAMES.get(cmd['cmd_id'], f'UNKNOWN({cmd["cmd_id"]})')
                print(f"  Recv {i:2d}: cmd_id={cmd['cmd_id']:3d} ({name:20s}) "
                      f"payload_len={len(cmd['payload'])}")
            else:
                print(f"  Recv {i:2d}: FAILED size={len(bytes.fromhex(hex_str))}")

        # Decode ALL sent frames with session key
        all_sent = await page.evaluate("window.__ws_sent.map(f => f.hex)")
        print(f"\n{'='*70}")
        print(f"ALL {len(all_sent)} SENT FRAMES (decrypted with session key):")
        print(f"{'='*70}")
        for i, hex_str in enumerate(all_sent):
            enc_len, version, cmd = decrypt_frame(hex_str, session_key)
            if cmd:
                name = CMD_NAMES.get(cmd['cmd_id'], f'UNKNOWN({cmd["cmd_id"]})')
                print(f"  Sent {i:2d}: cmd_id={cmd['cmd_id']:3d} ({name:20s}) "
                      f"payload_len={len(cmd['payload'])}")
                if cmd['cmd_id'] == 12:
                    op = parse_op(cmd['payload'])
                    if op:
                        print(f"           *** TRADE: action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')}) "
                              f"type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')}) "
                              f"vol={op['volume']} price={op['price_order']:.5f} "
                              f"fill={op['type_filling']}({FILLING.get(op['type_filling'],'?')}) "
                              f"time={op['type_time']}")
            else:
                print(f"  Sent {i:2d}: FAILED size={len(bytes.fromhex(hex_str))}")

        # Now open order dialog, click Buy, capture frames
        print(f"\n{'='*70}")
        print("OPENING ORDER DIALOG AND CLICKING BUY...")
        print(f"{'='*70}")

        await page.locator('text=Create New Order').first.click()
        await asyncio.sleep(2)

        sent_before = await page.evaluate("window.__ws_sent.length")
        recv_before = await page.evaluate("window.__ws_recv.length")

        # Click Buy by Market
        await page.locator('button:has-text("Buy by Market")').first.click()
        await asyncio.sleep(5)

        sent_after = await page.evaluate("window.__ws_sent.length")
        recv_after = await page.evaluate("window.__ws_recv.length")
        print(f"[+] Before: sent={sent_before} recv={recv_before}")
        print(f"[+] After:  sent={sent_after} recv={recv_after}")

        new_sent_hex = await page.evaluate(f"window.__ws_sent.slice({sent_before}).map(f=>f.hex)")
        new_recv_hex = await page.evaluate(f"window.__ws_recv.slice({recv_before}).map(f=>f.hex)")

        print(f"\n--- NEW SENT FRAMES ({len(new_sent_hex)}) ---")
        for i, hex_str in enumerate(new_sent_hex):
            enc_len, version, cmd = decrypt_frame(hex_str, session_key)
            if cmd:
                name = CMD_NAMES.get(cmd['cmd_id'], f'UNKNOWN({cmd["cmd_id"]})')
                print(f"  Sent {i}: cmd_id={cmd['cmd_id']} ({name}) payload_len={len(cmd['payload'])}")
                if cmd['cmd_id'] == 12:
                    op = parse_op(cmd['payload'])
                    if op:
                        print(f"    TRADE: action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')}) "
                              f"type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')}) "
                              f"sym={op['symbol']} vol={op['volume']} price={op['price_order']:.5f} "
                              f"fill={op['type_filling']}({FILLING.get(op['type_filling'],'?')}) "
                              f"time={op['type_time']} flags={op['type_flags']} dev={op['price_deviation']}")
                elif cmd['cmd_id'] == 1:
                    print(f"    HEARTBEAT")
            else:
                data = bytes.fromhex(hex_str)
                print(f"  Sent {i}: FAILED size={len(data)} hex={hex_str[:60]}...")

        print(f"\n--- NEW RECV FRAMES ({len(new_recv_hex)}) ---")
        for i, hex_str in enumerate(new_recv_hex):
            enc_len, version, cmd = decrypt_frame(hex_str, session_key)
            if cmd:
                name = CMD_NAMES.get(cmd['cmd_id'], f'UNKNOWN({cmd["cmd_id"]})')
                print(f"  Recv {i}: cmd_id={cmd['cmd_id']} ({name}) payload_len={len(cmd['payload'])}")
                if cmd['cmd_id'] == 19:
                    body = cmd['payload']
                    ap_off = 4 + OP_SIZE
                    if len(body) >= ap_off + 4:
                        op = parse_op(body[4:4+OP_SIZE])
                        ap_retcode = struct.unpack_from('<I', body, ap_off)[0]
                        ap_deal = struct.unpack_from('<q', body, ap_off+4)[0]
                        ap_order = struct.unpack_from('<q', body, ap_off+12)[0]
                        ap_vol = struct.unpack_from('<q', body, ap_off+20)[0]
                        ap_price = struct.unpack_from('<d', body, ap_off+28)[0]
                        print(f"    *** TRADE_EVENT ***")
                        if op:
                            print(f"    Op echo: action={op['trade_action']} type={op['trade_type']} "
                                  f"sym={op['symbol']} vol={op['volume']} price={op['price_order']:.5f}")
                        print(f"    Ap result: retcode={ap_retcode} deal={ap_deal} order={ap_order} "
                              f"vol={ap_vol} price={ap_price:.5f}")
                elif cmd['cmd_id'] == 8:
                    # QUOTE event
                    if len(cmd['payload']) >= 50:
                        print(f"    (quote data)")
            else:
                print(f"  Recv {i}: FAILED size={len(bytes.fromhex(hex_str))}")

        # Screenshot
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_final.png')

        await browser.close()

asyncio.run(main())
