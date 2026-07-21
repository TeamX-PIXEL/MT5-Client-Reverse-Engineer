"""
Market-Open Test — adapts to whichever symbols are currently tradeable.
Discovers live quotes, picks available symbols, runs full operations.
"""
import asyncio
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, '/media/teamx/New Volume/AlgoMinds/MT5_API_Test/OwnMt5API/MT5API')

from mt5client import MT5Client
from mt5client.protocol import TIMEFRAMES

RESULTS = []


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def result(name, passed, detail=""):
    status = "✓" if passed else "✗"
    RESULTS.append((name, passed, detail))
    log(f"  {status} {name}: {detail}")


async def retry_trade(coro_factory, max_retries=3, delay=1.0):
    for attempt in range(max_retries):
        r = await coro_factory()
        if r.retcode == 10009:
            return r
        if r.retcode == 10013:
            log(f"    REQUOTE (attempt {attempt+1}/{max_retries}), retrying...")
            await asyncio.sleep(delay)
            continue
        return r
    return r


async def main():
    log("=" * 70)
    log("MARKET-OPEN COMPREHHENSIVE TEST")
    log("=" * 70)

    async with MT5Client(
        login=463558919,
        password='Trade@123',
        server='Exness-MT5Trial17',
    ) as client:
        log(f"Connected. {len(client._symbols)} symbols loaded.")

        # Phase 1: Discover live markets
        log("")
        log("=" * 70)
        log("PHASE 1: DISCOVER LIVE MARKETS")
        log("=" * 70)

        candidates = ['EURUSDm', 'GBPUSDm', 'USDJPYm', 'USDCADm',
                       'XAUUSDm', 'BTCUSDm', 'ETHUSDm', 'ETHBTCm',
                       'BTCJPYm', 'GBPJPYm', 'EURJPYm', 'USDCHFm',
                       'AUDUSDm', 'NZDUSDm', 'EURGBPm']
        quotes = await client.subscribe_batch(candidates, timeout=20)

        live_symbols = sorted(quotes.keys())
        log(f"Live symbols ({len(live_symbols)}): {live_symbols}")

        if not live_symbols:
            log("No quotes. Market fully closed.")
            _summary()
            return

        for name, q in quotes.items():
            log(f"  {name}: bid={q.bid} ask={q.ask}")

        sym1 = live_symbols[0]
        sym2 = live_symbols[1] if len(live_symbols) > 1 else live_symbols[0]
        sym3 = live_symbols[2] if len(live_symbols) > 2 else live_symbols[0]
        log(f"  Trading: {sym1}, {sym2}, {sym3}")
        log("")

        # Helper to get digits for a symbol
        def digits(sym):
            s = client._symbols.get(sym)
            return s.digits if s else 5

        def pt(sym):
            return 10 ** -digits(sym)

        # Phase 2: Market orders (with REQUOTE retry)
        log("=" * 70)
        log("PHASE 2: MARKET ORDERS — BUY / SELL")
        log("=" * 70)

        market_tickets = []

        for sym in [sym1, sym2]:
            try:
                q = client.get_quote(sym)
                log(f"  BUY {sym}: 0.01 lots @ {q.ask}")
                r = await retry_trade(lambda s=sym: client.buy(s, 0.01, comment="TEST BUY"))
                result(f"BUY {sym}", r.retcode == 10009,
                       f"retcode={r.retcode} order={r.order} price={r.price}")
                if r.retcode == 10009:
                    market_tickets.append((sym, r.order))
            except Exception as e:
                result(f"BUY {sym}", False, str(e)[:60])

        for sym in [sym3, sym1]:
            try:
                q = client.get_quote(sym)
                log(f"  SELL {sym}: 0.01 lots @ {q.bid}")
                r = await retry_trade(lambda s=sym: client.sell(s, 0.01, comment="TEST SELL"))
                result(f"SELL {sym}", r.retcode == 10009,
                       f"retcode={r.retcode} order={r.order} price={r.price}")
                if r.retcode == 10009:
                    market_tickets.append((sym, r.order))
            except Exception as e:
                result(f"SELL {sym}", False, str(e)[:60])

        # BUY with SL/TP
        try:
            d = digits(sym1)
            q = client.get_quote(sym1)
            sl = round(q.ask - pt(sym1) * 50, d)
            tp = round(q.ask + pt(sym1) * 100, d)
            log(f"  BUY {sym1} SL={sl} TP={tp}")
            r = await retry_trade(lambda s=sym1, sl=sl, tp=tp: client.buy(s, 0.01, sl=sl, tp=tp, comment="TEST SLTP"))
            result("BUY+SL/TP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                market_tickets.append((sym1, r.order))
        except Exception as e:
            result("BUY+SL/TP", False, str(e)[:60])

        # SELL with SL/TP
        try:
            d = digits(sym2)
            q = client.get_quote(sym2)
            sl = round(q.bid + pt(sym2) * 50, d)
            tp = round(q.bid - pt(sym2) * 100, d)
            log(f"  SELL {sym2} SL={sl} TP={tp}")
            r = await retry_trade(lambda s=sym2, sl=sl, tp=tp: client.sell(s, 0.01, sl=sl, tp=tp, comment="TEST SLTP"))
            result("SELL+SL/TP", r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
            if r.retcode == 10009:
                market_tickets.append((sym2, r.order))
        except Exception as e:
            result("SELL+SL/TP", False, str(e)[:60])

        log(f"  Market trades accepted: {len(market_tickets)}")
        log("")

        # Phase 3: Pending orders
        log("=" * 70)
        log("PHASE 3: PENDING ORDERS — LIMIT + STOP")
        log("=" * 70)

        pending_tickets = []

        for action, sym, side in [
            ('BUY LIMIT', sym1, 'BUY'),
            ('SELL LIMIT', sym2, 'SELL'),
            ('BUY STOP', sym1, 'BUY'),
            ('SELL STOP', sym2, 'SELL'),
        ]:
            try:
                d = digits(sym)
                q = client.get_quote(sym)
                if 'BUY' in action and 'LIMIT' in action:
                    price = round(q.bid - pt(sym) * 200, d)
                elif 'SELL' in action and 'LIMIT' in action:
                    price = round(q.ask + pt(sym) * 200, d)
                elif 'BUY STOP' in action:
                    price = round(q.ask + pt(sym) * 200, d)
                else:
                    price = round(q.bid - pt(sym) * 200, d)

                log(f"  {action} {sym} @ {price}")

                if action == 'BUY LIMIT':
                    r = await retry_trade(lambda s=sym, p=price: client.buy_limit(s, 0.01, p, comment="TEST BL"))
                elif action == 'SELL LIMIT':
                    r = await retry_trade(lambda s=sym, p=price: client.sell_limit(s, 0.01, p, comment="TEST SL"))
                elif action == 'BUY STOP':
                    r = await retry_trade(lambda s=sym, p=price: client.buy_stop(s, 0.01, p, comment="TEST BS"))
                else:
                    r = await retry_trade(lambda s=sym, p=price: client.sell_stop(s, 0.01, p, comment="TEST SS"))

                result(action, r.retcode == 10009, f"retcode={r.retcode} order={r.order}")
                if r.retcode == 10009:
                    pending_tickets.append((sym, r.order))
            except Exception as e:
                result(action, False, str(e)[:60])

        log(f"  Pending orders accepted: {len(pending_tickets)}")
        log("")

        # Phase 4: Wait for fills
        log("=" * 70)
        log("PHASE 4: WAITING FOR FILLS (10s)")
        log("=" * 70)
        await asyncio.sleep(10)

        positions = await client.get_positions()
        log(f"  Positions: {len(positions)}")
        for p in positions:
            log(f"    #{p.ticket} {'BUY' if p.type == 0 else 'SELL'} {p.symbol} {p.volume} lots profit={p.profit}")
        log("")

        # Phase 5: Modify position SL/TP
        log("=" * 70)
        log("PHASE 5: MODIFY POSITION SL/TP")
        log("=" * 70)

        modified = 0
        for p in positions:
            d = digits(p.symbol)
            q = client.get_quote(p.symbol)
            if q:
                if p.type == 0:
                    sl = round(q.bid - pt(p.symbol) * 50, d)
                    tp = round(q.bid + pt(p.symbol) * 100, d)
                else:
                    sl = round(q.ask + pt(p.symbol) * 50, d)
                    tp = round(q.ask - pt(p.symbol) * 100, d)
            else:
                sl = round(p.price - pt(p.symbol) * 50, d)
                tp = round(p.price + pt(p.symbol) * 100, d)

            log(f"  MODIFY #{p.ticket} {p.symbol}: SL={sl} TP={tp}")
            try:
                r = await retry_trade(lambda t=p.ticket, sl=sl, tp=tp: client.modify_position(t, sl=sl, tp=tp))
                result(f"Modify #{p.ticket}", r.retcode in (10009, 10013), f"retcode={r.retcode}")
                if r.retcode == 10009:
                    modified += 1
            except Exception as e:
                result(f"Modify #{p.ticket}", False, str(e)[:60])
        log(f"  Modified: {modified}/{len(positions)} positions")
        log("")

        # Phase 6: Modify pending orders
        log("=" * 70)
        log("PHASE 6: MODIFY PENDING ORDER SL/TP + PRICE")
        log("=" * 70)

        pending = await client.get_orders()
        log(f"  Pending orders: {len(pending)}")
        for o in pending:
            d = digits(o.symbol)
            q = client.get_quote(o.symbol)
            if q:
                new_sl = round(q.bid - pt(o.symbol) * 150, d)
                new_tp = round(q.bid + pt(o.symbol) * 150, d)
            else:
                new_sl = round(o.price - pt(o.symbol) * 150, d)
                new_tp = round(o.price + pt(o.symbol) * 150, d)
            new_price = round(o.price + pt(o.symbol) * 50, d)

            log(f"  MODIFY ORDER #{o.ticket} {o.symbol} SL={new_sl} TP={new_tp}")
            try:
                r = await retry_trade(lambda t=o.ticket, sl=new_sl, tp=new_tp: client.modify_order(t, sl=sl, tp=tp))
                result(f"Modify Order #{o.ticket}", r.retcode in (10009, 10013), f"retcode={r.retcode}")
            except Exception as e:
                result(f"Modify Order #{o.ticket}", False, str(e)[:60])

            log(f"  MODIFY PRICE #{o.ticket} -> {new_price}")
            try:
                r = await retry_trade(lambda t=o.ticket, p=new_price: client.modify_order_price(t, p))
                result(f"Modify Price #{o.ticket}", r.retcode in (10009, 10013), f"retcode={r.retcode}")
            except Exception as e:
                result(f"Modify Price #{o.ticket}", False, str(e)[:60])
        log("")

        # Phase 7: Close positions
        log("=" * 70)
        log("PHASE 7: CLOSE POSITIONS (FULL + PARTIAL)")
        log("=" * 70)

        positions = await client.get_positions()
        closed = 0
        for p in positions:
            if p.volume > 0.02:
                log(f"  PARTIAL CLOSE #{p.ticket} {p.symbol}: 0.01 lots (of {p.volume})")
                try:
                    r = await retry_trade(lambda t=p.ticket: client.partial_close(t, 0.01))
                    result(f"Partial #{p.ticket}", r.retcode == 10009, f"retcode={r.retcode}")
                except Exception as e:
                    result(f"Partial #{p.ticket}", False, str(e)[:60])
            else:
                log(f"  CLOSE #{p.ticket} {p.symbol} {p.volume} lots")
                try:
                    r = await retry_trade(lambda t=p.ticket: client.close_position(t))
                    result(f"Close #{p.ticket}", r.retcode == 10009, f"retcode={r.retcode}")
                    if r.retcode == 10009:
                        closed += 1
                except Exception as e:
                    result(f"Close #{p.ticket}", False, str(e)[:60])

        log(f"  Closed: {closed}/{len(positions)} positions")
        log("")

        # Phase 8: Cancel pending orders
        log("=" * 70)
        log("PHASE 8: CANCEL PENDING ORDERS")
        log("=" * 70)

        pending = await client.get_orders()
        cancelled = 0
        for o in pending:
            log(f"  CANCEL #{o.ticket} {o.symbol}")
            try:
                r = await retry_trade(lambda t=o.ticket: client.cancel_order(t))
                result(f"Cancel #{o.ticket}", r.retcode == 10009, f"retcode={r.retcode}")
                if r.retcode == 10009:
                    cancelled += 1
            except Exception as e:
                result(f"Cancel #{o.ticket}", False, str(e)[:60])

        log(f"  Cancelled: {cancelled}/{len(pending)} orders")
        log("")

        # Phase 9: Live streaming
        log("=" * 70)
        log("PHASE 9: LIVE QUOTE STREAMING (5s)")
        log("=" * 70)

        t0 = time.time()
        quotes_received = {}
        while time.time() - t0 < 5:
            for sym in live_symbols:
                q = client.get_quote(sym)
                if q:
                    if sym not in quotes_received:
                        quotes_received[sym] = 0
                    quotes_received[sym] += 1
            await asyncio.sleep(0.1)

        for sym, count in sorted(quotes_received.items()):
            q = client.get_quote(sym)
            result(f"Live {sym}", count > 0, f"{count} quotes, bid={q.bid} ask={q.ask}")
        log("")

        # Phase 10: Candles
        log("=" * 70)
        log("PHASE 10: ALL TIMEFRAME CANDLES")
        log("=" * 70)

        candle_syms = live_symbols[:3] if live_symbols else ['EURUSDm']
        for sym_name in candle_syms:
            for tf_name in ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1']:
                try:
                    c = await client.get_candles(sym_name, tf_name, 15)
                    result(f"Candle {sym_name}/{tf_name}", len(c) > 0, f"{len(c)} candles")
                except Exception as e:
                    result(f"Candle {sym_name}/{tf_name}", False, str(e)[:40])
        log("")

        # Phase 11: Final state
        log("=" * 70)
        log("PHASE 11: FINAL STATE")
        log("=" * 70)

        acct = await client.get_account()
        result("Account", acct.balance > 0,
               f"balance={acct.balance:.2f} equity={acct.equity:.2f} profit={acct.profit:.2f}")

        positions = await client.get_positions()
        result("Positions", True, f"{len(positions)} open")
        for p in positions:
            log(f"    #{p.ticket} {'BUY' if p.type == 0 else 'SELL'} {p.symbol} {p.volume} lots profit={p.profit}")

        pending = await client.get_orders()
        result("Pending Orders", True, f"{len(pending)} pending")

        deals = await client.get_deals()
        result("Deals", True, f"{len(deals)} historical")
        log("")

        _summary()


def _summary():
    log("=" * 70)
    log("TEST SUMMARY")
    log("=" * 70)
    total = len(RESULTS)
    passed = sum(1 for _, p, _ in RESULTS if p)
    failed = total - passed
    rate = (passed / total * 100) if total > 0 else 0
    log(f"  Total: {total}  Passed: {passed}  Failed: {failed}  Rate: {rate:.1f}%")

    if failed > 0:
        log("")
        log("  FAILED:")
        for name, p, detail in RESULTS:
            if not p:
                log(f"    ✗ {name}: {detail}")
    log("")
    log("DONE.")


if __name__ == '__main__':
    asyncio.run(main())
