#!/usr/bin/env python3
"""
cmd10 Diagnostic: Capture raw cmd10 bodies using the client's capture hook.
"""
import asyncio
import struct
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(__file__))

from MT5API.mt5client.client import MT5Client

OUT_DIR = "/tmp/cmd10_bodies"
os.makedirs(OUT_DIR, exist_ok=True)

LOGIN = 463558919
PASSWORD = "Trade@123"
SERVER = "Exness-MT5Trial17"


async def main():
    async with MT5Client(LOGIN, PASSWORD, SERVER) as client:
        print(f"Connected. Balance: {client._account.balance if client._account else 'N/A'}")

        # Enable raw capture
        client._raw_cmd10_capture = []

        # Subscribe to XAUUSDm
        sym = "XAUUSDm"
        print(f"Subscribing to {sym}...")
        await client.subscribe(sym)
        await asyncio.sleep(1)

        # Open position
        print(f"\nOpening 0.01 lot BUY on {sym}...")
        result = await client.buy(sym, 0.01, magic=99999, comment="diag_test")
        if result:
            print(f"  order={result.order}, deal={result.deal}, retcode={result.retcode}")
        
        # Wait for pushes
        print("Waiting 8s for cmd10 pushes (open + price moves)...")
        await asyncio.sleep(8)

        # Modify SL/TP
        positions = await client.get_positions()
        for p in positions:
            if p.magic == 99999:
                print(f"\nModifying SL/TP on ticket={p.ticket}...")
                await client.modify_position(p.ticket, sl=p.price - 5, tp=p.price + 10)
                await asyncio.sleep(5)
                break

        # Close position
        positions = await client.get_positions()
        for p in positions:
            if p.magic == 99999:
                print(f"\nClosing ticket={p.ticket}...")
                await client.close_position(p.ticket)
                await asyncio.sleep(5)
                break

        # Get captured data
        raw_captures = client._raw_cmd10_capture
        client._raw_cmd10_capture = None

    # Process captured data
    print(f"\n=== Captured {len(raw_captures)} cmd10 bodies ===\n")

    all_tickets = set()
    all_symbols = set()
    all_prices = set()
    all_volumes = set()
    body_sizes = set()

    for idx, (cmd, body) in enumerate(raw_captures):
        body_len = len(body)
        body_sizes.add(body_len)
        fname = f"cmd{cmd}_{idx+1:03d}_len{body_len}"

        with open(os.path.join(OUT_DIR, f"{fname}.bin"), 'wb') as f:
            f.write(body)

        hex_lines = []
        for off in range(0, min(body_len, 512), 16):
            chunk = body[off:off+16]
            hex_part = ' '.join(f'{b:02x}' for b in chunk)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            hex_lines.append(f'  {off:04x}: {hex_part:<48s}  {ascii_part}')

        # Scan for candidates
        ticket_offs = []
        for i in range(0, min(body_len - 8, body_len), 4):
            val = struct.unpack_from('<Q', body, i)[0]
            if 10000000 <= val <= 9999999999:
                ticket_offs.append((i, val))
                all_tickets.add(i)

        symbol_offs = []
        for i in range(0, min(body_len - 64, body_len), 4):
            try:
                s = body[i:i+64].decode('utf-16-le', errors='ignore').split('\x00')[0]
                if s and 3 <= len(s) <= 20 and s[0].isalpha() and s.isascii():
                    if all(c.isalnum() or c in ('.', '_', '-') for c in s):
                        symbol_offs.append((i, s))
                        all_symbols.add(i)
            except:
                pass

        price_offs = []
        for i in range(0, min(body_len - 8, body_len), 8):
            val = struct.unpack_from('<d', body, i)[0]
            if 0.3 < val < 5000.0:
                price_offs.append((i, round(val, 5)))
                all_prices.add(i)

        vol_offs = []
        for i in range(0, min(body_len - 4, body_len), 4):
            val = struct.unpack_from('<I', body, i)[0]
            if 100000 <= val <= 10000000000:
                vol_offs.append((i, val, round(val / 100000000, 4)))
                all_volumes.add(i)

        # Also scan for int32 (potential type field at various offsets)
        int32_offs = []
        for i in range(0, min(body_len - 4, body_len), 4):
            val = struct.unpack_from('<I', body, i)[0]
            if val <= 10:  # small int like type
                int32_offs.append((i, val))

        with open(os.path.join(OUT_DIR, f"{fname}_analysis.txt"), 'w') as f:
            f.write(f"cmd={cmd}, body_len={body_len}\n\n")
            f.write("HEXDUMP:\n")
            f.write('\n'.join(hex_lines))
            f.write(f"\n\nTICKETS (u64): {len(ticket_offs)}\n")
            for off, val in ticket_offs[:30]:
                f.write(f"  @{off:4d}: {val}\n")
            f.write(f"\nSYMBOLS (UTF-16): {len(symbol_offs)}\n")
            for off, s in symbol_offs[:10]:
                f.write(f"  @{off:4d}: {s}\n")
            f.write(f"\nPRICES (f64, 0.3-5000): {len(price_offs)}\n")
            for off, val in price_offs[:30]:
                f.write(f"  @{off:4d}: {val}\n")
            f.write(f"\nVOLUMES (u32, lots*100M): {len(vol_offs)}\n")
            for off, raw_val, lots in vol_offs[:10]:
                f.write(f"  @{off:4d}: raw={raw_val} lots={lots}\n")
            f.write(f"\nSMALL INTS (u32, 0-10): {len(int32_offs)}\n")
            for off, val in int32_offs[:30]:
                f.write(f"  @{off:4d}: {val}\n")

        print(f"[{idx+1:2d}] len={body_len:4d} "
              f"syms={[(o,s) for o,s in symbol_offs[:2]]} "
              f"tix={[(o,v) for o,v in ticket_offs[:2]]} "
              f"prices={[(o,v) for o,v in price_offs[:3]]}")

    # Consistency analysis
    print(f"\n=== CONSISTENCY ANALYSIS ===")
    print(f"  Body sizes: {sorted(body_sizes)}")
    print(f"  Ticket offsets seen: {sorted(all_tickets)}")
    print(f"  Symbol offsets seen: {sorted(all_symbols)}")
    print(f"  Price offsets seen:  {sorted(all_prices)}")
    print(f"  Volume offsets seen: {sorted(all_volumes)}")

    with open(os.path.join(OUT_DIR, 'summary.json'), 'w') as f:
        json.dump({
            'count': len(raw_captures),
            'sizes': sorted(body_sizes),
            'ticket_offsets': sorted(all_tickets),
            'symbol_offsets': sorted(all_symbols),
            'price_offsets': sorted(all_prices),
            'volume_offsets': sorted(all_volumes),
        }, f, indent=2)
    print(f"\nFiles saved to {OUT_DIR}/")


if __name__ == '__main__':
    asyncio.run(main())
