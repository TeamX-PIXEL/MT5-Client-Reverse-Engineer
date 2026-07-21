## Goal
Build a pure Python MT5 broker client for Exness by reverse-engineering the MT5 Web Terminal WebSocket protocol.

## Status: ALL CORE FEATURES WORKING ✅

## What Works (Tested & Verified)
| Feature | Cmd ID | Status | Notes |
|---------|--------|--------|-------|
| Auth handshake | 0 | ✅ | Static key → session key |
| Login | 28 | ✅ | 912B payload, retcode=0 |
| Heartbeat | 51 | ✅ | Empty body, server echoes |
| Account data | 3 | ✅ | 6496B, parses balance/equity/etc |
| Symbols list | 34 | ✅ | 355 symbols, gzip compressed |
| Subscribe quotes | 7 | ✅ | Array of uint32 symbol IDs |
| Live quotes | 8 | ✅ | 50B per quote, raw prices ÷ 10^digits |
| Open positions | 4 | ✅ | [pos_count, order_count] format |
| Deal history | 5 | ✅ | from/to uint32, returns deal array |
| Trade orders | 12 | ✅ | 248B serialized, retcode=0 |
| Trade events | 19 | ✅ | 380B pushed after trade |
| Broker Search | — | ✅ | SearchMQ (POST+HMAC) + Search (GET) — server name → IP |
| Server List | — | ✅ | servers.dat binary format — local fallback for IP resolution |

## Trade Serialization Fix (from other terminal)
The key fix was: **trade_action, type_filling, type_flags** values.

### Op Schema (22 fields, 248 bytes)
```
[0]  action_id        (u32)    = 0
[1]  trade_action     (u32)    = 3 for market, 2=instant, 1=request, 4=exchange
[2]  symbol           (UTF-16LE 64B)
[3]  volume           (u64)    = lots × 100000
[4]  digits           (u32)
[5]  trade_order      (u64)    = 0 for new
[6]  trade_type       (u32)    = 0=buy, 1=sell
[7]  type_filling     (u32)    = 0 for market (FOK)
[8]  type_time        (u32)    = 0 (GTC)
[9]  type_flags       (u32)    = 2 for new orders
[10] type_reason      (u32)    = 0
[11] price_order      (f64)    = ask for buy, bid for sell
[12] price_trigger    (f64)    = 0
[13] price_sl         (f64)    = stop loss price
[14] price_tp         (f64)    = take profit price
[15] price_deviation  (u32)    = 0
[16] price_top        (f64)    = 0
[17] price_bottom     (f64)    = 0
[18] comment          (UTF-16LE 64B)
[19] trade_position   (u64)    = 0 for new
[20] position_by      (u64)    = 0
[21] time_expiration  (u32)    = 0
```

### Critical Values
- **trade_action**: 
  - 3 = MARKET (for market open AND close with opposite type)
  - 5 = PENDING (for limit/stop/stop-limit orders) — VERIFIED
  - 6 = MODIFY_DEAL (for position SL/TP modify) — VERIFIED, use order_ticket in trade_position
  - 7 = MODIFY_ORDER (for pending order SL/TP modify) — VERIFIED, use order_ticket in trade_order
  - 8 = CANCEL (for canceling pending orders) — VERIFIED
  - **DO NOT USE**: 1 (returns 10013), 10 (returns 10030), 201 (returns 10030)
- **trade_type**:
  - 0=BUY, 1=SELL (market)
  - 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP (pending)
  - 6=BUY_STOP_LIMIT, 7=SELL_STOP_LIMIT (stop-limit) — VERIFIED
- **CRITICAL: Position ID = ORDER ticket** — The `order` field from TRADE_EVENT is the position ID for modify AND close. NOT the `deal` field!
- **type_filling**: 0=FOK for market, 2=RETURN for pending
- **type_flags**: 2 for new orders
- **action_id**: Always 0 (default)
- **volume**: lots × 100000000 (0.01 lots = 1000000)
- **Pending price distance**: Must be 100+ _Point from current price

## Open Positions (cmd_id=4)
Response format: `[pos_count:uint32, positions × POS_SIZE, order_count:uint32, orders × ORDER_SIZE]`
- **Note**: First 8 bytes = two uint32 counts (positions, then orders)
- If both are 0, body = 8 bytes total

