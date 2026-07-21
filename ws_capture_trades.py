#!/usr/bin/env python3
"""
Intercept WebSocket frames from the web terminal when placing trades.
Uses Playwright to open the real web terminal, hook WS.send,
capture binary frames, decrypt, and show the Op payload.
"""
import asyncio, struct, time, json
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_decrypt(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

ERROR_CODES = {
    0: 'OK', 1: 'OK', 2: 'common error', 3: 'invalid params', 4: 'invalid params',
    10009: 'done', 10010: 'done', 10011: 'common error',
    10013: 'invalid parameters', 10014: 'invalid volume',
    10015: 'invalid price', 10016: 'invalid SL/TP', 10017: 'account restricted',
    10021: 'no price', 10030: 'invalid trade_action', 10035: 'filling not allowed',
}
OP_NAMES = {
    0: 'action_id(u32)', 4: 'trade_action(u32)', 8: 'symbol(UTF16,64B)',
    72: 'volume(u64)', 80: 'digits(u32)', 84: 'trade_order(u64)',
    92: 'trade_type(u32)', 96: 'type_filling(u32)', 100: 'type_time(u32)',
    104: 'type_flags(u32)', 108: 'type_reason(u32)',
    112: 'price_order(f64)', 120: 'price_trigger(f64)',
    128: 'price_sl(f64)', 136: 'price_tp(f64)', 144: 'price_deviation(u32)',
    148: 'price_top(f64)', 156: 'price_bottom(f64)',
    164: 'comment(UTF16,64B)', 228: 'trade_position(u64)', 236: 'position_by(u64)',
    244: 'time_expiration(u32)',
}
TRADE_ACTIONS = {0: 'DEAL', 1: 'PENDING', 2: 'INSTANT', 3: 'MARKET', 10: 'CLOSE', 201: 'MODIFY'}
TRADE_TYPES = {0: 'BUY', 1: 'SELL', 2: 'BUY_LIMIT', 3: 'SELL_LIMIT', 4: 'BUY_STOP', 5: 'SELL_STOP'}
FILLING = {0: 'FOK', 1: 'IOC', 2: 'RETURN'}
TIME_TYPE = {0: 'GTC', 1: 'DAY', 2: 'SPECIFIED', 3: 'SPECIFIED_DAY'}

# Store for captured frames
captured_sent = []
session_key = None

async def main():
    global session_key

    hook_js = """
    window.__captured_ws_frames = [];
    window.__session_key_hex = null;
    window.__ws_instances = [];

    const origWS = window.WebSocket;
    window.WebSocket = function(...args) {
        const ws = new origWS(...args);
        window.__ws_instances.push(ws);

        const origSend = ws.send.bind(ws);
        ws.send = function(data) {
            if (data instanceof ArrayBuffer || data instanceof Uint8Array) {
                const bytes = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
                window.__captured_ws_frames.push({
                    time: Date.now(),
                    dir: 'sent',
                    size: bytes.length,
                    hex: Array.from(bytes).map(b => b.toString(16).padStart(2,'0')).join(''),
                    raw: Array.from(bytes)
                });
            }
            return origSend(data);
        };

        const origOnMessage = ws.onmessage;
        ws.addEventListener('message', function(evt) {
            if (evt.data instanceof ArrayBuffer) {
                const bytes = new Uint8Array(evt.data);
                window.__captured_ws_frames.push({
                    time: Date.now(),
                    dir: 'recv',
                    size: bytes.length,
                    hex: Array.from(bytes).map(b => b.toString(16).padStart(2,'0')).join(''),
                    raw: Array.from(bytes)
                });
            }
        });

        return ws;
    };
    window.WebSocket.prototype = origWS.prototype;
    window.WebSocket.CONNECTING = origWS.CONNECTING;
    window.WebSocket.OPEN = origWS.OPEN;
    window.WebSocket.CLOSING = origWS.CLOSING;
    window.WebSocket.CLOSED = origWS.CLOSED;

    // Also hook JSON.stringify to capture session key from auth
    const origStringify = JSON.stringify;
    JSON.stringify = function(...args) {
        return origStringify.apply(this, args);
    };
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'
        ])
        context = await browser.new_context(
            ignore_https_errors=True,
            viewport={'width': 1280, 'height': 800}
        )
        await context.add_init_script(hook_js)
        page = await context.new_page()

        print("[*] Opening web terminal...")
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Clear captured frames from page load
        await page.evaluate("window.__captured_ws_frames = []")
        print("[+] Page loaded, hook active")

        # Login
        print("[*] Logging in...")
        try:
            await page.fill('input[name="login"]', '463558919', timeout=5000)
            await page.fill('input[name="password"]', 'Trade@123', timeout=5000)
            await page.click('button[type="submit"]', timeout=5000)
        except Exception:
            try:
                await page.fill('#login', '463558919', timeout=3000)
                await page.fill('#password', 'Trade@123', timeout=3000)
                await page.click('#loginBtn', timeout=3000)
            except Exception:
                print("[!] Could not fill login form via selectors, trying JS...")
                await page.evaluate("""
                    const inputs = document.querySelectorAll('input');
                    for (const inp of inputs) {
                        if (inp.name === 'login' || inp.id === 'login') inp.value = '463558919';
                        if (inp.name === 'password' || inp.id === 'password') inp.value = 'Trade@123';
                    }
                    const btn = document.querySelector('button[type="submit"]') || document.querySelector('#loginBtn') || document.querySelector('button');
                    if (btn) btn.click();
                """)

        print("[*] Waiting for login to complete...")
        await asyncio.sleep(10)

        # Clear frames from login, keep only what comes after
        await page.evaluate("window.__captured_ws_frames = []")

        # Get page info
        page_info = await page.evaluate("""() => {
            const frames = window.__captured_ws_frames;
            const wsInstances = window.__ws_instances;
            return {
                frameCount: frames.length,
                wsCount: wsInstances.length,
                url: window.location.href,
                title: document.title
            };
        }""")
        print(f"[+] Page: {page_info['title']}, WS instances: {page_info['wsCount']}")

        # Get the current price from the page
        price_info = await page.evaluate("""() => {
            // Try to find price elements
            const elements = document.querySelectorAll('[class*="price"], [class*="bid"], [class*="ask"]');
            const texts = [];
            elements.forEach(el => {
                if (el.textContent.trim()) texts.push(el.textContent.trim().substring(0, 50));
            });
            return texts.slice(0, 10);
        }""")
        print(f"[+] Price elements on page: {price_info}")

        # ============================================
        # CAPTURE TRADE FRAMES
        # ============================================
        print("\n" + "="*60)
        print("STEP 1: Capturing frames during Buy click...")
        print("="*60)

        # Find and click the Buy button
        await page.evaluate("window.__captured_ws_frames = []")

        buy_clicked = False
        try:
            # Try various Buy button selectors
            selectors = [
                'button:has-text("Buy")', 'button:has-text("BUY")',
                '[data-action="buy"]', '.buy-button', '#buyBtn',
                'button.buy', 'button[class*="buy"]', 'button[class*="Buy"]',
                'span:has-text("Buy")', 'div:has-text("Buy")',
            ]
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.click()
                        buy_clicked = True
                        print(f"[+] Clicked Buy button: {sel}")
                        break
                except Exception:
                    continue
        except Exception as e:
            print(f"[!] Buy click error: {e}")

        if not buy_clicked:
            # Try clicking by text
            try:
                await page.get_by_text("Buy", exact=False).first.click()
                buy_clicked = True
                print("[+] Clicked Buy by text")
            except Exception as e:
                print(f"[!] Could not click Buy: {e}")

        await asyncio.sleep(5)

        # Check captured frames
        frames = await page.evaluate("""() => {
            return window.__captured_ws_frames.filter(f => f.dir === 'sent').map(f => ({
                time: f.time,
                size: f.size,
                hex: f.hex.substring(0, 200)
            }));
        }""")
        print(f"[+] Captured {len(frames)} sent frames after Buy click")
        for i, f in enumerate(frames):
            print(f"  Frame {i}: size={f['size']} hex={f['hex'][:80]}...")

        # Now try to get ALL sent frames with full hex
        all_sent = await page.evaluate("""() => {
            return window.__captured_ws_frames.filter(f => f.dir === 'sent').map(f => f.hex);
        }""")

        for i, hex_str in enumerate(all_sent):
            data = bytes.fromhex(hex_str)
            print(f"\n--- Sent Frame {i} (size={len(data)}) ---")
            if len(data) >= 8:
                enc_len = struct.unpack_from('<I', data, 0)[0]
                version = struct.unpack_from('<I', data, 4)[0]
                enc_data = data[8:]
                print(f"  Wire: enc_len={enc_len} version={version} enc_data_len={len(enc_data)}")

                # Try to decrypt with known key candidates
                for key_name, key in [("STATIC_KEY", STATIC_KEY)]:
                    try:
                        dec = aes_decrypt(key, enc_data)
                        if len(dec) >= 4:
                            rand = struct.unpack_from('<H', dec, 0)[0]
                            cmd_id = struct.unpack_from('<H', dec, 2)[0]
                            payload = dec[4:]
                            print(f"  Decrypted({key_name}): rand={rand} cmd_id={cmd_id} payload_len={len(payload)}")
                            if cmd_id == 12:
                                print(f"  *** TRADE COMMAND ***")
                                # Parse Op
                                if len(payload) >= 248:
                                    op = payload[:248]
                                    action = struct.unpack_from('<I', op, 4)[0]
                                    sym_raw = op[8:72]
                                    sym = sym_raw.decode('utf-16-le', errors='ignore').rstrip('\x00')
                                    vol = struct.unpack_from('<Q', op, 72)[0]
                                    digits = struct.unpack_from('<I', op, 80)[0]
                                    trade_type = struct.unpack_from('<I', op, 92)[0]
                                    filling = struct.unpack_from('<I', op, 96)[0]
                                    time_type = struct.unpack_from('<I', op, 100)[0]
                                    price_order = struct.unpack_from('<d', op, 112)[0]
                                    price_trigger = struct.unpack_from('<d', op, 120)[0]
                                    sl = struct.unpack_from('<d', op, 128)[0]
                                    tp = struct.unpack_from('<d', op, 136)[0]
                                    deviation = struct.unpack_from('<I', op, 144)[0]
                                    pos_id = struct.unpack_from('<Q', op, 228)[0]
                                    pos_by = struct.unpack_from('<Q', op, 236)[0]
                                    comment_raw = op[164:228]
                                    comment = comment_raw.decode('utf-16-le', errors='ignore').rstrip('\x00')

                                    print(f"  trade_action={action}({TRADE_ACTIONS.get(action, 'UNKNOWN')})")
                                    print(f"  symbol={sym}")
                                    print(f"  volume={vol} ({vol/100000000:.2f} lots)")
                                    print(f"  digits={digits}")
                                    print(f"  trade_type={trade_type}({TRADE_TYPES.get(trade_type, 'UNKNOWN')})")
                                    print(f"  type_filling={filling}({FILLING.get(filling, 'UNKNOWN')})")
                                    print(f"  type_time={time_type}({TIME_TYPE.get(time_type, 'UNKNOWN')})")
                                    print(f"  price_order={price_order:.5f}")
                                    print(f"  price_trigger={price_trigger:.5f}")
                                    print(f"  sl={sl:.5f} tp={tp:.5f}")
                                    print(f"  deviation={deviation}")
                                    print(f"  pos_id={pos_id} pos_by={pos_by}")
                                    print(f"  comment={comment}")
                                    print(f"  --- FULL OP HEX ---")
                                    print(f"  {op.hex()}")
                            elif cmd_id == 19:
                                print(f"  *** TRADE EVENT (response) ***")
                    except Exception as e:
                        print(f"  Decrypt failed ({key_name}): {e}")
            else:
                print(f"  Too short to parse: {hex_str[:40]}...")

        # Also dump ALL frames for analysis
        print("\n" + "="*60)
        print("ALL CAPTURED FRAMES (sent and received):")
        print("="*60)
        all_frames = await page.evaluate("""() => {
            return window.__captured_ws_frames.map(f => ({
                dir: f.dir,
                size: f.size,
                hex: f.hex
            }));
        }""")
        for i, f in enumerate(all_frames):
            data_hex = f['hex']
            print(f"\nFrame {i}: {f['dir']} size={f['size']}")
            print(f"  hex: {data_hex[:120]}{'...' if len(data_hex) > 120 else ''}")

        await browser.close()

asyncio.run(main())
