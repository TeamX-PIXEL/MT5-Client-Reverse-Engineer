#!/usr/bin/env python3
"""
Quick deal history check - verify if trades actually executed.
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
        print("[+] Logged in\n")

        # Get positions first
        await sc(ws, sk, 4)
        await asyncio.sleep(1)
        pos_count = 0
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 4:
                    body = r['res_body']
                    pos_count = struct.unpack_from('<I', body, 0)[0]
                    print(f"=== CURRENT POSITIONS: {pos_count} ===")
                    off = 4
                    for i in range(pos_count):
                        if off + POS_SIZE > len(body): break
                        rec = body[off:off+POS_SIZE]
                        pid = struct.unpack_from('<q', rec, 0)[0]
                        sym = rec[24:88].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        act = struct.unpack_from('<I', rec, 88)[0]
                        po = struct.unpack_from('<d', rec, 92)[0]
                        pc = struct.unpack_from('<d', rec, 100)[0]
                        vol = struct.unpack_from('<Q', rec, 124)[0]
                        pnl = struct.unpack_from('<d', rec, 132)[0]
                        print(f"  #{pid} {sym} {'BUY' if act==0 else 'SELL'} "
                              f"{vol/100000000:.2f} lots open={po:.5f} cur={pc:.5f} P/L={pnl:.2f}")
                        off += POS_SIZE
                    break
            except asyncio.TimeoutError: continue

        # Get deal history (last hour)
        now = int(time.time())
        await sc(ws, sk, 5, struct.pack('<II', now - 3600, now))
        print(f"\n=== DEAL HISTORY (last hour) ===")
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                r = pr(aes_dec(sk, raw[8:]))
                if r and r['cmd_id'] == 5:
                    body = r['res_body']
                    if len(body) < 4:
                        print("  No deals"); break
                    deal_count = struct.unpack_from('<I', body, 0)[0]
                    print(f"  Deal count: {deal_count}\n")
                    off = 4
                    for i in range(deal_count):
                        if off + DEAL_SIZE > len(body): break
                        rec = body[off:off+DEAL_SIZE]
                        deal_id = struct.unpack_from('<q', rec, 0)[0]
                        order_id = struct.unpack_from('<q', rec, 8)[0]
                        time_d = struct.unpack_from('<I', rec, 16)[0]
                        sym = rec[24:88].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        deal_type = struct.unpack_from('<I', rec, 88)[0]
                        deal_entry = struct.unpack_from('<I', rec, 92)[0]  
                        vol = struct.unpack_from('<Q', rec, 96)[0]
                        price = struct.unpack_from('<d', rec, 104)[0]
                        profit = struct.unpack_from('<d', rec, 128)[0]
                        commission = struct.unpack_from('<d', rec, 136)[0]
                        swap = struct.unpack_from('<d', rec, 144)[0]
                        magic = struct.unpack_from('<I', rec, 152)[0]
                        deal_comment = rec[160:224].decode('utf-16-le', errors='ignore').rstrip('\x00')
                        position_id = struct.unpack_from('<q', rec, 224)[0]

                        type_names = {0:'BUY', 1:'SELL', 2:'BUY_LIMIT', 3:'SELL_LIMIT',
                                     4:'BUY_STOP', 5:'SELL_STOP', 6:'BALANCE'}
                        entry_names = {0:'IN', 1:'OUT', 2:'INOUT', 3:'OUT_BY'}

                        age = now - time_d
                        print(f"  Deal #{deal_id}")
                        print(f"    Order: {order_id}  Position: {position_id}")
                        print(f"    Time: {time_d} ({age}s ago)")
                        print(f"    Symbol: {sym}")
                        print(f"    Type: {type_names.get(deal_type, deal_type)} "
                              f"Entry: {entry_names.get(deal_entry, deal_entry)}")
                        print(f"    Volume: {vol/100000000:.2f} lots  Price: {price:.5f}")
                        print(f"    P/L: {profit:.2f}  Commission: {commission:.2f}  Swap: {swap:.2f}")
                        print(f"    Comment: '{deal_comment}'")
                        print()
                        off += DEAL_SIZE
                    break
            except asyncio.TimeoutError:
                print("  Timeout waiting for deals"); break

if __name__ == '__main__':
    asyncio.run(main())