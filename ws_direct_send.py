#!/usr/bin/env python3
"""
Direct approach: Get the real WebSocket reference from the page,
and call ws.send() directly with a properly crafted trade command.
This bypasses all UI and Svelte event handling.
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

def aes_enc(key, pt):
    pad_len = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad_len] * pad_len))

TRADE_ACTIONS = {0:'DEAL', 1:'PENDING', 2:'INSTANT', 3:'MARKET', 10:'CLOSE', 201:'MODIFY'}
TRADE_TYPES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP'}
FILLING = {0:'FOK', 1:'IOC', 2:'RETURN'}
CMD_NAMES = {0:'AUTH', 1:'HEARTBEAT', 3:'ACCOUNT_FULL', 4:'ACCOUNT',
             5:'DEALS', 7:'SUBSCRIBE_QUOTE', 8:'QUOTE', 9:'SYMBOLS_FULL',
             11:'RATES', 12:'TRADE', 17:'SYMBOL_SPEC', 18:'SYMBOLS_COMPACT',
             19:'TRADE_EVENT', 28:'LOGIN', 34:'SYMBOLS', 51:'PING'}

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

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        # Hook to capture frames AND store the raw WebSocket reference
        hook_js = """
        window.__all_sent = [];
        window.__all_recv = [];
        window.__ws_raw = null;
        window.__ws_original_send = null;

        const _origWS = window.WebSocket;
        window.WebSocket = function(...args) {
            const ws = new _origWS(...args);
            window.__ws_raw = ws;
            // Store the REAL original send (unbound)
            window.__ws_original_send = _origWS.prototype.send;

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

        # Get current EURUSDm price from the last quote
        print("[*] Getting current price...")
        price_data = await page.evaluate("""() => {
            // Get the last quote data from recv frames
            return window.__all_recv.length;
        }""")
        print(f"  Total recv frames: {price_data}")

        # Find EURUSDm price from recv frames
        eurusd_price = None
        for hex_str in recv_hexes:
            data = bytes.fromhex(hex_str)
            if len(data) < 8: continue
            try:
                dec = aes_dec_unpad(session_key, data[8:])
                if dec and len(dec) >= 4:
                    cmd_id = struct.unpack_from('<H', dec, 2)[0]
                    if cmd_id == 8:  # QUOTE
                        payload = dec[4:]
                        # Quote format: [symbol_name(UTF16,64B)][...price data...]
                        sym = payload[0:64].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        if 'EURUSD' in sym:
                            # Parse quote data
                            if len(payload) >= 100:
                                bid = struct.unpack_from('<d', payload, 64)[0]
                                ask = struct.unpack_from('<d', payload, 72)[0]
                                eurusd_price = {'bid': bid, 'ask': ask, 'sym': sym}
                                break
            except: pass

        if eurusd_price:
            print(f"[+] EURUSDm: bid={eurusd_price['bid']:.5f} ask={eurusd_price['ask']:.5f}")
        else:
            print("[!] Could not find EURUSDm price, using default")
            eurusd_price = {'bid': 1.14500, 'ask': 1.14515}

        # =========================================================
        # Direct trade via ws.send() using the ORIGINAL send method
        # =========================================================
        print(f"\n{'='*70}")
        print("SENDING TRADE COMMAND DIRECTLY VIA ws.send()")
        print(f"{'='*70}")

        # Build the trade command as a hex string in JS
        # We'll construct the full packet: [random:2][cmd_id:2][Op:248]
        # Then encrypt with session key, wrap with wire header

        ask_price = eurusd_price['ask']
        bid_price = eurusd_price['bid']

        # Build Op payload (248 bytes) in Python, convert to hex for JS
        op = bytearray(OP_SIZE)
        struct.pack_into('<I', op, 4, 0)  # trade_action = DEAL
        sym = 'EURUSDm'.encode('utf-16-le')
        op[8:8+len(sym)] = sym
        struct.pack_into('<Q', op, 72, 1000)  # volume = 0.01 lots
        struct.pack_into('<I', op, 80, 5)  # digits = 5
        struct.pack_into('<I', op, 92, 0)  # trade_type = BUY
        struct.pack_into('<I', op, 96, 0)  # type_filling = FOK
        struct.pack_into('<I', op, 100, 0)  # type_time = GTC
        struct.pack_into('<d', op, 112, ask_price)  # price = ASK
        struct.pack_into('<I', op, 144, 10)  # deviation = 10

        print(f"  trade_action=0(DEAL) type=0(BUY) sym=EURUSDm")
        print(f"  volume=1000 (0.01 lots) price={ask_price:.5f}")
        print(f"  filling=0(FOK) time=0(GTC) deviation=10")

        # Build FULL Pp: [action_id(4)][Op(248)][Ap(128)] = 380 bytes
        import random
        action_id = random.randint(0, 0xFFFFFFFF)
        ap = bytearray(128)  # Ap is all zeros for request
        pp = struct.pack('<I', action_id) + bytes(op) + bytes(ap)

        # Build command: [random:2][cmd_id:2][Pp:380]
        rand = random.randint(0, 65535)
        cmd_bytes = struct.pack('<HH', rand, 12) + pp  # cmd_id=12

        # Encrypt with session key
        enc = aes_enc(session_key, cmd_bytes)

        # Build wire packet: [enc_len:4][version:4][encrypted_data]
        wire = struct.pack('<II', len(enc), 1) + enc

        wire_hex = wire.hex()
        print(f"  Wire packet: {len(wire)} bytes")
        print(f"  Hex: {wire_hex[:80]}...")

        # Send via the original send method
        sent_before = await page.evaluate("window.__all_recv.length")
        result = await page.evaluate("""(hex) => {
            const ws = window.__ws_raw;
            if (!ws) return 'no ws';
            if (ws.readyState !== 1) return 'ws not open: ' + ws.readyState;
            const arr = new Uint8Array(hex.match(/.{2}/g).map(b => parseInt(b, 16)));
            WebSocket.prototype.send.call(ws, arr.buffer);
            return 'sent ' + arr.buffer.byteLength + ' bytes';
        }""", wire_hex)
        print(f"  Result: {result}")

        # Wait for response
        await asyncio.sleep(5)

        # Check for TRADE_EVENT in recv
        recv_after = await page.evaluate("window.__all_recv")
        new_recv = recv_after[sent_before:]

        print(f"  New recv frames after direct send: {len(new_recv)}")
        for i, hex_str in enumerate(new_recv):
            data = bytes.fromhex(hex_str)
            r = try_decrypt(hex_str, session_key)
            if r:
                cmd_name = CMD_NAMES.get(r['cmd_id'], f'CMD_{r["cmd_id"]}')
                print(f"\n  Recv {i}: cmd_id={r['cmd_id']} ({cmd_name}) payload_len={len(r['payload'])}")
                
                if r['cmd_id'] in [12, 19]:
                    body = r['payload']
                    print(f"    RAW PAYLOAD HEX ({len(body)} bytes):")
                    for off in range(0, min(len(body), 500), 32):
                        chunk = body[off:off+32]
                        print(f"      [{off:3d}] {chunk.hex()}")
                    
                    if r['cmd_id'] == 19 and len(body) > 4:
                        # Try multiple Op offsets
                        for op_off in [0, 4]:
                            if op_off + OP_SIZE <= len(body):
                                op_echo = parse_op(body[op_off:op_off+OP_SIZE])
                                if op_echo:
                                    print(f"    Op at offset {op_off}: action={op_echo['trade_action']} "
                                          f"sym='{op_echo['symbol']}' vol={op_echo['volume']} "
                                          f"price={op_echo['price_order']:.5f}")
                        # Try multiple Ap offsets
                        for ap_off in [4, OP_SIZE, OP_SIZE+4]:
                            if ap_off + 32 <= len(body):
                                retcode = struct.unpack_from('<I', body, ap_off)[0]
                                deal = struct.unpack_from('<q', body, ap_off+4)[0]
                                order = struct.unpack_from('<q', body, ap_off+12)[0]
                                vol = struct.unpack_from('<q', body, ap_off+20)[0]
                                price_bytes = body[ap_off+28:ap_off+36]
                                try:
                                    price = struct.unpack('<d', price_bytes)[0]
                                except:
                                    price = 0
                                print(f"    Ap at {ap_off}: retcode={retcode} deal={deal} order={order} vol={vol} price={price:.5f}")
            else:
                print(f"  Recv {i}: size={len(data)} (decrypt failed)")
                # Still dump raw for analysis
                dec = aes_dec(session_key, data[8:])
                if dec:
                    print(f"    RAW DECrypted ({len(dec)} bytes):")
                    for off in range(0, min(len(dec), 300), 32):
                        chunk = dec[off:off+32]
                        print(f"      [{off:3d}] {chunk.hex()}")

        # Screenshot
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_direct_trade.png')

        # Also try SELL
        print(f"\n{'='*70}")
        print("SENDING SELL COMMAND...")
        print(f"{'='*70}")

        op2 = bytearray(OP_SIZE)
        struct.pack_into('<I', op2, 4, 0)  # trade_action = DEAL
        op2[8:8+len(sym)] = sym
        struct.pack_into('<Q', op2, 72, 1000)  # volume = 0.01 lots
        struct.pack_into('<I', op2, 80, 5)  # digits = 5
        struct.pack_into('<I', op2, 92, 1)  # trade_type = SELL
        struct.pack_into('<I', op2, 96, 0)  # type_filling = FOK
        struct.pack_into('<I', op2, 100, 0)  # type_time = GTC
        struct.pack_into('<d', op2, 112, bid_price)  # price = BID
        struct.pack_into('<I', op2, 144, 10)  # deviation = 10

        # Build FULL Pp for SELL: [action_id(4)][Op(248)][Ap(128)] = 380 bytes
        action_id2 = random.randint(0, 0xFFFFFFFF)
        ap2 = bytearray(128)
        pp2 = struct.pack('<I', action_id2) + bytes(op2) + bytes(ap2)

        rand2 = random.randint(0, 65535)
        cmd_bytes2 = struct.pack('<HH', rand2, 12) + pp2  # cmd_id=12
        enc2 = aes_enc(session_key, cmd_bytes2)
        wire2 = struct.pack('<II', len(enc2), 1) + enc2
        wire_hex2 = wire2.hex()

        sent_before = await page.evaluate("window.__all_recv.length")
        result2 = await page.evaluate("""(hex) => {
            const ws = window.__ws_raw;
            if (!ws || ws.readyState !== 1) return 'ws not ready';
            const arr = new Uint8Array(hex.match(/.{2}/g).map(b => parseInt(b, 16)));
            WebSocket.prototype.send.call(ws, arr.buffer);
            return 'sent ' + arr.buffer.byteLength + ' bytes';
        }""", wire_hex2)
        print(f"  Result: {result2}")
        await asyncio.sleep(5)

        recv_after = await page.evaluate("window.__all_recv")
        new_recv = recv_after[sent_before:]
        print(f"  New recv: {len(new_recv)}")
        for i, hex_str in enumerate(new_recv):
            r = try_decrypt(hex_str, session_key)
            if r and r['cmd_id'] == 19:
                body = r['payload']
                ap_off = 4 + OP_SIZE
                if len(body) >= ap_off + 64:
                    retcode = struct.unpack_from('<I', body, ap_off)[0]
                    deal = struct.unpack_from('<q', body, ap_off+4)[0]
                    order = struct.unpack_from('<q', body, ap_off+12)[0]
                    comment = body[ap_off+64:ap_off+128].decode('utf-16-le', errors='ignore').rstrip('\x00')
                    print(f"    TRADE_EVENT: retcode={retcode} deal={deal} order={order} comment='{comment}'")

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_after_sell.png')
        await browser.close()

asyncio.run(main())
