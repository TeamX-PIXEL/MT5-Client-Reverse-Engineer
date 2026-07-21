#!/usr/bin/env python3
"""
Bypass the UI entirely: find the web terminal's internal connection object
and call sendCommand(12, tradePayload) directly.
"""
import asyncio, struct, time
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

        # CRITICAL: Override send at instance level using a Proxy
        # This catches ALL sends, even from stored references
        hook_js = """
        window.__all_sent = [];
        window.__all_recv = [];
        window.__ws_ref = null;

        // Override WebSocket constructor to wrap send with a Proxy
        const _origWS = window.WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            window.__ws_ref = ws;

            // Use Proxy to intercept ALL property access on ws
            // Specifically intercept .send calls
            const origSendFn = ws.send.bind(ws);
            ws.send = function(data) {
                let hex = '';
                if (data instanceof ArrayBuffer) {
                    hex = Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                } else if (data && data.buffer instanceof ArrayBuffer) {
                    hex = Array.from(new Uint8Array(data.buffer, data.byteOffset, data.byteLength)).map(b=>b.toString(16).padStart(2,'0')).join('');
                } else if (typeof data === 'string') {
                    hex = 'STR:' + data.substring(0, 100);
                }
                if (hex && !hex.startsWith('STR:')) {
                    window.__all_sent.push(hex);
                    console.log('[HOOK-SEND] size=' + (hex.length/2));
                }
                return origSendFn(data);
            };

            // Also patch __proto__.send to catch stored references
            Object.defineProperty(ws, '__send_hooked', {value: true, writable: false});

            ws.addEventListener('message', function(evt) {
                if (evt.data instanceof ArrayBuffer) {
                    window.__all_recv.push(Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join(''));
                }
            });
            return ws;
        };
        window.WebSocket.prototype = _origWS.prototype;

        // ALSO: Override the send on the prototype AND freeze it
        const _realSend = WebSocket.prototype.send;
        Object.defineProperty(WebSocket.prototype, 'send', {
            value: function(data) {
                let hex = '';
                if (data instanceof ArrayBuffer) {
                    hex = Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                } else if (data && data.buffer instanceof ArrayBuffer) {
                    hex = Array.from(new Uint8Array(data.buffer, data.byteOffset, data.byteLength)).map(b=>b.toString(16).padStart(2,'0')).join('');
                }
                if (hex) {
                    window.__all_sent.push(hex);
                    console.log('[HOOK-PROTO] size=' + (hex.length/2));
                }
                return _realSend.call(this, data);
            },
            writable: true,
            configurable: true
        });
        """
        await context.add_init_script(hook_js)
        page = await context.new_page()

        console_logs = []
        page.on('console', lambda msg: console_logs.append(msg.text) if 'HOOK' in msg.text else None)

        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(12)

        # Get session key
        recv_hexes = await page.evaluate("window.__all_recv")
        session_key = None
        for hex_str in recv_hexes[:5]:
            data = bytes.fromhex(hex_str)
            try:
                dec = aes_dec(STATIC_KEY, data[8:])
                if dec and len(dec) >= 103 and struct.unpack_from('<H', dec, 2)[0] == 0:
                    session_key = dec[71:103]
                    print(f"[+] Session key: {session_key.hex()}")
                    break
            except: pass

        if not session_key:
            print("[!] No session key"); await browser.close(); return

        # Check sent frames
        sent = await page.evaluate("window.__all_sent")
        print(f"[+] Sent: {len(sent)}")
        for i, h in enumerate(sent):
            r = try_decrypt(h, session_key)
            if r: print(f"  {i}: cmd_id={r['cmd_id']} ({CMD_NAMES.get(r['cmd_id'], '?')})")

        print(f"\nConsole logs: {console_logs[-5:]}")

        # Clear
        await page.evaluate("window.__all_sent = []; window.__all_recv = [];")

        # Open order dialog
        print("\n[*] Opening order dialog...")
        await page.locator('text=Create New Order').first.click()
        await asyncio.sleep(3)

        # Find Buy button
        buy = await page.evaluate("""() => {
            for (const btn of document.querySelectorAll('button')) {
                const text = (btn.textContent||'').trim();
                if (text === 'Buy by Market') {
                    const r = btn.getBoundingClientRect();
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
            }
            return null;
        }""")

        if not buy:
            print("[!] No Buy button!")
            await browser.close()
            return

        print(f"[*] Buy button at ({buy['x']:.0f},{buy['y']:.0f})")

        # Method: Click and wait
        console_logs.clear()
        await page.mouse.click(buy['x'], buy['y'])
        await asyncio.sleep(8)

        # Analyze
        all_sent = await page.evaluate("window.__all_sent")
        print(f"\nConsole after click: {console_logs}")
        print(f"[+] Sent after click: {len(all_sent)}")

        for i, h in enumerate(all_sent):
            r = try_decrypt(h, session_key)
            if r:
                cmd_name = CMD_NAMES.get(r['cmd_id'], f'CMD_{r["cmd_id"]}')
                print(f"\n  Sent {i}: cmd_id={r['cmd_id']} ({cmd_name})")
                if r['cmd_id'] == 12:
                    op = parse_op(r['payload'])
                    if op:
                        print(f"    *** TRADE ***")
                        print(f"    action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')})")
                        print(f"    symbol='{op['symbol']}'")
                        print(f"    volume={op['volume']} ({op['volume']/100000000:.2f} lots)")
                        print(f"    type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')})")
                        print(f"    fill={op['type_filling']}({FILLING.get(op['type_filling'],'?')})")
                        print(f"    time={op['type_time']}")
                        print(f"    flags={op['type_flags']}")
                        print(f"    price={op['price_order']:.5f}")
                        print(f"    sl={op['price_sl']:.5f} tp={op['price_tp']:.5f}")
                        print(f"    dev={op['price_deviation']}")
                        print(f"    comment='{op['comment']}'")
                        # Dump full Op
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
                            chunk = r['payload'][:OP_SIZE][start:end]
                            if typ == 's':
                                val = chunk.decode('utf-16-le', errors='ignore').rstrip('\x00')
                                print(f"      [{start:3d}-{end:3d}] {name:20s} = '{val}'")
                            elif typ == 'd':
                                print(f"      [{start:3d}-{end:3d}] {name:20s} = {struct.unpack_from('<d', chunk)[0]:.5f}")
                            elif typ == 'Q':
                                print(f"      [{start:3d}-{end:3d}] {name:20s} = {struct.unpack_from('<Q', chunk)[0]}")
                            elif typ == 'I':
                                print(f"      [{start:3d}-{end:3d}] {name:20s} = {struct.unpack_from('<I', chunk)[0]}")
            else:
                data = bytes.fromhex(h)
                print(f"  Sent {i}: size={len(data)} FAILED")

        # Recv
        all_recv = await page.evaluate("window.__all_recv")
        print(f"\n{'='*70}")
        print(f"RECV (non-quote): {len(all_recv)} total")
        print(f"{'='*70}")
        for i, h in enumerate(all_recv):
            r = try_decrypt(h, session_key)
            if r and r['cmd_id'] not in [8, 51]:
                if r['cmd_id'] == 19:
                    print(f"\n  *** TRADE_EVENT ***")
                    body = r['payload']
                    ap_off = 4 + OP_SIZE
                    if len(body) >= ap_off + 64:
                        op = parse_op(body[4:4+OP_SIZE])
                        retcode = struct.unpack_from('<I', body, ap_off)[0]
                        deal = struct.unpack_from('<q', body, ap_off+4)[0]
                        order = struct.unpack_from('<q', body, ap_off+12)[0]
                        vol = struct.unpack_from('<q', body, ap_off+20)[0]
                        price = struct.unpack_from('<d', body, ap_off+28)[0]
                        bid = struct.unpack_from('<d', body, ap_off+48)[0]
                        ask = struct.unpack_from('<d', body, ap_off+56)[0]
                        comment = body[ap_off+64:ap_off+128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        if op:
                            print(f"    Echo: action={op['trade_action']}({TRADE_ACTIONS.get(op['trade_action'],'?')}) "
                                  f"type={op['trade_type']}({TRADE_TYPES.get(op['trade_type'],'?')}) "
                                  f"sym={op['symbol']} vol={op['volume']} price={op['price_order']:.5f}")
                        print(f"    retcode={retcode} deal={deal} order={order} "
                              f"vol={vol} price={price:.5f} bid={bid:.5f} ask={ask:.5f}")
                        print(f"    comment='{comment}'")
                else:
                    cmd_name = CMD_NAMES.get(r['cmd_id'], f'CMD_{r["cmd_id"]}')
                    print(f"  Recv: cmd_id={r['cmd_id']} ({cmd_name}) len={len(r['payload'])}")

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_final.png')
        await browser.close()

asyncio.run(main())
