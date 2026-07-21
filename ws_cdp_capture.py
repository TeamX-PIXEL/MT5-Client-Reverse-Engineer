#!/usr/bin/env python3
"""
CDP capture with Network enabled BEFORE page load.
Also hook WebSocket via add_init_script for session key extraction.
"""
import asyncio, struct, time, base64
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

TRADE_ACTIONS = {0:'DEAL', 1:'PENDING', 2:'INSTANT', 3:'MARKET', 10:'CLOSE', 201:'MODIFY'}
TRADE_TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}
FILLING = {0:'FOK', 1:'IOC', 2:'RETURN'}
CMD_NAMES = {0:'AUTH', 1:'HEARTBEAT', 2:'AUTH_RESP', 3:'ACCOUNT_FULL', 4:'ACCOUNT',
             5:'DEALS', 7:'SUBSCRIBE_QUOTE', 8:'QUOTE', 9:'SYMBOLS_FULL',
             11:'RATES', 12:'TRADE', 17:'SYMBOL_SPEC', 18:'SYMBOLS_COMPACT',
             19:'TRADE_EVENT', 28:'LOGIN', 34:'SYMBOLS', 37:'NEWS', 51:'PING'}

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

def decrypt_and_parse(hex_str, sk):
    data = bytes.fromhex(hex_str) if isinstance(hex_str, str) else hex_str
    if len(data) < 8: return None
    enc_len = struct.unpack_from('<I', data, 0)[0]
    version = struct.unpack_from('<I', data, 4)[0]
    enc_data = data[8:]
    for name, key in [("STATIC", STATIC_KEY), ("SESSION", sk)]:
        if key is None: continue
        try:
            dec = aes_decrypt(key, enc_data)
            if dec and len(dec) >= 4:
                cmd_id = struct.unpack_from('<H', dec, 2)[0]
                return {'key': name, 'cmd_id': cmd_id, 'payload': dec[4:], 'raw': dec}
        except: pass
    return None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        # Hook to extract session key
        hook_js = """
        window.__session_key_hex = null;
        window.__trade_sent = null;

        const _origSend = WebSocket.prototype.send;
        WebSocket.prototype.send = function(data) {
            if (data instanceof ArrayBuffer) {
                const hex = Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                // Store last 50 sent frames with their full hex
                if (!window.__all_sent) window.__all_sent = [];
                window.__all_sent.push(hex);
                if (window.__all_sent.length > 100) window.__all_sent.shift();
            }
            return _origSend.call(this, data);
        };

        const _origWS = WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            ws.addEventListener('message', function(evt) {
                if (evt.data instanceof ArrayBuffer) {
                    const hex = Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                    if (!window.__all_recv) window.__all_recv = [];
                    window.__all_recv.push(hex);
                    if (window.__all_recv.length > 200) window.__all_recv.shift();
                }
            });
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

        # Enable CDP Network BEFORE navigation
        cdp = await context.new_cdp_session(page)
        await cdp.send('Network.enable')

        ws_frames = []

        def on_ws_sent(params):
            payload_b64 = params.get('response', {}).get('payload', '')
            if payload_b64:
                data = base64.b64decode(payload_b64) if params.get('response', {}).get('payloadEncoding') == 'base64' else payload_b64.encode()
                ws_frames.append({'dir': 'SENT', 'data': data, 'hex': data.hex()})

        def on_ws_recv(params):
            payload_b64 = params.get('response', {}).get('payload', '')
            if payload_b64:
                data = base64.b64decode(payload_b64) if params.get('response', {}).get('payloadEncoding') == 'base64' else payload_b64.encode()
                ws_frames.append({'dir': 'RECV', 'data': data, 'hex': data.hex()})

        cdp.on('Network.webSocketFrameSent', on_ws_sent)
        cdp.on('Network.webSocketFrameReceived', on_ws_recv)

        print("[*] Opening web terminal...")
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        print("[*] Logging in...")
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(15)

        print(f"[+] After login: {len(ws_frames)} CDP frames, "
              f"{len(await page.evaluate('window.__all_sent || []'))} JS sent, "
              f"{len(await page.evaluate('window.__all_recv || []'))} JS recv")

        # Extract session key from JS recv frames
        session_key = None
        js_recv = await page.evaluate("window.__all_recv || []")
        for hex_str in js_recv:
            parsed = decrypt_and_parse(hex_str, None)
            if parsed and parsed['cmd_id'] == 0:  # Auth response
                payload = parsed['payload']
                # payload = [res_code:1][resBody]
                # resBody[66:98] = session key
                if len(payload) > 98 and payload[0] == 0:
                    session_key = payload[67:99]
                    print(f"[+] Session key: {session_key.hex()}")
                    break

        if not session_key:
            print("[!] Could not find session key in JS recv frames")
            # Try CDP frames
            for f in ws_frames:
                if f['dir'] == 'RECV':
                    parsed = decrypt_and_parse(f['hex'], None)
                    if parsed and parsed['cmd_id'] == 0:
                        payload = parsed['payload']
                        if len(payload) > 98 and payload[0] == 0:
                            session_key = payload[67:99]
                            print(f"[+] Session key from CDP: {session_key.hex()}")
                            break

        if not session_key:
            print("[!] FAILED to extract session key")
            await browser.close()
            return

        # Clear frames
        ws_frames.clear()
        await page.evaluate("window.__all_sent = []; window.__all_recv = [];")

        # Open order dialog
        print("\n[*] Opening order dialog...")
        await page.locator('text=Create New Order').first.click()
        await asyncio.sleep(2)

        # Select EURUSDm
        print("[*] Selecting EURUSDm...")
        sym_input = page.locator('input[placeholder="Search symbol"]')
        if await sym_input.count() > 0:
            await sym_input.first.click()
            await sym_input.first.fill('')
            await asyncio.sleep(0.5)
            await sym_input.first.fill('EURUSDm')
            await asyncio.sleep(1)
            # Click on EURUSDm in the dropdown
            try:
                await page.locator('text=EURUSDm').first.click(timeout=3000)
                print("[+] Selected EURUSDm")
            except:
                print("[!] Could not click EURUSDm in dropdown")

        await asyncio.sleep(1)
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_cdp2_dialog.png')

        # Clear frames before Buy click
        ws_frames.clear()
        sent_before = len(await page.evaluate("window.__all_sent || []"))
        recv_before = len(await page.evaluate("window.__all_recv || []"))

        print("[*] Clicking 'Buy by Market'...")
        try:
            await page.locator('button:has-text("Buy by Market")').first.click(timeout=5000)
        except:
            await page.mouse.click(280, 400)
        print("[+] Clicked Buy")

        await asyncio.sleep(5)
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_cdp2_after_buy.png')

        # Get JS frames
        js_sent = await page.evaluate("window.__all_sent || []")
        js_recv = await page.evaluate("window.__all_recv || []")
        new_sent = js_sent[sent_before:]
        new_recv = js_recv[recv_before:]

        print(f"\n[+] CDP frames after Buy: {len(ws_frames)}")
        print(f"[+] JS sent after Buy: {len(new_sent)}")
        print(f"[+] JS recv after Buy: {len(new_recv)}")

        # Decrypt and parse ALL new frames (JS source is more reliable)
        print(f"\n{'='*70}")
        print(f"NEW SENT FRAMES ({len(new_sent)}):")
        print(f"{'='*70}")
        for i, hex_str in enumerate(new_sent):
            parsed = decrypt_and_parse(hex_str, session_key)
            if parsed:
                cmd_name = CMD_NAMES.get(parsed['cmd_id'], f'CMD_{parsed["cmd_id"]}')
                print(f"\n  Sent {i}: cmd_id={parsed['cmd_id']} ({cmd_name}) key={parsed['key']}")
                if parsed['cmd_id'] == 12:
                    op = parse_op(parsed['payload'])
                    if op:
                        print(f"    *** TRADE COMMAND ***")
                        print(f"    action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')})")
                        print(f"    symbol='{op['symbol']}'")
                        print(f"    volume={op['volume']} ({op['volume']/100000:.2f} lots)")
                        print(f"    trade_type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')})")
                        print(f"    type_filling={op['type_filling']}({FILLING.get(op['type_filling'],'?')})")
                        print(f"    type_time={op['type_time']}")
                        print(f"    type_flags={op['type_flags']}")
                        print(f"    price_order={op['price_order']:.5f}")
                        print(f"    price_sl={op['price_sl']:.5f} tp={op['price_tp']:.5f}")
                        print(f"    deviation={op['price_deviation']}")
                        print(f"    comment='{op['comment']}'")
                        print(f"    FULL OP HEX (248 bytes):")
                        full_op = parsed['payload'][:OP_SIZE]
                        for off in range(0, OP_SIZE, 16):
                            chunk = full_op[off:off+16]
                            # Also show known field names
                            print(f"      [{off:3d}] {chunk.hex()}")
            else:
                data = bytes.fromhex(hex_str)
                print(f"  Sent {i}: size={len(data)} hex={hex_str[:60]}...")

        print(f"\n{'='*70}")
        print(f"NEW RECV FRAMES ({len(new_recv)}):")
        print(f"{'='*70}")
        for i, hex_str in enumerate(new_recv):
            parsed = decrypt_and_parse(hex_str, session_key)
            if parsed:
                cmd_name = CMD_NAMES.get(parsed['cmd_id'], f'CMD_{parsed["cmd_id"]}')
                print(f"\n  Recv {i}: cmd_id={parsed['cmd_id']} ({cmd_name}) key={parsed['key']}")
                if parsed['cmd_id'] == 19:
                    body = parsed['payload']
                    ap_off = 4 + OP_SIZE
                    if len(body) >= ap_off + 64:
                        op = parse_op(body[4:4+OP_SIZE])
                        ap_retcode = struct.unpack_from('<I', body, ap_off)[0]
                        ap_deal = struct.unpack_from('<q', body, ap_off+4)[0]
                        ap_order = struct.unpack_from('<q', body, ap_off+12)[0]
                        ap_vol = struct.unpack_from('<q', body, ap_off+20)[0]
                        ap_price = struct.unpack_from('<d', body, ap_off+28)[0]
                        ap_bid = struct.unpack_from('<d', body, ap_off+48)[0]
                        ap_ask = struct.unpack_from('<d', body, ap_off+56)[0]
                        comment_raw = body[ap_off+64:ap_off+64+64]
                        comment = comment_raw.decode('utf-16-le', errors='ignore').rstrip('\x00')
                        print(f"    *** TRADE_EVENT ***")
                        if op:
                            print(f"    Echo: action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')}) "
                                  f"type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')}) "
                                  f"sym={op['symbol']} vol={op['volume']} price={op['price_order']:.5f}")
                        print(f"    retcode={ap_retcode} deal={ap_deal} order={ap_order}")
                        print(f"    vol={ap_vol} price={ap_price:.5f} bid={ap_bid:.5f} ask={ap_ask:.5f}")
                        print(f"    comment='{comment}'")
                elif parsed['cmd_id'] != 8:  # skip quotes
                    print(f"    payload_len={len(parsed['payload'])}")
            else:
                data = bytes.fromhex(hex_str)
                print(f"  Recv {i}: size={len(data)} hex={hex_str[:60]}...")

        await browser.close()

asyncio.run(main())
