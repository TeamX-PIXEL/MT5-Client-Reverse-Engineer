#!/usr/bin/env python3
"""Verify deal schema with correct 356-byte records"""
import asyncio, struct, time, random, ssl
import websockets
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_encrypt(key, pt):
    pl = 16 - (len(pt) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(pt + bytes([pl]*pl))
def aes_decrypt(key, ct):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ct)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b==p for b in pt[-p:]) else pt
def pack_data(cid, ed):
    return struct.pack('<II', len(ed), 1) + ed
def build_cmd(cid, payload=b''):
    cmd = bytearray(4+len(payload))
    cmd[0]=random.randint(0,255); cmd[1]=random.randint(0,255)
    struct.pack_into('<H', cmd, 2, cid)
    if payload: cmd[4:4+len(payload)]=payload
    return bytes(cmd)
def parse_resp(data):
    if len(data)<5: return None
    return {'tag':struct.unpack('<H',data[0:2])[0],'cmd_id':struct.unpack('<H',data[2:4])[0],'res_code':data[4],'res_body':data[5:]}

RECORD_SIZE = 356

def parse_deal(buf, off):
    if off + RECORD_SIZE > len(buf):
        return None
    deal = struct.unpack_from('<q', buf, off)[0]
    deal_id_raw = buf[off+8:off+72]
    try:
        deal_id = deal_id_raw.decode('utf-16-le').split('\x00')[0]
    except:
        deal_id = ''
    trade_order = struct.unpack_from('<q', buf, off+72)[0]
    time_create = struct.unpack_from('<I', buf, off+80)[0]
    time_update = struct.unpack_from('<I', buf, off+84)[0]
    sym_raw = buf[off+88:off+152]
    try:
        trade_symbol = sym_raw.decode('utf-16-le').split('\x00')[0]
    except:
        trade_symbol = ''
    trade_action = struct.unpack_from('<I', buf, off+152)[0]
    entry = struct.unpack_from('<I', buf, off+156)[0]
    price_open = struct.unpack_from('<d', buf, off+160)[0]
    price_close = struct.unpack_from('<d', buf, off+168)[0]
    sl = struct.unpack_from('<d', buf, off+176)[0]
    tp = struct.unpack_from('<d', buf, off+184)[0]
    trade_volume = struct.unpack_from('<Q', buf, off+192)[0]
    profit = struct.unpack_from('<d', buf, off+200)[0]
    rate_profit = struct.unpack_from('<d', buf, off+208)[0]
    rate_margin = struct.unpack_from('<d', buf, off+216)[0]
    commission = struct.unpack_from('<d', buf, off+224)[0]
    storage = struct.unpack_from('<d', buf, off+232)[0]
    expert = struct.unpack_from('<q', buf, off+240)[0]
    position_id = struct.unpack_from('<q', buf, off+248)[0]
    comment_raw = buf[off+256:off+320]
    try:
        comment = comment_raw.decode('utf-16-le').split('\x00')[0]
    except:
        comment = ''
    contract_size = struct.unpack_from('<d', buf, off+320)[0]
    digits = struct.unpack_from('<I', buf, off+328)[0]
    digits_currency = struct.unpack_from('<I', buf, off+332)[0]
    trade_reason = struct.unpack_from('<I', buf, off+336)[0]
    time_create_ms = struct.unpack_from('<i', buf, off+340)[0]
    time_update_ms = struct.unpack_from('<i', buf, off+344)[0]
    commission_fee = struct.unpack_from('<d', buf, off+348)[0]

    ACTION_NAMES = {0:'BUY', 1:'SELL', 2:'BUY LIMIT', 3:'SELL LIMIT', 4:'BUY STOP', 5:'SELL STOP', 6:'BUY STOP LIMIT', 7:'SELL STOP LIMIT'}
    ENTRY_NAMES = {0:'IN', 1:'OUT', 2:'INOUT', 3:'OUT_BY'}

    return {
        'ticket': deal, 'deal_id': deal_id, 'order': trade_order,
        'pos_id': position_id, 'symbol': trade_symbol,
        'action': ACTION_NAMES.get(trade_action, str(trade_action)),
        'entry': ENTRY_NAMES.get(entry, str(entry)),
        'price_open': price_open, 'price_close': price_close,
        'sl': sl, 'tp': tp,
        'volume': trade_volume / 100000000 if trade_volume else 0,
        'profit': profit, 'rate_profit': rate_profit,
        'commission': commission, 'storage': storage,
        'commission_fee': commission_fee,
        'digits': digits, 'comment': comment,
        'time': 1000 * time_create + time_create_ms,
    }

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect('wss://15.206.31.153:443/terminal', ssl=ssl_ctx, ping_interval=None,
        additional_headers={'Origin':'https://15.206.31.153:443'}) as ws:
        await ws.send(pack_data(0, aes_encrypt(STATIC_KEY, build_cmd(0, bytes(64)))))
        r = parse_resp(aes_decrypt(STATIC_KEY, (await asyncio.wait_for(ws.recv(), timeout=10))[8:]))
        sk = r['res_body'][66:]
        h = bytearray(912)
        pw = 'Trade@123'.encode('utf-16-le')
        h[4:4+len(pw)] = pw
        struct.pack_into('<I', h, 476, len('15.206.31.153'))
        ip_enc = '15.206.31.153'.encode('utf-16-le')
        h[480:480+len(ip_enc)] = ip_enc
        struct.pack_into('<Q', h, 736, 463558919)
        await ws.send(pack_data(28, aes_encrypt(sk, build_cmd(28, bytes(h)))))
        r = parse_resp(aes_decrypt(sk, (await asyncio.wait_for(ws.recv(), timeout=10))[8:]))
        print("Login OK")

        # Get ALL deals
        await ws.send(pack_data(5, aes_encrypt(sk, build_cmd(5, struct.pack('<II', 0, 0)))))

        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=2)
                if isinstance(resp, bytes) and len(resp) > 8:
                    r = parse_resp(aes_decrypt(sk, resp[8:]))
                    if r and r['cmd_id'] == 5:
                        buf = r['res_body']
                        deal_count = struct.unpack_from('<I', buf, 0)[0]
                        print(f"Deal count: {deal_count}")

                        # Parse first 10 deals
                        print(f"\n=== First 10 deals (356-byte records) ===")
                        for i in range(min(10, deal_count)):
                            off = 4 + i * RECORD_SIZE
                            d = parse_deal(buf, off)
                            if d:
                                ts = d['time']
                                from datetime import datetime, timezone
                                dt = datetime.fromtimestamp(ts/1000, tz=timezone.utc) if ts > 100000000000 else datetime.fromtimestamp(ts, tz=timezone.utc)
                                print(f"  [{i:3d}] ticket={d['ticket']} deal_id={d['deal_id']}")
                                print(f"        order={d['order']} pos_id={d['pos_id']}")
                                print(f"        {d['symbol']} {d['action']} {d['entry']} {d['volume']:.5f} lots")
                                print(f"        open={d['price_open']:.5f} close={d['price_close']:.5f} sl={d['sl']:.5f} tp={d['tp']:.5f}")
                                print(f"        profit={d['profit']:.2f} rate_profit={d['rate_profit']:.6f} commission={d['commission']:.2f} swap={d['storage']:.2f} comm_fee={d['commission_fee']:.2f}")
                                print(f"        digits={d['digits']} comment={d['comment'][:40]}")
                                print(f"        time={dt.strftime('%Y-%m-%d %H:%M:%S')}.{ts%1000:03d}")
                                print()

                        # Now find and display deposit deals and OUT deals with profit
                        print(f"\n=== Deposit deals (symbol=Balance) ===")
                        deposit_count = 0
                        for i in range(deal_count):
                            off = 4 + i * RECORD_SIZE
                            d = parse_deal(buf, off)
                            if d and d['symbol'] == 'Balance':
                                deposit_count += 1
                                if deposit_count <= 5:
                                    print(f"  [{i:3d}] ticket={d['ticket']} profit={d['profit']:.2f} comment={d['comment'][:40]}")
                        print(f"  Total deposit deals: {deposit_count}")

                        print(f"\n=== OUT deals with non-zero profit (last 10) ===")
                        out_deals = []
                        for i in range(deal_count):
                            off = 4 + i * RECORD_SIZE
                            d = parse_deal(buf, off)
                            if d and d['entry'] == 'OUT':
                                out_deals.append((i, d))
                        for idx, d in out_deals[-10:]:
                            print(f"  [{idx:3d}] ticket={d['ticket']} {d['symbol']} {d['action']} {d['volume']:.5f}")
                            print(f"        open={d['price_open']:.5f} close={d['price_close']:.5f}")
                            print(f"        profit={d['profit']:.2f} rate_profit={d['rate_profit']:.6f}")
                            print(f"        commission={d['commission']:.2f} swap={d['storage']:.2f} comm_fee={d['commission_fee']:.2f}")
                            print()
                        print(f"  Total OUT deals: {len(out_deals)}")

                        break
            except asyncio.TimeoutError:
                pass

asyncio.run(main())
