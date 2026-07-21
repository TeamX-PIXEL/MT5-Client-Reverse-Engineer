#!/usr/bin/env python3
import asyncio, sys, time

sys.path.insert(0, "./MT5API")
from mt5client import MT5Client

ACCOUNTS = [
    {"login": 463558919, "password": "Trade@123", "server": "Exness-MT5Trial17", "label": "ACC-463"},
    {"login": 270146102, "password": "Trade@123", "server": "Exness-MT5Trial17", "label": "ACC-270"},
]

SYMBOLS = ["EURUSDm", "GBPUSDm", "XAUUSDm"]

results = {"passed": 0, "failed": 0, "errors": []}


def ok(label, msg=""):
    results["passed"] += 1
    print(f"  \u2713 {label}" + (f" {msg}" if msg else ""))


def fail(label, msg=""):
    results["failed"] += 1
    results["errors"].append(f"{label}: {msg}")
    print(f"  \u2717 {label}: {msg}")


async def account_task(acc):
    label = acc["label"]

    async with MT5Client(login=acc["login"], password=acc["password"], server=acc["server"]) as client:
        acct = await client.get_account()
        if acct:
            ok(f"[{label}] AUTH", f"balance={acct.balance:.2f}")
        else:
            fail(f"[{label}] AUTH")
            return

        syms = await client.get_symbols()
        ok(f"[{label}] SYMBOLS", f"{len(syms)} loaded")

        await client.send_subscribe(SYMBOLS)
        for i in range(20):
            await asyncio.sleep(0.5)
            if all(client.get_quote(s) for s in SYMBOLS):
                break
        ok(f"[{label}] SUBSCRIBE", f"{sum(1 for s in SYMBOLS if client.get_quote(s))}/{len(SYMBOLS)}")

        # --- 3 parallel market orders ---
        trade_tasks = []
        for sym in SYMBOLS:
            q = client.get_quote(sym)
            if not q:
                continue
            trade_tasks.append(client.buy(sym, 0.01))

        results_list = await asyncio.gather(*trade_tasks, return_exceptions=True)
        market_trades = []
        for i, r in enumerate(results_list):
            sym = SYMBOLS[i] if i < len(SYMBOLS) else "?"
            if isinstance(r, Exception):
                fail(f"[{label}] MARKET {sym}", str(r))
            elif r.retcode == 10009:
                market_trades.append({"order": r.order, "symbol": sym, "type": 0, "volume": 0.01})
                ok(f"[{label}] MARKET {sym}", f"order={r.order}")
            else:
                fail(f"[{label}] MARKET {sym}", f"retcode={r.retcode}")
        ok(f"[{label}] MARKETS", f"{len(market_trades)}/{len(SYMBOLS)}")

        # --- 3 parallel pending orders ---
        pending_tasks = []
        pending_meta = []
        for sym in SYMBOLS:
            q = client.get_quote(sym)
            if not q:
                continue
            if "XAU" in sym:
                price = round(q.ask - 500 * 0.01, 2)
                pending_tasks.append(client.buy_limit(sym, 0.01, price))
                pending_meta.append({"symbol": sym, "type": 2, "volume": 0.01, "price": price})
            elif "GBP" in sym:
                price = round(q.bid + 200 * 0.00001, 5)
                pending_tasks.append(client.sell_limit(sym, 0.01, price))
                pending_meta.append({"symbol": sym, "type": 3, "volume": 0.01, "price": price})
            else:
                price = round(q.ask - 200 * 0.00001, 5)
                pending_tasks.append(client.buy_limit(sym, 0.01, price))
                pending_meta.append({"symbol": sym, "type": 2, "volume": 0.01, "price": price})

        results_list = await asyncio.gather(*pending_tasks, return_exceptions=True)
        pending_trades = []
        for i, r in enumerate(results_list):
            sym = pending_meta[i]["symbol"] if i < len(pending_meta) else "?"
            if isinstance(r, Exception):
                fail(f"[{label}] PENDING {sym}", str(r))
            elif r.retcode == 10009:
                meta = pending_meta[i]
                pending_trades.append({"order": r.order, **meta})
                ok(f"[{label}] PENDING {sym}", f"order={r.order}")
            else:
                fail(f"[{label}] PENDING {sym}", f"retcode={r.retcode}")
        ok(f"[{label}] PENDINGS", f"{len(pending_trades)}/{len(SYMBOLS)}")

        await asyncio.sleep(1)

        # --- Position list ---
        positions = await client.get_positions()
        ok(f"[{label}] POSITIONS", f"{len(positions)} open")

        # --- Orders list ---
        orders = await client.get_orders()
        ok(f"[{label}] ORDERS", f"{len(orders)} pending")

        # --- Modify position SL/TP (use cached data bypass) ---
        mod_ok = 0
        for t in market_trades[:2]:
            r = await client.modify_position(
                t["order"], sl=0.0, tp=0.0,
                symbol=t["symbol"], pos_type=t["type"], volume=t["volume"]
            )
            if r.retcode == 10009:
                mod_ok += 1
                ok(f"[{label}] MODIFY POS #{t['order']}", "ok")
            else:
                fail(f"[{label}] MODIFY POS #{t['order']}", f"retcode={r.retcode}")
        ok(f"[{label}] MODIFY POS", f"{mod_ok}/2")

        # --- Modify pending order SL/TP ---
        mod_pend = 0
        for t in pending_trades[:2]:
            r = await client.modify_order(t["order"], sl=0, tp=0)
            if r.retcode == 10009:
                mod_pend += 1
                ok(f"[{label}] MODIFY ORD #{t['order']}", "ok")
            else:
                fail(f"[{label}] MODIFY ORD #{t['order']}", f"retcode={r.retcode}")
        ok(f"[{label}] MODIFY ORD", f"{mod_pend}/2")

        # --- Cancel all pending (use bypass) ---
        cancel_ok = 0
        cancel_tasks = [
            client.cancel_order(
                t["order"], symbol=t["symbol"], order_type=t["type"],
                volume=t["volume"], price=t["price"]
            )
            for t in pending_trades
        ]
        results_list = await asyncio.gather(*cancel_tasks, return_exceptions=True)
        for i, r in enumerate(results_list):
            tkt = pending_trades[i]["order"]
            if isinstance(r, Exception):
                fail(f"[{label}] CANCEL #{tkt}", str(r))
            elif r.retcode == 10009:
                cancel_ok += 1
                ok(f"[{label}] CANCEL #{tkt}", "ok")
            else:
                fail(f"[{label}] CANCEL #{tkt}", f"retcode={r.retcode}")
        ok(f"[{label}] CANCEL", f"{cancel_ok}/{len(pending_trades)}")

        # --- Close all market positions (use bypass) ---
        close_ok = 0
        close_tasks = [
            client.close_position(
                t["order"], symbol=t["symbol"], pos_type=t["type"], volume=t["volume"]
            )
            for t in market_trades
        ]
        results_list = await asyncio.gather(*close_tasks, return_exceptions=True)
        for i, r in enumerate(results_list):
            tkt = market_trades[i]["order"]
            if isinstance(r, Exception):
                fail(f"[{label}] CLOSE #{tkt}", str(r))
            elif r.retcode == 10009:
                close_ok += 1
                ok(f"[{label}] CLOSE #{tkt}", f"deal={r.deal}")
            else:
                fail(f"[{label}] CLOSE #{tkt}", f"retcode={r.retcode}")
        ok(f"[{label}] CLOSE", f"{close_ok}/{len(market_trades)}")

        # --- Candles ---
        candles = await client.get_candles("EURUSDm", "M1", 5)
        ok(f"[{label}] CANDLE", f"{len(candles)} bars")

        # --- Deals ---
        deals = await client.get_deals()
        ok(f"[{label}] DEALS", f"{len(deals)} historical")


async def main():
    print("=" * 70)
    print("MULTI-ACCOUNT PARALLEL TEST")
    print(f"Accounts: {[a['login'] for a in ACCOUNTS]}")
    print(f"Symbols: {SYMBOLS}")
    print("=" * 70)

    t0 = time.time()
    await asyncio.gather(*[account_task(acc) for acc in ACCOUNTS])
    elapsed = time.time() - t0

    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    total = results["passed"] + results["failed"]
    print(f"  Passed: {results['passed']}/{total}")
    print(f"  Failed: {results['failed']}/{total}")
    print(f"  Time:   {elapsed:.1f}s")
    if results["errors"]:
        print(f"\n  FAILURES:")
        for e in results["errors"]:
            print(f"    - {e}")
    print("=" * 70)


asyncio.run(main())
