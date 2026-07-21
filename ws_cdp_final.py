#!/usr/bin/env python3
"""
Use CDP Network.webSocketFrameSent/Received enabled BEFORE page load.
This captures ALL WebSocket frames at browser level, bypassing JS hooks.
"""
import asyncio, struct, time, base64
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)
OP_SIZE = 248

def aes_dec(key, ct):
    if not ct or len(ct) % 16 != 0: return None
    return AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)

def aes_dec_unpad(key, ct):
    pt = aes_dec(key, ct)
    if pt is None: return None
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

TRADE_ACTIONS = {0:'DEAL', 1:'PENDING', 2:'INSTANT', 3:'MARKET', 10:'CLOSE', 201:'MODIFY'}
TRADE_TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}
FILLING = {0:'FOK', 1:'IOC', 2:'RETURN'}
CMD_NAMES = {0:'AUTH', 1:'HEARTBEAT', 3:'ACCOUNT_FULL', 4:'ACCOUNT',
             5:'DEALS', 7:'SUBSCRIBE_QUOTE', 8:'QUOTE', 9:'SYMBOLS_FULL',
             11:'RATES', 12:'TRADE', 17:'SYMBOL_SPEC', 18:'SYMBOLS_COMPACT',
             19:'TRADE_EVENT', 28:'LOGIN', 34:'SYMBOLS', 51:'PING'}

