"""
Subscribe ALL 355 symbols — ONE command, stream quotes.
"""
import asyncio
import sys
import time

sys.path.insert(0, '/media/teamx/New Volume/AlgoMinds/MT5_API_Test/OwnMt5API/MT5API')

from mt5client import MT5Client


async def main():
    async with MT5Client(
        login=463558919,
        password='Trade@123',
        server='Exness-MT5Trial17',
    ) as client:
        all_symbols = sorted(client._symbols.keys())
        print(f"Loaded {len(all_symbols)} symbols.")
        print()

        # ONE subscribe command for ALL symbols
        t0 = time.time()
        await client.send_subscribe(all_symbols)
        elapsed = time.time() - t0
        print(f"Sent ONE subscribe for {len(all_symbols)} symbols in {elapsed:.3f}s")
        print()

        # Wait for quotes
        print("Waiting for quotes...")
        for i in range(6):
            await asyncio.sleep(5)
            live = sum(1 for s in all_symbols if client.get_quote(s) and client.get_quote(s).bid > 0)
            print(f"  [{(i+1)*5}s] {live} live")

        # Stream for 10 seconds
        print()
        print("Streaming live quotes for 10 seconds...")
        t0 = time.time()
        updates = {}
        while time.time() - t0 < 10:
            for sym in all_symbols:
                q = client.get_quote(sym)
                if q and q.bid > 0:
                    if sym not in updates:
                        updates[sym] = 0
                    updates[sym] += 1
            await asyncio.sleep(0.05)

        # Print top 30 by updates
        print()
        print(f"{'Symbol':<20} {'Bid':>14} {'Ask':>14} {'Spread':>10} {'Updates':>8}")
        print("-" * 70)

        live_count = 0
        for sym in sorted(updates.keys(), key=lambda s: -updates[s])[:30]:
            q = client.get_quote(sym)
            if q and q.bid > 0:
                spread = q.ask - q.bid
                print(f"{sym:<20} {q.bid:>14.5f} {q.ask:>14.5f} {spread:>10.5f} {updates[sym]:>8}")
                live_count += 1

        total_live = sum(1 for s in all_symbols if client.get_quote(s) and client.get_quote(s).bid > 0)
        print()
        print(f"{'=' * 70}")
        print(f"TOTAL SYMBOLS:    {len(all_symbols)}")
        print(f"LIVE (streaming): {total_live}")
        print(f"NO DATA:          {len(all_symbols) - total_live}")
        print(f"TOTAL UPDATES:    {sum(updates.values())}")


if __name__ == '__main__':
    asyncio.run(main())
