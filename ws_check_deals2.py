#!/usr/bin/env python3
"""
Fixed deal history check with correct 356-byte deal schema offsets.
"""
import asyncio, struct, time, random, ssl
import websockets
from Crypto.Cipher import AES

WS_URL = "wss://15.206.31.153:443/terminal"
LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER_IP = "15.206.31.153"
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)
POS_SIZE = 344
DEAL_SIZE = 356

def aes_enc(key, pt):
    pad = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pad]*pad))

def aes_dec(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

def pack_data(cid, enc):
    return struct.pack('<II', len(enc), 1) + enc

def mk_cmd(cid, pl=b''):
    cmd = bytearray(4 + len(pl))
    cmd[0] = random.randint(0,255); cmd[1] = random.randint(0,255)
    struct.pack_into('<H', cmd, 2, cid)
    if pl: cmd[4:4+len(pl)] = pl
    return bytes(cmd)

def pr(data):
    if len(data) < 5: return None
    return {'tag': struct.unpack('<H', data[0:2])[0], 'cmd_id': struct.unpack('<H', data[2:4])[0],
            'res_code': data[4], 'res_body': data[5:]}

def mk_login(lid, pw, url):
    h = bytearray(912)
    p = pw.encode('utf-16-le'); h[4:4+len(p)] = p
    struct.pack_into('<I', h, 476, len(url))
    ip = url.encode('utf-16-le'); h[480:480+len(ip)] = ip
    struct.pack_into('<Q', h, 736, lid)
    return bytes(h)

async def sc(ws, sk, cid, pl=b''):
    await ws.send(pack_data(cid, aes_enc(sk, mk_cmd(cid, pl))))

def parse_deal(buf, off):
    if off + DEAL_SIZE > len(buf): return None
    return {
        'deal': struct.unpack_from('<q', buf, off)[0],
        'deal_id': buf[off+8:off+72].decode('utf-16-le', errors='ignore').split('\x00')[0],
        'order': struct.unpack_from('<q', buf, off+72)[0],
        'time_create': struct.unpack_from('<I', buf, off+80)[0],
        'time_update': struct.unpack_from('<I', buf, off+84)[0],
        'symbol': buf[off+88:off+152].decode('utf-16-le', errors='ignore').split('\x00')[0],
        'trade_action': struct.unpack_from('<I', buf, off+152)[0],
        'entry': struct.unpack_from('<I', buf, off+156)[0],
        'price_open': struct.unpack_from('<d', buf, off+160)[0],
        'price_close': struct.unpack_from('<d', buf, off+168)[0],
        'sl': struct.unpack_from('<d', buf, off+176)[0],
        'tp': struct.unpack_from('<d', buf, off+184)[0],
        'volume': struct.unpack_from('<Q', buf, off+192)[0],
        'profit': struct.unpack_from('<d', buf, off+200)[0],
        'rate_profit': struct.unpack_from('<d', buf, off+208)[0],
        'rate_margin': struct.unpack_from('<d', buf, off+216)[0],
        'commission': struct.unpack_from('<d', buf, off+224)[0],
        'storage': struct.unpack_from('<d', buf, off+232)[0],
        'expert': struct.unpack_from('<q', buf, off+240)[0],
        'position_id': struct.unpack_from('<q', buf, off+248)[0],
        'comment': buf[off+256:off+320].decode('utf-16-le', errors='ignore').split('\x00')[0],
        'contract_size': struct.unpack_from('<d', buf, off+320)[0],
        'digits': struct.unpack_from('<I', buf, off+328)[0],
        'reason': struct.unpack_from('<I', buf, off+336)[0],
        'time_ms': struct.unpack_from('<i', buf, off+340)[0],
    }

def parse_pos(body, off):
    if off + POS_SIZE > len(body): return None
    r = body[off:off+POS_SIZE]
    return {
        'id': struct.unpack_from('<q', r, 0)[0],
        'order': struct.unpack_from('<q', r, 8)[0],
        'time': struct.unpack_from('<I', r, 16)[0],
        'symbol': r[24:88].decode('utf-16-le', errors='ignore').rstrip('\x00'),
        'action': struct.unpack_from('<I', r, 88)[0],
        'price_open': struct.unpack_from('<d', r, 92)[0],
        'price_cur': struct.unpack_from('<d', r, 100)[0],
        'sl': struct.unpack_from('<d', r, 108)[0],
        'tp': struct.unpack_from('<d', r, 116)[0],
        'volume': struct.unpack_from('<Q', r, 124)[0],
        'profit': struct.unpack_from('<d', r, 132)[0],
        'digits': struct.unpack_from('<I', r, 260)[0],
    }

TYPE_NAMES = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT', 4:'BUY_STOP', 5:'SELL_STOP', 6:'BALANCE'}
ENTRY_NAMES = {0:'IN', 1:'OUT', 2:'INOUT', 3:'OUT_BY'}

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': 'https://15.206.31.153:443'}) as ws:
        await ws.send(pack_data(0, aes_enc(STATIC_KEY, mk_cmd(0, bytes(64)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        sk = pr(aes_dec(STATIC_KEY, raw[8:]))['res_body'][66:]
        await ws.send(pack_data(28, aes_enc(sk, mk_cmd(28, mk_login(LOGIN, PASSWORD, SERVER_IP)))))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[+] Logged in")

        # Get positions
        await sc(ws, sk, 4)
        print(f"\n{'='*70}")
        print("CURRENT POSITIONS")
        print(f"{'='*70}")
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 4:
                    body = r['res_body']
                    cnt = struct.unpack_from('<I', body, 0)[0]
                    print(f"  Count: {cnt}")
                    off = 4
                    for i in range(cnt):
                        p = parse_pos(body, off)
                        if p:
                            d = 'BUY' if p['action'] == 0 else 'SELL'
                            print(f"  #{p['id']} {p['symbol']} {d} "
                                  f"{p['volume']/100000000:.2f} lots @ {p['price_open']:.5f} "
                                  f"cur={p['price_cur']:.5f} P/L={p['profit']:.2f}")
                        off += POS_SIZE
                    break
            except asyncio.TimeoutError: continue

        # Get deal history (last 24h)
        now = int(time.time())
        await sc(ws, sk, 5, struct.pack('<II', now - 86400, now))
        print(f"\n{'='*70}")
        print("DEAL HISTORY (last 24h)")
        print(f"{'='*70}")
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 5:
                    body = r['res_body']
                    if len(body) < 4:
                        print("  Empty"); break
                    cnt = struct.unpack_from('<I', body, 0)[0]
                    print(f"  Deal count: {cnt}\n")
                    off = 4
                    for i in range(cnt):
                        d = parse_deal(body, off)
                        if not d: break
                        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(d['time_create']))
                        print(f"  [{i+1}] Deal #{d['deal']}")
                        print(f"      Order: {d['order']}  Position: {d['position_id']}")
                        print(f"      Time: {ts}  Symbol: {d['symbol']}")
                        print(f"      Type: {TYPE_NAMES.get(d['trade_action'], d['trade_action'])}  "
                              f"Entry: {ENTRY_NAMES.get(d['entry'], d['entry'])}")
                        print(f"      Volume: {d['volume']/100000000:.2f} lots")
                        print(f"      Open: {d['price_open']:.5f}  Close: {d['price_close']:.5f}")
                        print(f"      P/L: {d['profit']:.2f}  Commission: {d['commission']:.2f}  "
                              f"Swap: {d['storage']:.2f}")
                        print(f"      Comment: '{d['comment']}'")
                        print()
                        off += DEAL_SIZE
                    break
            except asyncio.TimeoutError:
                print("  Timeout"); break

if __name__ == '__main__':
    asyncio.run(main())