#!/usr/bin/env python3
"""
Dump raw auth response to find exact session key offset.
"""
import asyncio, struct, time
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_decrypt(key, ct):
    if not ct or len(ct) % 16 != 0: return None
    return AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)

def pkcs7_unpad(data):
    p = data[-1]
    if 1 <= p <= 16 and all(b == p for b in data[-p:]):
        return data[:-p]
    return data

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        hook_js = """
        window.__all_sent = [];
        window.__all_recv = [];
        const _origSend = WebSocket.prototype.send;
        WebSocket.prototype.send = function(data) {
            if (data instanceof ArrayBuffer) {
                window.__all_sent.push(Array.from(new Uint8Array(data)).map(b=>b.toString(16).padStart(2,'0')).join(''));
            }
            return _origSend.call(this, data);
        };
        const _origWS = WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            ws.addEventListener('message', function(evt) {
                if (evt.data instanceof ArrayBuffer) {
                    window.__all_recv.push(Array.from(new Uint8Array(evt.data)).map(b=>b.toString(16).padStart(2,'0')).join(''));
                }
            });
            return ws;
        };
        window.WebSocket.prototype = _origWS.prototype;
        """
        await context.add_init_script(hook_js)
        page = await context.new_page()

        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(12)

        js_recv = await page.evaluate("window.__all_recv")
        js_sent = await page.evaluate("window.__all_sent")

        print(f"Total sent: {len(js_sent)}, Total recv: {len(js_recv)}")

        # Dump first 5 recv frames raw hex and sizes
        print("\n=== FIRST 10 RECV FRAMES ===")
        for i, hex_str in enumerate(js_recv[:10]):
            data = bytes.fromhex(hex_str)
            print(f"\nRecv {i}: size={len(data)} hex={hex_str[:120]}...")

        # Decrypt auth response (first recv frame) with STATIC key
        print("\n=== DECRYPTING FIRST RECV FRAME WITH STATIC KEY ===")
        auth_hex = js_recv[0]
        auth_data = bytes.fromhex(auth_hex)
        enc_len = struct.unpack_from('<I', auth_data, 0)[0]
        version = struct.unpack_from('<I', auth_data, 4)[0]
        enc_data = auth_data[8:]
        print(f"Wire: enc_len={enc_len} version={version} enc_data_len={len(enc_data)}")

        # Decrypt raw (without PKCS7 unpad)
        raw_dec = aes_decrypt(STATIC_KEY, enc_data)
        print(f"Raw decrypted ({len(raw_dec)} bytes): {raw_dec.hex()}")
        print(f"First 16 bytes: {raw_dec[:16].hex()}")
        print(f"Last 16 bytes: {raw_dec[-16:].hex()}")

        # Try different unpad approaches
        # Just look for 32-byte key at every possible offset
        print("\n=== SEARCHING FOR 32-BYTE KEY IN DECRYPTED DATA ===")
        # The session key should appear somewhere in the auth response
        # Try all possible 32-byte windows
        for offset in range(0, len(raw_dec) - 31):
            candidate = raw_dec[offset:offset+32]
            # Check if it looks like a valid key (not all zeros, not repeating)
            if candidate.count(candidate[0]) < 32:
                # This might be the session key
                # Verify by trying to decrypt the second recv frame
                if len(js_recv) > 1:
                    frame2 = bytes.fromhex(js_recv[1])
                    if len(frame2) >= 8:
                        enc2 = frame2[8:]
                        if len(enc2) >= 16:
                            try:
                                dec2 = aes_decrypt(candidate, enc2)
                                cmd2 = struct.unpack_from('<H', dec2, 2)[0]
                                if cmd2 in [28, 3, 4, 5, 7, 8, 9, 11, 12, 17, 18, 19, 34, 51]:
                                    print(f"  offset={offset}: VALID! cmd_id={cmd2} key={candidate.hex()}")
                                    # Try on frame 3 too
                                    if len(js_recv) > 2:
                                        frame3 = bytes.fromhex(js_recv[2])
                                        dec3 = aes_decrypt(candidate, frame3[8:])
                                        cmd3 = struct.unpack_from('<H', dec3, 2)[0]
                                        print(f"    frame3 cmd_id={cmd3}")
                                    if len(js_recv) > 3:
                                        frame4 = bytes.fromhex(js_recv[3])
                                        dec4 = aes_decrypt(candidate, frame4[8:])
                                        cmd4 = struct.unpack_from('<H', dec4, 2)[0]
                                        print(f"    frame4 cmd_id={cmd4}")
                                    break
                            except:
                                pass

        # Also check: maybe the key is at the start after some header bytes
        print("\n=== RAW DECRYPTED AUTH RESPONSE FULL HEX ===")
        for off in range(0, len(raw_dec), 32):
            chunk = raw_dec[off:off+32]
            print(f"  [{off:3d}] {chunk.hex()}")

        await browser.close()

asyncio.run(main())
