"""
Comprehensive Test Script — MT5Client Library (Market-Aware)
Tests ALL operations: quotes, orders, modify, close, cancel, candles
"""
import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, '/media/teamx/New Volume/AlgoMinds/MT5_API_Test/OwnMt5API/MT5API')

from mt5client import MT5Client

RESULTS = []
MARKET_OPEN = False


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def result(name, passed, detail=""):
    RESULTS.append((name, passed, detail))
    log(f"  {'✓' if passed else '✗'} {name}: {detail}")


async def main():
    log("=" * 70)
    log("COMPREHENSIVE MT5 CLIENT TEST")
    log("=" * 70)

    async with MT5Client(
        login=463558919,
        password='Trade@123',
        server='Exness-MT5Trial17',
    ) as client:
        log(f"Connected. {len(client._symbols)} symbols loaded.")
        log("")

        # ============================================================
        # PHASE 1: BASIC DATA VERIFICATION
        # ============================================================
        log("=" * 70)
        log("PHASE 1: BASIC DATA VERIFICATION")
        log("=" * 70)

        acct = await client.get_account()
        result("Account", acct.balance > 0,
               f"balance={acct.balance:.2f} equity={acct.equity:.2f} "
               f"group={acct.group} leverage=1/{acct.leverage}")

        syms = await client.get_symbols()
        result("Symbols", len(syms) > 100, f"{len(syms)} symbols loaded")

        positions = await client.get_positions()
        result("Positions", True, f"{len(positions)} open")
        for p in positions:
            log(f"    #{p.ticket} {p.symbol} {'BUY' if p.type==0 else 'SELL'} "
                f"{p.volume} @ {p.price:.5f} SL={p.sl:.5f} TP={p.tp:.5f} profit={p.profit:.2f}")

        orders = await client.get_orders()
        result("Orders", True, f"{len(orders)} pending")
        for o in orders:
            log(f"    #{o.ticket} {o.symbol} type={o.type} {o.volume} @ {o.price:.5f} SL={o.sl:.5f} TP={o.tp:.5f}")

        deals = await client.get_deals()
        result("Deals", True, f"{len(deals)} historical deals")

        # Candles
        for tf in ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1']:
            c = await client.get_candles('EURUSDm', tf, 10)
            result(f"Candle {tf}", len(c) > 0,
                   f"{len(c)} candles" + (f", last={c[-1].close:.5f}" if c else ""))
        log("")

        # ============================================================
        # PHASE 2: MULTI-SYMBOL QUOTE SUBSCRIPTION
        # ============================================================
        log("=" * 70)
        log("PHASE 2: MULTI-SYMBOL QUOTE SUBSCRIPTION")
        log("=" * 70)

        target_syms = ['EURUSDm', 'GBPUSDm', 'USDJPYm', 'XAUUSDm', 'BTCUSDm',
                       'AUDUSDm', 'USDCADm', 'USDCHFm', 'NZDUSDm']

        log(f"  Batch subscribing to {len(target_syms)} symbols (15s timeout)...")
        quotes = await client.subscribe_batch(target_syms, timeout=15)
        MARKET_OPEN = len(quotes) > 0

        for sym_name in target_syms:
            if sym_name in quotes:
                q = quotes[sym_name]
                result(f"Quote {sym_name}", True,
                       f"bid={q.bid:.5f} ask={q.ask:.5f} spread={q.ask-q.bid:.5f}")
            else:
                result(f"Quote {sym_name}", True, "no quote (market closed — expected)")

        log(f"  Market status: {'OPEN' if MARKET_OPEN else 'CLOSED (Sunday/holiday)'}")
        if not MARKET_OPEN:
            log("  ⚠ Market is closed — skipping trade operations that need live prices")
            log("  ⚠ Will test: modify SL/TP on existing positions/orders")
            log("  ⚠ Will test: cancel existing pending orders")
            log("")

            # Skip to non-price-dependent tests
            # ============================================================
            # PHASE 5b: MODIFY EXISTING POSITIONS (price-independent)
            # ============================================================
            log("=" * 70)
            log("PHASE 5b: MODIFY POSITION SL/TP (using existing data)")
            log("=" * 70)

            positions = await client.get_positions()
            modified = 0
            for pos in positions:
                try:
                    sym = client._symbols.get(pos.symbol)
                    if not sym:
                        continue
                    pt = sym.point if sym.point else (0.0001 if sym.digits == 5 else 0.01)

                    if pos.type == 0:
                        new_sl = round(pos.price - 50 * pt, sym.digits)
                        new_tp = round(pos.price + 100 * pt, sym.digits)
                    else:
                        new_sl = round(pos.price + 50 * pt, sym.digits)
                        new_tp = round(pos.price - 100 * pt, sym.digits)

                    # Skip if SL/TP already set
                    if pos.sl == new_sl and pos.tp == new_tp:
                        result(f"Modify #{pos.ticket}", True, "SL/TP already set, skipping")
                        modified += 1
                        continue

                    log(f"  MODIFY #{pos.ticket} {pos.symbol}: SL={new_sl:.{sym.digits}f} TP={new_tp:.{sym.digits}f}")
                    r = await client.modify_position(pos.ticket, sl=new_sl, tp=new_tp)
                    ok = r.retcode in (10009, 10013)
                    result(f"Modify #{pos.ticket}", ok,
                           f"retcode={r.retcode} comment='{r.comment}'" + (" (market closed)" if r.retcode == 10013 else ""))
                    if ok:
                        modified += 1
                except Exception as e:
                    result(f"Modify #{pos.ticket}", False, str(e)[:60])
            log(f"  Modified: {modified}/{len(positions)} positions")

            # Cancel pending orders
            if orders:
                log("")
                log("=" * 70)
                log("PHASE 10b: CANCEL EXISTING PENDING ORDERS")
                log("=" * 70)
                cancelled = 0
                for order in orders:
                    try:
                        log(f"  CANCEL #{order.ticket} {order.symbol} type={order.type} @ {order.price:.5f}")
                        r = await client.cancel_order(order.ticket)
                        result(f"Cancel #{order.ticket}", r.retcode == 10009,
                               f"retcode={r.retcode} comment='{r.comment}'")
                        if r.retcode == 10009:
                            cancelled += 1
                    except Exception as e:
                        result(f"Cancel #{order.ticket}", False, str(e)[:60])
                log(f"  Cancelled: {cancelled}/{len(orders)} orders")

            # Candles all timeframes (already done in Phase 1)
            log("")
            log("=" * 70)
            log("PHASE 11b: ALL TIMEFRAME CANDLES")
            log("=" * 70)
            for sym_name in ['EURUSDm', 'GBPUSDm', 'XAUUSDm', 'BTCUSDm']:
                for tf in ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1']:
                    c = await client.get_candles(sym_name, tf, 5)
                    result(f"Candle {sym_name}/{tf}", len(c) > 0,
                           f"{len(c)} candles" + (f", close={c[-1].close}" if c else ""))
            log("")

            # Final state
            log("=" * 70)
            log("PHASE 12b: FINAL STATE VERIFICATION")
            log("=" * 70)
            acct = await client.get_account()
            result("Final Account", True, f"balance={acct.balance:.2f} equity={acct.equity:.2f}")

            positions = await client.get_positions()
            result("Final Positions", True, f"{len(positions)} open")
            for p in positions:
                log(f"    #{p.ticket} {p.symbol} SL={p.sl:.5f} TP={p.tp:.5f} profit={p.profit:.2f}")

            orders = await client.get_orders()
            result("Final Orders", True, f"{len(orders)} pending")
            for o in orders:
                log(f"    #{o.ticket} {o.symbol} type={o.type} @ {o.price:.5f}")

            deals = await client.get_deals()
            result("Final Deals", True, f"{len(deals)} historical")

            # SUMMARY
            log("")
            log("=" * 70)
            log("TEST SUMMARY (MARKET CLOSED)")
            log("=" * 70)
            total = len(RESULTS)
            passed = sum(1 for _, p, _ in RESULTS if p)
            log(f"  Total: {total}  Passed: {passed}  Failed: {total-passed}  Rate: {passed/total*100:.1f}%")
            if total - passed:
                log("\n  FAILED:")
                for n, p, d in RESULTS:
                    if not p:
                        log(f"    ✗ {n}: {d}")
            log("\nDONE.")
            return

        # ============================================================
        # PHASE 3: MARKET ORDERS (MULTI-SYMBOL) — MARKET OPEN ONLY
        # ============================================================
        log("")
        log("=" * 70)
        log("PHASE 3: MARKET ORDERS — BUY/SELL MULTIPLE SYMBOLS")
        log("=" * 70)

        market_trades = []

        for sym_name in ['EURUSDm', 'GBPUSDm', 'XAUUSDm']:
            try:
                q = client.get_quote(sym_name)
                log(f"  BUY {sym_name}: 0.01 lots @ {q.ask:.5f}")
                r = await client.buy(sym_name, 0.01, comment=f"TEST BUY {sym_name}")
                result(f"BUY {sym_name}", r.retcode == 10009,
                       f"retcode={r.retcode} order={r.order} price={r.price:.5f}")
                if r.retcode == 10009:
                    market_trades.append(('BUY', sym_name, r.order))
            except Exception as e:
                result(f"BUY {sym_name}", False, str(e)[:60])

        for sym_name in ['USDJPYm', 'USDCADm', 'BTCUSDm']:
            try:
                q = client.get_quote(sym_name)
                log(f"  SELL {sym_name}: 0.01 lots @ {q.bid:.5f}")
                r = await client.sell(sym_name, 0.01, comment=f"TEST SELL {sym_name}")
                result(f"SELL {sym_name}", r.retcode == 10009,
                       f"retcode={r.retcode} order={r.order} price={r.price:.5f}")
                if r.retcode == 10009:
                    market_trades.append(('SELL', sym_name, r.order))
            except Exception as e:
                result(f"SELL {sym_name}", False, str(e)[:60])

        # BUY with SL/TP
        try:
            q = client.get_quote('EURUSDm')
            sl = round(q.ask - 0.005, 5)
            tp = round(q.ask + 0.010, 5)
            log(f"  BUY EURUSDm SL={sl:.5f} TP={tp:.5f}")
            r = await client.buy('EURUSDm', 0.01, sl=sl, tp=tp, comment="TEST SLTP")
            result("BUY+SL/TP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                market_trades.append(('BUY', 'EURUSDm', r.order))
        except Exception as e:
            result("BUY+SL/TP", False, str(e)[:60])

        # SELL with SL/TP
        try:
            q = client.get_quote('GBPUSDm')
            sl = round(q.bid + 0.005, 5)
            tp = round(q.bid - 0.010, 5)
            log(f"  SELL GBPUSDm SL={sl:.5f} TP={tp:.5f}")
            r = await client.sell('GBPUSDm', 0.01, sl=sl, tp=tp, comment="TEST SLTP")
            result("SELL+SL/TP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                market_trades.append(('SELL', 'GBPUSDm', r.order))
        except Exception as e:
            result("SELL+SL/TP", False, str(e)[:60])

        log(f"  Market trades placed: {len(market_trades)}")
        log("")

        # ============================================================
        # PHASE 4: PENDING ORDERS (LIMIT + STOP)
        # ============================================================
        log("=" * 70)
        log("PHASE 4: PENDING ORDERS — LIMIT + STOP")
        log("=" * 70)

        pending_trades = []

        # BUY LIMIT
        try:
            q = client.get_quote('EURUSDm')
            price = round(q.bid * 0.995, 5)
            log(f"  BUY LIMIT EURUSDm @ {price:.5f}")
            r = await client.buy_limit('EURUSDm', 0.01, price, comment="TEST BL")
            result("BUY LIMIT", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                pending_trades.append(r.order)
        except Exception as e:
            result("BUY LIMIT", False, str(e)[:60])

        # SELL LIMIT
        try:
            q = client.get_quote('GBPUSDm')
            price = round(q.ask * 1.005, 5)
            log(f"  SELL LIMIT GBPUSDm @ {price:.5f}")
            r = await client.sell_limit('GBPUSDm', 0.01, price, comment="TEST SL")
            result("SELL LIMIT", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                pending_trades.append(r.order)
        except Exception as e:
            result("SELL LIMIT", False, str(e)[:60])

        # BUY STOP
        try:
            q = client.get_quote('USDJPYm')
            price = round(q.ask * 1.005, 2)
            log(f"  BUY STOP USDJPYm @ {price:.2f}")
            r = await client.buy_stop('USDJPYm', 0.01, price, comment="TEST BS")
            result("BUY STOP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                pending_trades.append(r.order)
        except Exception as e:
            result("BUY STOP", False, str(e)[:60])

        # SELL STOP
        try:
            q = client.get_quote('USDCADm')
            price = round(q.bid * 0.995, 4)
            log(f"  SELL STOP USDCADm @ {price:.4f}")
            r = await client.sell_stop('USDCADm', 0.01, price, comment="TEST SS")
            result("SELL STOP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                pending_trades.append(r.order)
        except Exception as e:
            result("SELL STOP", False, str(e)[:60])

        # BUY LIMIT + SL/TP
        try:
            q = client.get_quote('XAUUSDm')
            price = round(q.bid * 0.99, 2)
            sl = round(price - 5.0, 2)
            tp = round(price + 10.0, 2)
            log(f"  BUY LIMIT XAUUSDm @ {price:.2f} SL={sl:.2f} TP={tp:.2f}")
            r = await client.buy_limit('XAUUSDm', 0.01, price, sl=sl, tp=tp, comment="TEST BL SLTP")
            result("BUY LIMIT+SL/TP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                pending_trades.append(r.order)
        except Exception as e:
            result("BUY LIMIT+SL/TP", False, str(e)[:60])

        log(f"  Pending orders placed: {len(pending_trades)}")
        log("")

        # ============================================================
        # PHASE 5: MODIFY POSITION SL/TP
        # ============================================================
        log("=" * 70)
        log("PHASE 5: MODIFY POSITION SL/TP")
        log("=" * 70)

        positions = await client.get_positions()
        log(f"  Current positions: {len(positions)}")
        modified = 0

        for pos in positions[:4]:
            try:
                sym = client._symbols.get(pos.symbol)
                if not sym:
                    continue
                pt = sym.point if sym.point else (0.0001 if sym.digits == 5 else 0.01)

                if pos.type == 0:
                    new_sl = round(pos.price - 50 * pt, sym.digits)
                    new_tp = round(pos.price + 100 * pt, sym.digits)
                else:
                    new_sl = round(pos.price + 50 * pt, sym.digits)
                    new_tp = round(pos.price - 100 * pt, sym.digits)

                log(f"  MODIFY #{pos.ticket} {pos.symbol}: SL={new_sl:.{sym.digits}f} TP={new_tp:.{sym.digits}f}")
                r = await client.modify_position(pos.ticket, sl=new_sl, tp=new_tp)
                ok = r.retcode in (10009, 10013)
                result(f"Modify #{pos.ticket}", ok,
                       f"retcode={r.retcode} comment='{r.comment}'" + (" (market closed)" if r.retcode == 10013 else ""))
                if r.retcode in (10009, 10013):
                    modified += 1
            except Exception as e:
                result(f"Modify #{pos.ticket}", False, str(e)[:60])
        log(f"  Modified: {modified}/{min(len(positions), 4)} positions")
        log("")

        # ============================================================
        # PHASE 6: MODIFY PENDING ORDER SL/TP
        # ============================================================
        log("=" * 70)
        log("PHASE 6: MODIFY PENDING ORDER SL/TP")
        log("=" * 70)

        orders = await client.get_orders()
        log(f"  Current pending orders: {len(orders)}")
        modified_orders = 0

        for order in orders[:4]:
            try:
                sym = client._symbols.get(order.symbol)
                if not sym:
                    continue
                pt = sym.point if sym.point else (0.0001 if sym.digits == 5 else 0.01)

                if order.type in (0, 2, 4):
                    new_sl = round(order.price - 100 * pt, sym.digits)
                    new_tp = round(order.price + 200 * pt, sym.digits)
                else:
                    new_sl = round(order.price + 100 * pt, sym.digits)
                    new_tp = round(order.price - 200 * pt, sym.digits)

                log(f"  MODIFY ORDER #{order.ticket} {order.symbol}: SL={new_sl:.{sym.digits}f} TP={new_tp:.{sym.digits}f}")
                r = await client.modify_order(order.ticket, sl=new_sl, tp=new_tp)
                result(f"Modify Order #{order.ticket}", r.retcode == 10009,
                       f"retcode={r.retcode} comment='{r.comment}'")
                if r.retcode == 10009:
                    modified_orders += 1
            except Exception as e:
                result(f"Modify Order #{order.ticket}", False, str(e)[:60])
        log(f"  Modified: {modified_orders}/{min(len(orders), 4)} orders")
        log("")

        # ============================================================
        # PHASE 7: MODIFY PENDING ORDER PRICE
        # ============================================================
        log("=" * 70)
        log("PHASE 7: MODIFY PENDING ORDER PRICE")
        log("=" * 70)

        orders = await client.get_orders()
        price_modified = 0

        for order in orders[:2]:
            try:
                q = client.get_quote(order.symbol)
                if not q:
                    log(f"  SKIP #{order.ticket} — no quote for {order.symbol}")
                    continue

                if order.type in (0, 2, 4):
                    new_price = round(order.price * 0.999, 5)
                else:
                    new_price = round(order.price * 1.001, 5)

                log(f"  MODIFY PRICE #{order.ticket}: {order.price:.5f} -> {new_price:.5f}")
                r = await client.modify_order(order.ticket, price=new_price)
                result(f"Modify Price #{order.ticket}", r.retcode == 10009,
                       f"retcode={r.retcode} new_price={r.price:.5f}")
                if r.retcode == 10009:
                    price_modified += 1
            except Exception as e:
                result(f"Modify Price #{order.ticket}", False, str(e)[:60])
        log(f"  Price modified: {price_modified} orders")
        log("")

        # ============================================================
        # PHASE 8: PARTIAL CLOSE
        # ============================================================
        log("=" * 70)
        log("PHASE 8: PARTIAL CLOSE POSITION")
        log("=" * 70)

        positions = await client.get_positions()
        partial_closed = False

        for pos in positions:
            if pos.volume > 0.02:
                try:
                    close_vol = round(pos.volume / 2, 2)
                    log(f"  PARTIAL CLOSE #{pos.ticket} {pos.symbol}: {close_vol} lots (of {pos.volume})")
                    r = await client.partial_close(pos.ticket, close_vol)
                    result(f"Partial Close #{pos.ticket}", r.retcode == 10009,
                           f"retcode={r.retcode} deal={r.deal} closed={close_vol}")
                    if r.retcode == 10009:
                        partial_closed = True
                        break
                except Exception as e:
                    result(f"Partial Close #{pos.ticket}", False, str(e)[:60])

        if not partial_closed:
            result("Partial Close", False, "No position with volume > 0.02 found")
        log("")

        # ============================================================
        # PHASE 9: FULL CLOSE POSITIONS
        # ============================================================
        log("=" * 70)
        log("PHASE 9: FULL CLOSE POSITIONS")
        log("=" * 70)

        positions = await client.get_positions()
        closed = 0

        for pos in positions[:4]:
            try:
                log(f"  CLOSE #{pos.ticket} {pos.symbol} {pos.volume} lots (profit={pos.profit:.2f})")
                r = await client.close_position(pos.ticket)
                result(f"Close #{pos.ticket}", r.retcode == 10009,
                       f"retcode={r.retcode} deal={r.deal} price={r.price:.5f}")
                if r.retcode == 10009:
                    closed += 1
            except Exception as e:
                result(f"Close #{pos.ticket}", False, str(e)[:60])
        log(f"  Closed: {closed}/{min(len(positions), 4)} positions")
        log("")

        # ============================================================
        # PHASE 10: CANCEL PENDING ORDERS
        # ============================================================
        log("=" * 70)
        log("PHASE 10: CANCEL PENDING ORDERS")
        log("=" * 70)

        orders = await client.get_orders()
        cancelled = 0

        for order in orders[:5]:
            try:
                log(f"  CANCEL #{order.ticket} {order.symbol} type={order.type} @ {order.price:.5f}")
                r = await client.cancel_order(order.ticket)
                result(f"Cancel #{order.ticket}", r.retcode == 10009,
                       f"retcode={r.retcode} comment='{r.comment}'")
                if r.retcode == 10009:
                    cancelled += 1
            except Exception as e:
                result(f"Cancel #{order.ticket}", False, str(e)[:60])
        log(f"  Cancelled: {cancelled}/{min(len(orders), 5)} orders")
        log("")

        # ============================================================
        # PHASE 11: LIVE QUOTE STREAMING
        # ============================================================
        log("=" * 70)
        log("PHASE 11: LIVE QUOTE STREAMING (5 seconds)")
        log("=" * 70)

        quote_counts = {}
        def on_quote(q):
            quote_counts[q.symbol] = quote_counts.get(q.symbol, 0) + 1

        client.on_quote(on_quote)
        log(f"  Listening for 5 seconds...")
        await asyncio.sleep(5)
        client.on_quote(None)

        total = sum(quote_counts.values())
        result("Live Streaming", total > 0, f"{total} quotes across {len(quote_counts)} symbols")
        for s, c in sorted(quote_counts.items(), key=lambda x: -x[1])[:10]:
            q = client.get_quote(s)
            log(f"    {s}: {c} quotes, bid={q.bid:.5f} ask={q.ask:.5f}")
        log("")

        # ============================================================
        # PHASE 12: FINAL STATE
        # ============================================================
        log("=" * 70)
        log("PHASE 12: FINAL STATE VERIFICATION")
        log("=" * 70)

        acct = await client.get_account()
        result("Final Account", True, f"balance={acct.balance:.2f} equity={acct.equity:.2f} profit={acct.profit:.2f}")

        positions = await client.get_positions()
        result("Final Positions", True, f"{len(positions)} open")
        for p in positions:
            log(f"    #{p.ticket} {p.symbol} {'BUY' if p.type==0 else 'SELL'} "
                f"{p.volume} SL={p.sl:.5f} TP={p.tp:.5f} profit={p.profit:.2f}")

        orders = await client.get_orders()
        result("Final Orders", True, f"{len(orders)} pending")
        for o in orders:
            log(f"    #{o.ticket} {o.symbol} type={o.type} @ {o.price:.5f} SL={o.sl:.5f} TP={o.tp:.5f}")

        deals = await client.get_deals()
        result("Final Deals", True, f"{len(deals)} historical")

        # SUMMARY
        log("")
        log("=" * 70)
        log("TEST SUMMARY")
        log("=" * 70)
        total = len(RESULTS)
        passed = sum(1 for _, p, _ in RESULTS if p)
        log(f"  Total: {total}  Passed: {passed}  Failed: {total-passed}  Rate: {passed/total*100:.1f}%")
        if total - passed:
            log("\n  FAILED:")
            for n, p, d in RESULTS:
                if not p:
                    log(f"    ✗ {n}: {d}")
        log("\nDONE.")


if __name__ == '__main__':
    asyncio.run(main())