def parse_op(payload):
    if len(payload) < OP_SIZE: return None
    op = payload[:OP_SIZE]
    return {
        'trade_action': struct.unpack_from('<I', op, 4)[0],
        'symbol': op[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        'volume': struct.unpack_from('<Q', op, 72)[0],
        'digits': struct.unpack_from('<I', op, 80)[0],
        'trade_type': struct.unpack_from('<I', op, 92)[0],
        'type_filling': struct.unpack_from('<I', op, 96)[0],
        'type_time': struct.unpack_from('<I', op, 100)[0],
        'type_flags': struct.unpack_from('<I', op, 104)[0],
        'price_order': struct.unpack_from('<d', op, 112)[0],
        'price_trigger': struct.unpack_from('<d', op, 120)[0],
        'price_sl': struct.unpack_from('<d', op, 128)[0],
        'price_tp': struct.unpack_from('<d', op, 136)[0],
        'price_deviation': struct.unpack_from('<I', op, 144)[0],
        'trade_position': struct.unpack_from('<Q', op, 228)[0],
        'comment': op[164:228].decode('utf-16-le', errors='ignore').rstrip('\x00'),
    }

def try_decrypt(hex_str, sk):
    data = bytes.fromhex(hex_str) if isinstance(hex_str, str) else hex_str
    if len(data) < 8: return None
    for name, key in [("SESSION", sk), ("STATIC", STATIC_KEY)]:
        if key is None: continue
        try:
            dec = aes_dec_unpad(key, data[8:])
            if dec and len(dec) >= 4:
                cmd_id = struct.unpack_from('<H', dec, 2)[0]
                if cmd_id in CMD_NAMES:
                    return {'key': name, 'cmd_id': cmd_id, 'payload': dec[4:], 'raw': dec}
        except: pass
    return None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})
        page = await context.new_page()

        # Create CDP session BEFORE navigation
        cdp = await context.new_cdp_session(page)

        # Collect WS frames from CDP
        ws_sent_frames = []
        ws_recv_frames = []

        def on_frame_sent(params):
            payload = params.get('response', {}).get('payload', '')
            encoding = params.get('response', {}).get('payloadEncoding', '')
            if encoding == 'base64' and payload:
                data = base64.b64decode(payload)
            elif payload:
                data = payload.encode('latin-1') if isinstance(payload, str) else bytes(payload)
            else:
                return
            ws_sent_frames.append(data)

        def on_frame_recv(params):
            payload = params.get('response', {}).get('payload', '')
            encoding = params.get('response', {}).get('payloadEncoding', '')
            if encoding == 'base64' and payload:
                data = base64.b64decode(payload)
            elif payload:
                data = payload.encode('latin-1') if isinstance(payload, str) else bytes(payload)
            else:
                return
            ws_recv_frames.append(data)

        cdp.on('Network.webSocketFrameSent', on_frame_sent)
        cdp.on('Network.webSocketFrameReceived', on_frame_recv)
        await cdp.send('Network.enable')

        print("[*] Opening web terminal...")
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(12)

        print(f"[+] After login: {len(ws_sent_frames)} sent, {len(ws_recv_frames)} recv via CDP")

        # Extract session key from first recv frame (auth response)
        session_key = None
        for data in ws_recv_frames[:5]:
            if len(data) >= 103:
                try:
                    dec = aes_dec(STATIC_KEY, data[8:])
                    if dec and len(dec) >= 103 and struct.unpack_from('<H', dec, 2)[0] == 0:
                        session_key = dec[71:103]
                        print(f"[+] Session key: {session_key.hex()}")
                        break
                except: pass

        if not session_key:
            print("[!] No session key found in CDP frames")
            # Fallback: use JS hook to get session key
            js_recv = await page.evaluate("""() => {
                const frames = [];
                const origWS = WebSocket;
                // The session key must be in one of the initial recv frames
                return null;
            }""")
            await browser.close()
            return

        # Verify
        for data in ws_recv_frames[:10]:
            r = try_decrypt(data.hex(), session_key)
            if r:
                print(f"  Verified: cmd_id={r['cmd_id']} ({CMD_NAMES.get(r['cmd_id'], '?')})")

        # Record state before Buy
        sent_before = len(ws_sent_frames)
        recv_before = len(ws_recv_frames)

        # Open order dialog
        print("\n[*] Opening order dialog...")
        await page.locator('text=Create New Order').first.click()
        await asyncio.sleep(3)

        # Click Buy by Market
        buy_btns = await page.evaluate("""() => {
            const result = [];
            document.querySelectorAll('button').forEach(btn => {
                const rect = btn.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && (btn.textContent||'').trim().includes('Buy')) {
                    result.push({
                        text: (btn.textContent||'').trim(),
                        x: Math.round(rect.x + rect.width/2),
                        y: Math.round(rect.y + rect.height/2)
                    });
                }
            });
            return result;
        }""")

        if not buy_btns:
            print("[!] No Buy button found!")
            await browser.close()
            return

        buy = buy_btns[0]
        print(f"[*] Clicking '{buy['text']}' at ({buy['x']},{buy['y']})...")
        await page.mouse.click(buy['x'], buy['y'])
        await asyncio.sleep(8)

        # Screenshot
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_cdp_trade.png')

        # Analyze new frames
        new_sent = ws_sent_frames[sent_before:]
        new_recv = ws_recv_frames[recv_before:]

        print(f"\n[+] After Buy: {len(new_sent)} new sent, {len(new_recv)} new recv")

        # Decrypt all new sent frames
        print(f"\n{'='*70}")
        print(f"NEW SENT FRAMES ({len(new_sent)}):")
        print(f"{'='*70}")
        for i, data in enumerate(new_sent):
            hex_str = data.hex()
            r = try_decrypt(hex_str, session_key)
            if r:
                cmd_name = CMD_NAMES.get(r['cmd_id'], f'CMD_{r["cmd_id"]}')
                print(f"\n  Sent {i}: size={len(data)} cmd_id={r['cmd_id']} ({cmd_name})")
                if r['cmd_id'] == 12:
                    op = parse_op(r['payload'])
                    if op:
                        print(f"    *** TRADE COMMAND ***")
                        print(f"    trade_action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')})")
                        print(f"    symbol='{op['symbol']}'")
                        print(f"    volume={op['volume']} ({op['volume']/100000000:.2f} lots)")
                        print(f"    trade_type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')})")
                        print(f"    type_filling={op['type_filling']}({FILLING.get(op['type_filling'],'?')})")
                        print(f"    type_time={op['type_time']}")
                        print(f"    type_flags={op['type_flags']}")
                        print(f"    price_order={op['price_order']:.5f}")
                        print(f"    price_trigger={op['price_trigger']:.5f}")
                        print(f"    price_sl={op['price_sl']:.5f} tp={op['price_tp']:.5f}")
                        print(f"    deviation={op['price_deviation']}")
                        print(f"    comment='{op['comment']}'")
                        print(f"\n    --- FULL OP PAYLOAD FIELD-BY-FIELD ---")
                        full_op = r['payload'][:OP_SIZE]
                        FIELDS = [
                            (0,4,'action_id','I'), (4,8,'trade_action','I'), (8,72,'symbol','s'),
                            (72,80,'volume','Q'), (80,84,'digits','I'), (84,92,'trade_order','Q'),
                            (92,96,'trade_type','I'), (96,100,'type_filling','I'),
                            (100,104,'type_time','I'), (104,108,'type_flags','I'),
                            (108,112,'type_reason','I'), (112,120,'price_order','d'),
                            (120,128,'price_trigger','d'), (128,136,'price_sl','d'),
                            (136,144,'price_tp','d'), (144,148,'price_deviation','I'),
                            (148,156,'price_top','d'), (156,164,'price_bottom','d'),
                            (164,228,'comment','s'), (228,236,'trade_position','Q'),
                            (236,244,'position_by','Q'), (244,248,'time_expiration','I'),
                        ]
                        for start, end, name, typ in FIELDS:
                            chunk = full_op[start:end]
                            if typ == 's':
                                val = chunk.decode('utf-16-le', errors='ignore').rstrip('\x00')
                                print(f"    [{start:3d}-{end:3d}] {name:20s} = '{val}'")
                            elif typ == 'd':
                                val = struct.unpack_from('<d', chunk)[0]
                                print(f"    [{start:3d}-{end:3d}] {name:20s} = {val:.5f}")
                            elif typ == 'Q':
                                val = struct.unpack_from('<Q', chunk)[0]
                                print(f"    [{start:3d}-{end:3d}] {name:20s} = {val}")
                            elif typ == 'I':
                                val = struct.unpack_from('<I', chunk)[0]
                                print(f"    [{start:3d}-{end:3d}] {name:20s} = {val}")
            else:
                print(f"  Sent {i}: size={len(data)} hex={hex_str[:80]}...")

        # Decrypt all new recv frames
        print(f"\n{'='*70}")
        print(f"NEW RECV FRAMES (non-quote, non-ping):")
        print(f"{'='*70}")
        for i, data in enumerate(new_recv):
            hex_str = data.hex()
            r = try_decrypt(hex_str, session_key)
            if r and r['cmd_id'] not in [8, 51]:
                cmd_name = CMD_NAMES.get(r['cmd_id'], f'CMD_{r["cmd_id"]}')
                if r['cmd_id'] == 19:
                    print(f"\n  *** TRADE_EVENT ***")
                    body = r['payload']
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
                        comment = body[ap_off+64:ap_off+128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        if op:
                            print(f"    Echo: action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')}) "
                                  f"type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')}) "
                                  f"sym={op['symbol']} vol={op['volume']} price={op['price_order']:.5f}")
                        print(f"    retcode={ap_retcode} deal={ap_deal} order={ap_order}")
                        print(f"    vol={ap_vol} price={ap_price:.5f} bid={ap_bid:.5f} ask={ap_ask:.5f}")
                        print(f"    comment='{comment}'")
                elif r['cmd_id'] != 51:
                    print(f"  Recv: size={len(data)} cmd_id={r['cmd_id']} ({cmd_name}) len={len(r['payload'])}")

        await browser.close()

asyncio.run(main())
