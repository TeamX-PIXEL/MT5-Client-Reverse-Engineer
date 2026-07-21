#!/usr/bin/env python3
"""
Step 1: Login, click "Create New Order"
Step 2: Find Buy/Sell in order dialog, click, capture WS frames
"""
import asyncio, struct, time
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_decrypt(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        hook_js = """
        window.__ws_sent = [];
        window.__ws_recv = [];
        window.__ws_session_key = null;

        const _origWS = window.WebSocket;
        const _WrappedWS = function(...args) {
            const ws = new _origWS(...args);
            const origSend = ws.send.bind(ws);
            ws.send = function(data) {
                let hex = '';
                if (data instanceof ArrayBuffer) {
                    hex = Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                } else if (data && data.buffer) {
                    hex = Array.from(new Uint8Array(data.buffer)).map(b=>b.toString(16).padStart(2,'0')).join('');
                }
                if (hex) window.__ws_sent.push({ t: Date.now(), hex: hex, size: hex.length / 2 });
                return origSend(data);
            };
            ws.addEventListener('message', function(evt) {
                if (evt.data instanceof ArrayBuffer) {
                    const hex = Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join('');
                    window.__ws_recv.push({ t: Date.now(), hex: hex, size: hex.length / 2 });
                }
            });
            return ws;
        };
        _WrappedWS.prototype = _origWS.prototype;
        _WrappedWS.CONNECTING = _origWS.CONNECTING;
        _WrappedWS.OPEN = _origWS.OPEN;
        _WrappedWS.CLOSING = _origWS.CLOSING;
        _WrappedWS.CLOSED = _origWS.CLOSED;
        window.WebSocket = _WrappedWS;
        """
        await context.add_init_script(hook_js)
        page = await context.new_page()

        print("[*] Opening web terminal...")
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        print("[*] Logging in...")
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')

        print("[*] Waiting for connection...")
        await asyncio.sleep(12)

        sent_before = await page.evaluate("window.__ws_sent.length")
        recv_before = await page.evaluate("window.__ws_recv.length")
        print(f"[+] Connected! Sent: {sent_before}, Recv: {recv_before}")

        # Screenshot of trading panel
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_trading.png')
        print("[+] Trading panel screenshot saved")

        # Find "Create New Order" and click it
        print("\n[*] Looking for 'Create New Order'...")
        cno = page.locator('text=Create New Order')
        if await cno.count() > 0:
            await cno.first.click()
            print("[+] Clicked 'Create New Order'")
            await asyncio.sleep(2)
        else:
            # Try right-clicking on a symbol in the market watch
            print("[!] 'Create New Order' not found, trying to find order dialog...")

        # Screenshot of order dialog
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_order_dialog.png')
        print("[+] Order dialog screenshot saved")

        # Dump ALL buttons and clickable elements in the dialog
        dom = await page.evaluate("""() => {
            const result = { buttons: [], inputs: [], allText: [] };

            document.querySelectorAll('button, [role="button"], a, [onclick]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    result.buttons.push({
                        tag: el.tagName,
                        text: (el.textContent||'').trim().substring(0, 60),
                        class: el.className.substring(0, 100),
                        id: el.id,
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        w: Math.round(rect.width), h: Math.round(rect.height)
                    });
                }
            });

            document.querySelectorAll('input, select, textarea').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    result.inputs.push({
                        tag: el.tagName, type: el.type, name: el.name, id: el.id,
                        value: el.value, placeholder: el.placeholder || '',
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        w: Math.round(rect.width), h: Math.round(rect.height)
                    });
                }
            });

            // Find all text containing buy/sell/order/volume/lot
            document.querySelectorAll('*').forEach(el => {
                if (el.children.length === 0) {
                    const text = (el.textContent || '').trim();
                    if (text.length > 0 && text.length < 40) {
                        const lower = text.toLowerCase();
                        if (lower.includes('buy') || lower.includes('sell') || lower.includes('order') ||
                            lower.includes('volume') || lower.includes('lot') || lower.includes('price') ||
                            lower.includes('stop') || lower.includes('limit') || lower.includes('market') ||
                            lower.includes('pending')) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                result.allText.push({
                                    tag: el.tagName, text: text,
                                    x: Math.round(rect.x), y: Math.round(rect.y)
                                });
                            }
                        }
                    }
                }
            });

            return result;
        }""")

        print(f"\nButtons: {len(dom['buttons'])}")
        for btn in dom['buttons']:
            print(f"  [{btn['tag']}] text='{btn['text'][:40]}' pos=({btn['x']},{btn['y']}) size=({btn['w']}x{btn['h']})")

        print(f"\nInputs: {len(dom['inputs'])}")
        for inp in dom['inputs']:
            print(f"  [{inp['tag']} type={inp['type']}] name='{inp['name']}' value='{inp['value']}' "
                  f"placeholder='{inp['placeholder']}' pos=({inp['x']},{inp['y']})")

        print(f"\nTrade-related text: {len(dom['allText'])}")
        for t in sorted(dom['allText'], key=lambda x: (x['y'], x['x'])):
            print(f"  ({t['x']:4d},{t['y']:4d}) [{t['tag']}] '{t['text']}'")

        # Now click Buy button in the order dialog
        sent_before_trade = await page.evaluate("window.__ws_sent.length")

        # Try various Buy button selectors
        buy_found = False
        for selector in [
            'button:has-text("Buy")',
            'button:has-text("BUY")',
            'button:has-text("By Market")',
            'button:has-text("Market")',
            '[class*="buy"]',
            '[class*="Buy"]',
        ]:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=1000):
                    # Clear sent frames before clicking
                    await page.evaluate("window.__ws_sent = []")
                    await el.click()
                    print(f"\n[+] CLICKED BUY: {selector}")
                    buy_found = True
                    await asyncio.sleep(5)
                    break
            except Exception:
                continue

        if not buy_found:
            # List all buttons again more carefully
            print("\n[!] No Buy button found. All visible buttons:")
            all_btns = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('button, [role="button"]')).filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }).map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        text: (el.textContent||'').trim().substring(0, 60),
                        class: el.className.substring(0, 100),
                        x: Math.round(r.x), y: Math.round(r.y),
                        w: Math.round(r.width), h: Math.round(r.height)
                    };
                });
            }""")
            for b in all_btns:
                print(f"  text='{b['text']}' class='{b['class'][:60]}' pos=({b['x']},{b['y']}) size=({b['w']}x{b['h']})")

        # Check what happened
        sent_after = await page.evaluate("window.__ws_sent.length")
        print(f"\n[+] Sent frames after click: {sent_after} (was {sent_before_trade})")

        # Dump new sent frames
        if sent_after > sent_before_trade:
            new_frames = await page.evaluate(f"""() => {{
                return window.__ws_sent.slice({sent_before_trade}).map(f => f.hex);
            }}""")
            for i, hex_str in enumerate(new_frames):
                data = bytes.fromhex(hex_str)
                print(f"\n--- NEW Sent Frame {i} (size={len(data)}) ---")
                print(f"  hex: {hex_str[:200]}{'...' if len(hex_str) > 200 else ''}")
                if len(data) >= 8:
                    enc_len = struct.unpack_from('<I', data, 0)[0]
                    version = struct.unpack_from('<I', data, 4)[0]
                    enc_data = data[8:]
                    print(f"  wire: enc_len={enc_len} version={version}")

        # Also try double-clicking on a symbol to open order dialog
        print("\n[*] Trying to open order via symbol double-click...")
        # First close any open dialog
        try:
            await page.keyboard.press('Escape')
            await asyncio.sleep(1)
        except: pass

        # Try finding the symbol in the Market Watch
        symbol_row = page.locator('text=EURUSDm')
        if await symbol_row.count() > 0:
            await page.evaluate("window.__ws_sent = []")
            await symbol_row.first.dblclick()
            print("[+] Double-clicked EURUSDm")
            await asyncio.sleep(3)

            # Screenshot
            await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_after_dblclick.png')

            # Check for new buttons
            dom2 = await page.evaluate("""() => {
                const result = [];
                document.querySelectorAll('button, [role="button"]').forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        result.push({
                            text: (el.textContent||'').trim().substring(0, 60),
                            class: el.className.substring(0, 100),
                            x: Math.round(rect.x), y: Math.round(rect.y),
                            w: Math.round(rect.width), h: Math.round(rect.height)
                        });
                    }
                });
                return result;
            }""")
            print(f"  Buttons after double-click: {len(dom2)}")
            for b in dom2:
                print(f"    text='{b['text'][:40]}' pos=({b['x']},{b['y']}) size=({b['w']}x{b['h']})")

            # Try clicking Buy in the new dialog
            for selector in [
                'button:has-text("Buy")', 'button:has-text("BUY")',
                'button:has-text("Market")', 'button:has-text("By Market")',
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=1000):
                        await page.evaluate("window.__ws_sent = []")
                        await el.click()
                        print(f"\n[+] CLICKED BUY via double-click dialog: {selector}")
                        await asyncio.sleep(5)
                        break
                except Exception:
                    continue

        # Dump all final sent frames
        all_sent = await page.evaluate("window.__ws_sent.map(f => ({hex: f.hex, size: f.size}))")
        print(f"\n{'='*60}")
        print(f"FINAL: {len(all_sent)} sent frames total")
        print(f"{'='*60}")
        for i, f in enumerate(all_sent):
            data = bytes.fromhex(f['hex'])
            print(f"\nSent {i}: size={f['size']}")
            print(f"  hex: {f['hex'][:200]}{'...' if len(f['hex']) > 200 else ''}")

        await browser.close()

asyncio.run(main())
