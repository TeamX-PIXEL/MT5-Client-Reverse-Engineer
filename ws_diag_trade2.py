#!/usr/bin/env python3
"""
Diagnostic v2: try multiple trade_action values and Pp layouts.
Key finding: body[4:8] = action_id, body[8:256] = Op, body[252] = 10013 error.
Possibility: Pp has extra 4-byte header, or Ap starts at body[252].

Test matrix:
1. trade_action=0 (DEAL) with Pp=[hdr:4][act:4][Op:248][Ap:128] = 384B
2. trade_action=3 (MARKET) same layout
3. trade_action=0 without Ap (just act+Op = 252B)
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

def build_op(ask_price, trade_action=0, trade_type=0, type_filling=0, type_flags=2, sym='EURUSDm'):
    op = bytearray(OP_SIZE)
    struct.pack_into('<I', op, 4, trade_action)
    sym_bytes = sym.encode('utf-16-le')
    op[8:8+len(sym_bytes)] = sym_bytes
    struct.pack_into('<Q', op, 72, 1000)  # volume = 0.01 lots
    struct.pack_into('<I', op, 80, 5)     # digits = 5
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, type_filling)
    struct.pack_into('<I', op, 100, 0)    # type_time = GTC
    struct.pack_into('<I', op, 104, type_flags)
    struct.pack_into('<d', op, 112, ask_price)
    struct.pack_into('<I', op, 144, 10)   # deviation = 10
    return bytes(op)

def build_trade_wire(session_key, op_bytes, pp_header=b''):
    """Build trade command wire packet.
    pp_header: extra bytes before action_id+Op+Ap (e.g. 4 bytes of zeros)
    """
    action_id = random.randint(0, 0xFFFFFFFF)
    ap = bytearray(128)
    pp = pp_header + struct.pack('<I', action_id) + op_bytes + bytes(ap)
    rand_val = random.randint(0, 65535)
    cmd_bytes = struct.pack('<HH', rand_val, 12) + pp
    enc = aes_enc(session_key, cmd_bytes)
    return struct.pack('<II', len(enc), 1) + enc, action_id

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        hook_js = """
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

        # Get EURUSDm price from live quotes
        eurusd_ask = 1.14515
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
                            eurusd_ask = struct.unpack_from('<d', payload, 72)[0]
                            print(f"[+] EURUSDm ask={eurusd_ask:.5f}")
                            break
            except: pass

        async def send_and_parse(label, wire_hex, recv_before):
            """Send wire packet, wait for TRADE_EVENT, parse Ap."""
            sent_before = await page.evaluate("window.__all_recv.length")
            result = await page.evaluate("""(hex) => {
                const ws = window.__ws_raw;
                if (!ws || ws.readyState !== 1) return 'ws not ready';
                const arr = new Uint8Array(hex.match(/.{2}/g).map(b => parseInt(b, 16)));
                WebSocket.prototype.send.call(ws, arr.buffer);
                return 'sent ' + arr.buffer.byteLength + ' bytes';
            }""", wire_hex)
            print(f"  {label}: {result}")
            
            await asyncio.sleep(4)
            recv_after = await page.evaluate("window.__all_recv")
            new_recv = recv_after[sent_before:]
            
            for hex_str in new_recv:
                data = bytes.fromhex(hex_str)
                if len(data) < 8: continue
                dec_raw = aes_dec(session_key, data[8:])
                if dec_raw is None: continue
                p_byte = dec_raw[-1]
                if 1 <= p_byte <= 16 and all(b == p_byte for b in dec_raw[-p_byte:]):
                    dec = dec_raw[:-p_byte]
                else:
                    dec = dec_raw
                cmd_id = struct.unpack_from('<H', dec, 2)[0]
                if cmd_id == 19:
                    body = dec[5:]  # skip tag(2)+cmd_id(2)+res_code(1)
                    # Find the real Ap by scanning for retcode
                    # Ap retcode should be a small number (0 or error code)
                    best = None
                    for off in range(0, min(len(body)-31, 380), 4):
                        rc = struct.unpack_from('<I', body, off)[0]
                        deal = struct.unpack_from('<q', body, off+4)[0]
                        order = struct.unpack_from('<q', body, off+12)[0]
                        # Valid Ap: retcode is small (<100000) and deal/order are reasonable
                        if rc < 100000 and -1000000000 < deal < 1000000000 and -1000000000 < order < 1000000000:
                            if best is None or (rc != 0 and best[0] == 0) or (rc != 0 and best[0] != 0):
                                best = (rc, deal, order, off)
                    if best:
                        rc, deal, order, off = best
                        vol = struct.unpack_from('<q', body, off+20)[0] if off+28 <= len(body) else 0
                        try: price = struct.unpack_from('<d', body, off+28)[0] if off+36 <= len(body) else 0
                        except: price = 0
                        comment = body[off+64:off+128].decode('utf-16-le', errors='ignore').rstrip('\x00') if off+128 <= len(body) else ''
                        status = "SUCCESS" if rc == 0 and deal > 0 else f"ERROR({rc})"
                        print(f"    -> Ap@{off}: retcode={rc} deal={deal} order={order} vol={vol} price={price:.5f} [{status}]")
                        if comment: print(f"       comment='{comment}'")
                        return rc, deal
                    else:
                        # Dump non-zero values
                        print(f"    -> No valid Ap found. Non-zero values:")
                        for off2 in range(0, min(len(body)-3, 380), 4):
                            v = struct.unpack_from('<I', body, off2)[0]
                            if v != 0:
                                print(f"       body[{off2}:{off2+4}] = {v}")
                        return -1, 0
            print(f"    -> No TRADE_EVENT received")
            return -1, 0

        # Test 1: trade_action=0 (DEAL), full Pp (380B)
        print(f"\n{'='*60}")
        print(f"TEST 1: trade_action=0 (DEAL), Pp=[act:4][Op:248][Ap:128]")
        print(f"{'='*60}")
        op1 = build_op(eurusd_ask, trade_action=0, trade_type=0)
        wire1, aid1 = build_trade_wire(session_key, op1)
        await send_and_parse("BUY DEAL", wire1.hex(), None)
        await asyncio.sleep(2)

        # Test 2: trade_action=3 (MARKET), full Pp (380B)
        print(f"\n{'='*60}")
        print(f"TEST 2: trade_action=3 (MARKET), Pp=[act:4][Op:248][Ap:128]")
        print(f"{'='*60}")
        op2 = build_op(eurusd_ask, trade_action=3, trade_type=0)
        wire2, aid2 = build_trade_wire(session_key, op2)
        await send_and_parse("BUY MARKET", wire2.hex(), None)
        await asyncio.sleep(2)

        # Test 3: trade_action=0 (DEAL), Pp=[act:4][Op:248] (no Ap, 252B)
        print(f"\n{'='*60}")
        print(f"TEST 3: trade_action=0 (DEAL), Pp=[act:4][Op:248] (no Ap)")
        print(f"{'='*60}")
        action_id3 = random.randint(0, 0xFFFFFFFF)
        pp3 = struct.pack('<I', action_id3) + op1
        rand3 = random.randint(0, 65535)
        cmd3 = struct.pack('<HH', rand3, 12) + pp3
        enc3 = aes_enc(session_key, cmd3)
        wire3 = struct.pack('<II', len(enc3), 1) + enc3
        await send_and_parse("BUY NO-AP", wire3.hex(), None)
        await asyncio.sleep(2)

        # Test 4: trade_action=1 (PENDING), full Pp
        print(f"\n{'='*60}")
        print(f"TEST 4: trade_action=1 (PENDING), Pp=[act:4][Op:248][Ap:128]")
        print(f"{'='*60}")
        op4 = build_op(eurusd_ask, trade_action=1, trade_type=0)
        wire4, aid4 = build_trade_wire(session_key, op4)
        await send_and_parse("BUY PENDING", wire4.hex(), None)
        await asyncio.sleep(2)

        # Test 5: trade_action=0 (DEAL) with type_filling=2 (RETURN)
        print(f"\n{'='*60}")
        print(f"TEST 5: trade_action=0, type_filling=2 (RETURN)")
        print(f"{'='*60}")
        op5 = build_op(eurusd_ask, trade_action=0, trade_type=0, type_filling=2)
        wire5, aid5 = build_trade_wire(session_key, op5)
        await send_and_parse("BUY FILLING_RETURN", wire5.hex(), None)

        # Check positions
        print(f"\n{'='*60}")
        print("CHECKING POSITIONS...")
        print(f"{'='*60}")
        await asyncio.sleep(2)
        # Send cmd_id=4 (positions)
        pos_cmd = struct.pack('<HH', random.randint(0,65535), 4)
        pos_enc = aes_enc(session_key, pos_cmd)
        pos_wire = struct.pack('<II', len(pos_enc), 1) + pos_enc
        sent_before = await page.evaluate("window.__all_recv.length")
        await page.evaluate("""(hex) => {
            const ws = window.__ws_raw;
            const arr = new Uint8Array(hex.match(/.{2}/g).map(b => parseInt(b, 16)));
            WebSocket.prototype.send.call(ws, arr.buffer);
        }""", pos_wire.hex())
        await asyncio.sleep(3)
        recv_after = await page.evaluate("window.__all_recv")
        for hex_str in recv_after[sent_before:]:
            data = bytes.fromhex(hex_str)
            if len(data) < 8: continue
            dec_raw = aes_dec(session_key, data[8:])
            if dec_raw is None: continue
            p_byte = dec_raw[-1]
            if 1 <= p_byte <= 16 and all(b == p_byte for b in dec_raw[-p_byte:]):
                dec = dec_raw[:-p_byte]
            else:
                dec = dec_raw
            cmd_id = struct.unpack_from('<H', dec, 2)[0]
            if cmd_id == 4:
                body = dec[5:]
                if len(body) >= 4:
                    pos_count = struct.unpack_from('<I', body, 0)[0]
                    print(f"  Positions: {pos_count}")

        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/ss_diag_trade2.png')
        await browser.close()

asyncio.run(main())