## Deal History (cmd_id=5)
Payload: `[from:uint32, to:uint32]` (unix timestamps, 0=all)
Response: `[deal_count:uint32, deals × DEAL_SIZE]`
- Deal schema (xd): 28 fields including deal_id, symbol, action, volume, price, profit, etc.

## Protocol Commands (Complete)
| Cmd | Name | Dir | Payload | Response |
|-----|------|-----|---------|----------|
| 0 | AUTH | C→S | bytes(64) | session_key |
| 2 | LOGOUT | C→S | — | — |
| 3 | GET_ACCOUNT | C→S | — | 6496B account data |
| 4 | GET_POSITIONS | C→S | — | [pos_count, positions, order_count, orders] |
| 5 | GET_DEALS | C→S | [from:u32, to:u32] | [deal_count, deals] |
| 6 | GET_SYMBOLS_FULL | C→S | — | full symbol config |
| 7 | SUBSCRIBE | C→S | [count:u32, ids:u32[]] | — |
| 8 | QUOTES | S→C | — | 50B per quote |
| 9 | GET_CATEGORIES | C→S | — | categories |
| 11 | GET_RATES | C→S | [symbol:64B, timeframe:u16, from:i32, to:i32] | raw 48B candles |
| 12 | TRADE | C→S | 248B Op schema | [retcode:u32] |
| 14 | ACCT_UPDATE | S→C | — | account update |
| 15 | SYSTEM | S→C | — | [code:u32] |
| 17 | SYMBOL_SPEC | S→C | — | symbol spec |
| 19 | TRADE_EVENT | S→C | — | 380B deal info |
| 20 | SPREADS | C→S | [count:u32, ids:u32[]] | spreads |
| 22 | POS_UPDATE | S→C | — | position updates |
| 28 | LOGIN | C→S | 912B payload | [account_data:160B, account_id:u64] |
| 34 | GET_SYMBOLS_GZ | C→S | — | gzipped symbols |
| 42 | NOTIFY | C→S | [code:u32] | — |
| 51 | HEARTBEAT | C↔S | — | — |

## Account Schema (fl) — cmd_id=3
```
[0]  flags (u8)    [1]  login_id (i32)   [2]  permissions (i32)
[3]  balance (f64) [4]  equity (f64)     [5]  currency (UTF-16LE 64B)
[6]  field6 (u32)  [7]  field7 (u32)     [8]  group (UTF-16LE 256B)
[9]  leverage(u16) [10] server (UTF-16LE 128B) [11] name (UTF-16LE 256B)
[12] trade_mode(i32) [13] flag (i8)      [14] credit (u32)
[15] bonus (u32)   [16] profit (f64)     [17] margin (f64)
[18] field18(u32)  [19] margin_f3(f64)   [20] stop_out (u32)
[21-23] margin floats (f64) [24] pw_min(u32) [25] pw_flags(u32)
FL_SIZE = 816 bytes
```

## Symbol Schema (Mh) — cmd_id=34
```
[0] name (UTF-16LE 64B)     [1] description (UTF-16LE 128B)
[2] digits (u32)             [3] symbol_id (u32) ← USED FOR SUBSCRIPTION
[4] path (UTF-16LE 256B)    [5] trade_calc_mode (u32)
[6] basis (UTF-16LE 64B)    [7] sector (u16)
MH_SIZE = 526 bytes
```

## Quote Schema (Uh) — cmd_id=8
```
[0] symbol_id (u32) [1] tick_time (i32)  [2] fields (u32)
[3] bid (f64 RAW)   [4] ask (f64 RAW)    [5] last (f64 RAW)
[6] tick_volume(i64) [7] time_ms_delta(u32) [8] flags (u16)
QUOTE_SIZE = 50 bytes
Actual price = raw / 10^digits
```

## Candle Schema — cmd_id=11 — VERIFIED
```
[0] timestamp (i32)  [1] open (f64)   [2] high (f64)
[3] low (f64)        [4] close (f64)  [5] tick_volume (i64)
[6] spread (i32)
CANDLE_SIZE = 48 bytes (no count prefix, raw stream)
```

