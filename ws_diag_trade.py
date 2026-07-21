#!/usr/bin/env python3
"""
Diagnostic: send a BUY trade via ws_direct_send.py approach, then parse the
TRADE_EVENT response body byte-by-byte to find the correct Ap offset and error.
"""
import asyncio, struct, time, random
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

def aes_enc(key, pt):
    pad_len = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad_len] * pad_len))

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        hook_js = """
        window.__all_sent = [];
        window.__all_recv = [];
        window.__ws_raw = null;

        const _origWS = window.WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            window.__ws_raw = ws;
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

        # Find EURUSDm price
        eurusd_price = None
        for hex_str in recv_hexes:
            data = bytes.fromhex(hex_str)
            if len(data) < 8: continue
            try:
                dec = aes_dec_unpad(session_key, data[8:])
                if dec and len(dec) >= 4:
                    cmd_id = struct.unpack_from('<H', dec, 2)[0]
                    if cmd_id == 8:
                        payload = dec[5:]
                        sym = payload[0:64].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        if 'EURUSD' in sym and len(payload) >= 80:
                            bid = struct.unpack_from('<d', payload, 64)[0]
                            ask = struct.unpack_from('<d', payload, 72)[0]
                            eurusd_price = {'bid': bid, 'ask': ask}
                            break
            except: pass

        if not eurusd_price:
            print("[!] No EURUSDm price, using default")
            eurusd_price = {'bid': 1.14500, 'ask': 1.14515}
        print(f"[+] EURUSDm: bid={eurusd_price['bid']:.5f} ask={eurusd_price['ask']:.5f}")

        # ===== Build and send BUY trade =====
        ask_price = eurusd_price['ask']

        # Build Op (248 bytes)
        op = bytearray(OP_SIZE)
        struct.pack_into('<I', op, 4, 0)  # trade_action = DEAL
        sym_bytes = 'EURUSDm'.encode('utf-16-le')
        op[8:8+len(sym_bytes)] = sym_bytes
        struct.pack_into('<Q', op, 72, 1000)  # volume = 0.01 lots
        struct.pack_into('<I', op, 80, 5)  # digits = 5
        struct.pack_into('<I', op, 92, 0)  # trade_type = BUY
        struct.pack_into('<I', op, 96, 0)  # type_filling = FOK
        struct.pack_into('<I', op, 100, 0)  # type_time = GTC
        struct.pack_into('<I', op, 104, 2)  # type_flags = 2
        struct.pack_into('<d', op, 112, ask_price)  # price = ASK
        struct.pack_into('<I', op, 144, 10)  # deviation = 10

        print(f"\n[*] Op we're sending:")
        for name, off, fmt, sz in [
            ('trade_action', 4, '<I', 4),
            ('symbol', 8, 'utf16', 64),
            ('volume', 72, '<Q', 8),
            ('digits', 80, '<I', 4),
            ('trade_type', 92, '<I', 4),
            ('type_filling', 96, '<I', 4),
            ('type_time', 100, '<I', 4),
            ('type_flags', 104, '<I', 4),
            ('price_order', 112, '<d', 8),
            ('price_deviation', 144, '<I', 4),
            ('trade_position', 228, '<Q', 8),
        ]:
            if fmt == 'utf16':
                val = op[off:off+sz].decode('utf-16-le', errors='ignore').rstrip('\x00')
                print(f"  [{off:3d}] {name} = '{val}'")
            else:
                val = struct.unpack_from(fmt, op, off)[0]
                print(f"  [{off:3d}] {name} = {val}")

        # Build Pp: [action_id(4)][Op(248)][Ap(128)] = 380 bytes
        action_id = random.randint(0, 0xFFFFFFFF)
        ap = bytearray(128)
        pp = struct.pack('<I', action_id) + bytes(op) + bytes(ap)

        # Build command: [random:2][cmd_id:2][Pp:380]
        rand_val = random.randint(0, 65535)
        cmd_bytes = struct.pack('<HH', rand_val, 12) + pp
        enc = aes_enc(session_key, cmd_bytes)
        wire = struct.pack('<II', len(enc), 1) + enc

        print(f"\n[*] Sending BUY: Pp={len(pp)}B, cmd={len(cmd_bytes)}B, wire={len(wire)}B")
        print(f"  action_id = {action_id}")

        sent_before = await page.evaluate("window.__all_recv.length")
        result = await page.evaluate("""(hex) => {
            const ws = window.__ws_raw;
            if (!ws || ws.readyState !== 1) return 'ws not ready';
            const arr = new Uint8Array(hex.match(/.{2}/g).map(b => parseInt(b, 16)));
            WebSocket.prototype.send.call(ws, arr.buffer);
            return 'sent ' + arr.buffer.byteLength + ' bytes';
        }""", wire.hex())
        print(f"  {result}")

        # Wait for response
        await asyncio.sleep(5)

        recv_after = await page.evaluate("window.__all_recv")
        new_recv = recv_after[sent_before:]

        print(f"\n[*] Got {len(new_recv)} new frames after trade")

        for i, hex_str in enumerate(new_recv):
            data = bytes.fromhex(hex_str)
            if len(data) < 8: continue
            dec_raw = aes_dec(session_key, data[8:])
            if dec_raw is None:
                print(f"  Frame {i}: decrypt failed (size={len(data)})")
                continue

            # Don't unpad yet - keep raw for analysis
            dec_unpad = None
            p = dec_raw[-1]
            if 1 <= p <= 16 and all(b == p for b in dec_raw[-p:]):
                dec_unpad = dec_raw[:-p]
            else:
                dec_unpad = dec_raw

            cmd_id = struct.unpack_from('<H', dec_unpad, 2)[0]
            if cmd_id not in (12, 19):
                continue

            res_code = dec_unpad[4]
            body = dec_unpad[5:]  # resBody (skip res_code)

            print(f"\n{'='*70}")
            print(f"  Frame {i}: cmd_id={cmd_id} res_code={res_code} raw_len={len(dec_raw)} unpadded_len={len(dec_unpad)} body_len={len(body)}")
            print(f"{'='*70}")

            # Dump full body hex
            print(f"  BODY HEX ({len(body)} bytes):")
            for off in range(0, min(len(body), 400), 32):
                chunk = body[off:off+32]
                print(f"    [{off:3d}] {chunk.hex()}")

            if cmd_id == 12:
                # TRADE ack - just retcode
                if len(body) >= 4:
                    retcode = struct.unpack_from('<I', body, 0)[0]
                    print(f"\n  TRADE ACK: retcode={retcode}")

            elif cmd_id == 19:
                # TRADE_EVENT: try every possible Ap offset
                print(f"\n  Searching for Ap (looking for retcode field)...")
                
                # The Pp should be [action_id(4)][Op(248)][Ap(128)] = 380
                # But resBody might include extra bytes or have different layout
                # Try offsets: the resBody IS the Pp
                # Pp starts at body[0] (if no res_code in body)
                # Or Pp starts at body[1] (if res_code is body[0])
                
                # Try all possible Ap offsets
                for desc, off in [
                    ("body[0:4] as action_id, Op@body[4:252], Ap@body[252:380]", 252),
                    ("body[0] res_code, body[1:5] action_id, Op@body[5:253], Ap@body[253:381]", 253),
                    ("raw Op@body[0:248], Ap@body[248:376]", 248),
                    ("body[1:249] as Op, Ap@body[249:377]", 249),
                    ("body[2:250] as Op, Ap@body[250:378]", 250),
                    ("body[3:251] as Op, Ap@body[251:379]", 251),
                ]:
                    if off + 32 <= len(body):
                        retcode = struct.unpack_from('<I', body, off)[0]
                        deal = struct.unpack_from('<q', body, off+4)[0]
                        order = struct.unpack_from('<q', body, off+12)[0]
                        vol = struct.unpack_from('<q', body, off+20)[0]
                        try:
                            price = struct.unpack_from('<d', body, off+28)[0]
                        except:
                            price = -1
                        print(f"    Ap@{off:3d} [{desc[:50]}]: retcode={retcode} deal={deal} order={order} vol={vol} price={price:.5f}")

                # Also try to find our action_id to locate Pp
                print(f"\n  Searching for action_id={action_id} (hex: {action_id.to_bytes(4,'little').hex()})...")
                target_bytes = action_id.to_bytes(4, 'little')
                for off in range(len(body) - 3):
                    if body[off:off+4] == target_bytes:
                        print(f"    Found action_id at body[{off}:{off+4}]")
                        # If this is the Pp action_id, Op starts at off+4
                        op_start = off + 4
                        if op_start + OP_SIZE <= len(body):
                            op_data = body[op_start:op_start+OP_SIZE]
                            ta = struct.unpack_from('<I', op_data, 4)[0]
                            vol = struct.unpack_from('<Q', op_data, 72)[0]
                            dig = struct.unpack_from('<I', op_data, 80)[0]
                            tt = struct.unpack_from('<I', op_data, 92)[0]
                            tf = struct.unpack_from('<I', op_data, 96)[0]
                            price = struct.unpack_from('<d', op_data, 112)[0]
                            dev = struct.unpack_from('<I', op_data, 144)[0]
                            tp = op_data[8:72].decode('utf-16-le', errors='ignore').rstrip('\x00')
                            print(f"    Op@{op_start}: trade_action={ta} sym='{tp}' vol={vol} dig={dig} type={tt} filling={tf} price={price:.5f} dev={dev}")
                            
                            # Ap follows Op
                            ap_start = op_start + OP_SIZE
                            if ap_start + 32 <= len(body):
                                ap_retcode = struct.unpack_from('<I', body, ap_start)[0]
                                ap_deal = struct.unpack_from('<q', body, ap_start+4)[0]
                                ap_order = struct.unpack_from('<q', body, ap_start+12)[0]
                                ap_vol = struct.unpack_from('<q', body, ap_start+20)[0]
                                try:
                                    ap_price = struct.unpack_from('<d', body, ap_start+28)[0]
                                except:
                                    ap_price = -1
                                comment = body[ap_start+64:ap_start+128].decode('utf-16-le', errors='ignore').rstrip('\x00') if ap_start+128 <= len(body) else ''
                                print(f"    Ap@{ap_start}: retcode={ap_retcode} deal={ap_deal} order={ap_order} vol={ap_vol} price={ap_price:.5f} comment='{comment}'")
                        break

                # Also scan for known error code patterns
                print(f"\n  Scanning for non-zero uint32 values in body...")
                for off in range(0, len(body) - 3, 4):
                    val = struct.unpack_from('<I', body, off)[0]
                    if val != 0 and val not in (1000, 10013):
                        print(f"    body[{off}:{off+4}] = {val} (0x{val:08x})")
                for off in range(0, len(body) - 3, 4):
                    val = struct.unpack_from('<I', body, off)[0]
                    if val == 10013:
                        print(f"    body[{off}:{off+4}] = 10013 (ERROR_INVALID_PARAMETERS!)")

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_diag_trade.png')
        await browser.close()

asyncio.run(main())
