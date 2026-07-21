#!/usr/bin/env python3
"""Capture the actual web terminal trade command by clicking Buy button."""
import asyncio, struct, time
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_dec(key, ct):
    if not ct or len(ct) % 16 != 0: return None
    return AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)

def aes_dec_unpad(key, ct):
    pt = aes_dec(key, ct)
    if pt is None: return None
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})
        hook_js = r"""
        window.__all_sent = [];
        window.__all_recv = [];
        window.__ws_raw = null;
        const _origWS = window.WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            window.__ws_raw = ws;
            const origSend = ws.send.bind(ws);
            ws.send = function(data) {
                if (data instanceof ArrayBuffer) {
                    window.__all_sent.push({
                        t: Date.now(),
                        hex: Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join(''),
                        sz: data.byteLength
                    });
                }
                return origSend(data);
            };
            ws.addEventListener('message', function(evt) {
                if (evt.data instanceof ArrayBuffer)
                    window.__all_recv.push(Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join(''));
            });
            return ws;
        };
        window.WebSocket.prototype = _origWS.prototype;
        """
        await context.add_init_script(hook_js)
        page = await context.new_page()
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(12)

        recv_hexes = await page.evaluate("window.__all_recv")
        session_key = None
        for hex_str in recv_hexes[:5]:
            data = bytes.fromhex(hex_str)
            try:
                dec = aes_dec(STATIC_KEY, data[8:])
                if dec and len(dec) >= 103 and struct.unpack_from('<H', dec, 2)[0] == 0:
                    session_key = dec[71:103]
                    print(f"[+] Session key: {session_key.hex()[:32]}...")
                    break
            except: pass
        if not session_key:
            print("[!] No session key"); await browser.close(); return

        # Check sent frames so far (heartbeats etc)
        sent_before = await page.evaluate("window.__all_sent.length")
        print(f"[*] Sent frames before UI interaction: {sent_before}")

        # Look for trading UI
        print("[*] Searching for trading UI elements...")
        elements = await page.evaluate(r"""() => {
            const all = document.querySelectorAll('button, [class*="buy"], [class*="sell"], [class*="trade"], [class*="order"]');
            return Array.from(all).map(e => ({
                tag: e.tagName,
                text: (e.textContent || '').trim().substring(0, 80),
                cls: (e.className || '').substring(0, 80),
                id: e.id,
                vis: e.offsetParent !== null
            })).filter(e => e.vis && e.text).slice(0, 30);
        }""")
        for e in elements:
            print(f"  {e['tag']} id={e['id']} text='{e['text']}' class='{e['cls']}'")

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_cap1.png')

        # Right-click on EURUSDm to get context menu with trade option
        eurusd = await page.query_selector('text=EURUSDm')
        if eurusd:
            print("[*] Right-clicking EURUSDm...")
            await eurusd.click(button='right')
            await asyncio.sleep(1)
            await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_cap2.png')

            # Look for "New Order" or trade option in context menu
            menu_items = await page.evaluate(r"""() => {
                const items = document.querySelectorAll('[class*="menu"], [class*="context"], [role="menu"], [class*="popup"]');
                return Array.from(items).map(e => ({
                    tag: e.tagName,
                    text: (e.textContent || '').trim().substring(0, 200),
                    cls: (e.className || '').substring(0, 80)
                })).slice(0, 10);
            }""")
            print(f"  Menu items: {menu_items}")

            # Try clicking "New Order" text
            for txt in ['New Order', 'Trade', 'Buy', 'Create New Order']:
                el = await page.query_selector(f'text="{txt}"')
                if el:
                    print(f"[*] Found '{txt}', clicking...")
                    sent_before_click = await page.evaluate("window.__all_sent.length")
                    await el.click()
                    await asyncio.sleep(2)
                    sent_after_click = await page.evaluate("window.__all_sent.length")
                    new_sent = await page.evaluate(f"window.__all_sent.slice({sent_before_click})")
                    print(f"  New sent frames: {len(new_sent)}")
                    if new_sent:
                        for frame in new_sent:
                            print(f"  Size: {frame['sz']}B")
                    break

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_cap3.png')

        # Try clicking "Create New Order" button
        create_btn = await page.query_selector('text="Create New Order"')
        if create_btn:
            print("[*] Clicking 'Create New Order'...")
            sent_b = await page.evaluate("window.__all_sent.length")
            await create_btn.click()
            await asyncio.sleep(2)
            await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_cap4.png')

            # Now look for Buy button in the dialog
            buy_btn = await page.query_selector('button:has-text("Buy by Market")')
            if not buy_btn:
                buy_btn = await page.query_selector('button:has-text("Buy")')
            if buy_btn:
                print("[*] Found Buy button! Clicking...")
                sent_before_trade = await page.evaluate("window.__all_sent.length")
                recv_before_trade = await page.evaluate("window.__all_recv.length")
                await buy_btn.click()
                await asyncio.sleep(5)

                sent_after_trade = await page.evaluate("window.__all_sent.length")
                recv_after_trade = await page.evaluate("window.__all_recv.length")
                new_sent = await page.evaluate(f"window.__all_sent.slice({sent_before_trade})")
                new_recv = await page.evaluate(f"window.__all_recv.slice({recv_before_trade})")

                print(f"\n[*] After Buy click: {len(new_sent)} sent, {len(new_recv)} recv")
                for i, frame in enumerate(new_sent):
                    print(f"\n  SENT {i}: {frame['sz']}B")
                    data = bytes.fromhex(frame['hex'])
                    if len(data) >= 8:
                        dec_raw = aes_dec(session_key, data[8:])
                        if dec_raw:
                            pb = dec_raw[-1]
                            dec = dec_raw[:-pb] if 1 <= pb <= 16 and all(b == pb for b in dec_raw[-pb:]) else dec_raw
                            cmd_id = struct.unpack_from('<H', dec, 2)[0]
                            body = dec[5:]
                            print(f"    cmd_id={cmd_id} body_len={len(body)}")
                            if cmd_id == 12:
                                print(f"    *** TRADE COMMAND CAPTURED! ***")
                                for off in range(0, min(len(body), 400), 32):
                                    chunk = body[off:off+32]
                                    print(f"      [{off:3d}] {chunk.hex()}")
                                # Parse Pp
                                if len(body) >= 380:
                                    act_id = struct.unpack_from('<I', body, 0)[0]
                                    print(f"    action_id={act_id}")
                                    op = body[4:252]
                                    trade_action = struct.unpack_from('<I', op, 4)[0]
                                    sym = op[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00')
                                    vol = struct.unpack_from('<Q', op, 72)[0]
                                    dig = struct.unpack_from('<I', op, 80)[0]
                                    trade_type = struct.unpack_from('<I', op, 92)[0]
                                    filling = struct.unpack_from('<I', op, 96)[0]
                                    tflags = struct.unpack_from('<I', op, 104)[0]
                                    price = struct.unpack_from('<d', op, 112)[0]
                                    dev = struct.unpack_from('<I', op, 144)[0]
                                    comment = op[164:228].decode('utf-16-le', errors='ignore').rstrip('\x00')
                                    tpos = struct.unpack_from('<Q', op, 228)[0]
                                    print(f"    trade_action={trade_action} sym='{sym}' vol={vol} dig={dig}")
                                    print(f"    type={trade_type} filling={filling} flags={tflags}")
                                    print(f"    price={price:.5f} dev={dev} comment='{comment}' tpos={tpos}")
                                    ap = body[252:380]
                                    ap_rc = struct.unpack_from('<I', ap, 0)[0]
                                    print(f"    Ap retcode={ap_rc}")
                                elif len(body) >= 252:
                                    # No Ap, just action_id + Op
                                    act_id = struct.unpack_from('<I', body, 0)[0]
                                    op = body[4:252]
                                    trade_action = struct.unpack_from('<I', op, 4)[0]
                                    sym = op[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00')
                                    vol = struct.unpack_from('<Q', op, 72)[0]
                                    price = struct.unpack_from('<d', op, 112)[0]
                                    print(f"    act_id={act_id} trade_action={trade_action} sym='{sym}' vol={vol} price={price:.5f}")

                for i, hex_str in enumerate(new_recv):
                    data = bytes.fromhex(hex_str)
                    if len(data) < 8: continue
                    dec_raw = aes_dec(session_key, data[8:])
                    if dec_raw is None: continue
                    pb = dec_raw[-1]
                    dec = dec_raw[:-pb] if 1 <= pb <= 16 and all(b == pb for b in dec_raw[-pb:]) else dec_raw
                    cmd_id = struct.unpack_from('<H', dec, 2)[0]
                    if cmd_id in (12, 19):
                        body = dec[5:]
                        print(f"\n  RECV {i}: cmd_id={cmd_id} body_len={len(body)}")
                        for off in range(0, min(len(body), 400), 32):
                            chunk = body[off:off+32]
                            print(f"    [{off:3d}] {chunk.hex()}")

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_cap_final.png')
        await browser.close()

asyncio.run(main())