Timeframe constants: M1=1, M5=5, M15=15, M30=30, H1=16385, H4=16388, D1=16408, W1=32769, MN1=49153

## Position Schema (uu) — cmd_id=4
```
[0] position_id(i64)  [1] trade_order(i64) [2] time_create(u32) [3] time_update(u32)
[4] symbol(UTF-16LE 64B) [5] action(u32)  [6] price_open(f64) [7] price_close(f64)
[8] sl(f64) [9] tp(f64) [10] volume(u64)  [11] profit(f64)
[12] rate_profit(f64) [13] rate_margin(f64) [14] commission(f64)
[15] storage(f64) [16] expert(i64) [17] expert_pos_id(i64)
[18] comment(UTF-16LE 64B) [19] contract_size(f64)
[20] digits(u32) [21] digits_currency(u32) [22] magic(u32)
[23] reason(UTF-16LE 64B) [24] time_create_ms(i32) [25] time_update_ms(i32)
POS_SIZE = 344 bytes
```

## Serialization Types (Ac.parse)
```
1=int8, 2=int16, 3=int32, 4=uint8, 5=uint16, 6=uint32(4B!), 7=float32,
8=float64, 11=UTF-16LE(propLength B), 12=raw(propLength B), 17=int64, 18=uint64
```

## Known Account
- Login: 463558919, Password: Trade@123, Server: Exness-MT5Trial17
- Balance: 855.39 USD, Leverage: 1/5830, Group: Standard

## Remaining Tasks
1. ✅ Deal schema parsing — DONE
2. ✅ Decode cmd_id=19 (trade event) 380B response — DONE
3. ✅ Position close/modify — VERIFIED (trade_action=3 for close, 6 for SL/TP modify)
4. ✅ Pending orders — VERIFIED (trade_action=5 for open, 8 for cancel)
5. ✅ Pending order SL/TP modify — VERIFIED (trade_action=7, trade_order=ticket at offset 84)
6. ✅ Stop-limit orders — VERIFIED (trade_type=6/7, place/modify/cancel all working)
7. ✅ Multi-symbol trading — VERIFIED (BTC, XAU, ETH, GBP, JPY, EUR all working)
8. ✅ Historical candles — VERIFIED (cmd_id=11, 48-byte format, all 9 timeframes M1-MN1, datetime range selection)
9. ✅ Partial close — VERIFIED (same as full close, just smaller volume, use ORDER ticket)
10. ✅ Tick Size/Value/Contract Size — VERIFIED (in cmd_id=18 SymbolInfo offsets 1376,1384,1392)
11. ✅ Session queries — VERIFIED (IsQuoteSession/IsTradeSession from cmd_id=18 sessions)
12. ✅ Modify Pending Price — VERIFIED (trade_action=7, price_order at offset 112)
13. ✅ Timeframe Conversion — DOCUMENTED (client-side bar aggregation algorithm)
14. ✅ Profit/Margin Calculations — DOCUMENTED (formulas in ADDITIONAL_FEATURES.md)
15. ✅ Server List Parsing — DOCUMENTED (servers.dat binary format in ADDITIONAL_FEATURES.md)
16. ✅ Broker Search API — VERIFIED (SearchMQ + Search endpoints decrypted from mt5api.dll via .NET reflection)
17. Build clean client library — package all into reusable Python API

## Relevant Files
- `ws_client.py` — Working pure Python client
- `broker_search.py` — MT5 Broker Search API client (SearchMQ + Search)
- `WEBSOCKET_PROTOCOL.md` — Full protocol docs
- `ADDITIONAL_FEATURES.md` — Partial close, modify price, timeframe conversion, profit calc, sessions, server list, broker search API
- `PLAN.md` — This file
- `FUNCTION_COVERAGE.md` — C# vs OwnMt5API comparison (133 functions)
- `broker_connect.py` — TCP binary protocol client
- `webterminal_client.py` — Playwright-based client
- `ws_test_partial_close.py` — Partial close test (VERIFIED WORKING)
- `/tmp/BMwCVz0.js` — Core protocol JS (296KB)
- `/tmp/Bj2PVztK.js` — Crypto module JS (169KB)
- `/tmp/BUOVBlM4.js` — UI init JS (56KB)
