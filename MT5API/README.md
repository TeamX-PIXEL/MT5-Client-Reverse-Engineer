# MT5API — Pure Python Async Client for MetaTrader 5

A lightweight, fully async Python client for the MetaTrader 5 WebSocket protocol. No browser, no Playwright, no DLL — pure Python.

## Features

- **Full MT5 WebSocket protocol** — Auth, Login, Account, Symbols, Quotes, Trading, Positions, Orders, Deals, Candles
- **All order types** — Market, Limit, Stop, Stop-Limit
- **Position management** — Close, Partial Close, Modify SL/TP
- **Pending order management** — Cancel, Modify SL/TP, Modify Price
- **Live quotes** — Subscribe to real-time bid/ask streaming
- **Candle data** — All 9 timeframes (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
- **Deal history** — Full trade history with timestamps
- **Broker search** — Auto-resolve server names to IP addresses
- **Async context manager** — Clean resource management
- **Callbacks** — Register handlers for live quotes and trade events

## Installation

```bash
pip install websockets pycryptodome requests
```

Copy the `MT5API/` folder to your project:

```
your_project/
├── MT5API/
│   ├── __init__.py
│   └── mt5client/
│       ├── __init__.py
│       ├── client.py
│       ├── connection.py
│       ├── crypto.py
│       ├── exceptions.py
│       ├── models.py
│       ├── protocol.py
│       └── search.py
└── your_script.py
```

## Quick Start

```python
import asyncio
from MT5API import MT5Client

async def main():
    async with MT5Client(
        login=463558919,
        password="Trade@123",
        server="Exness-MT5Trial17"
    ) as client:
        # Account info
        account = await client.get_account()
        print(f"Balance: {account.balance:.2f} {account.currency}")

        # Subscribe to live quotes
        quote = await client.subscribe("EURUSDm")
        print(f"EURUSDm: bid={quote.bid:.5f} ask={quote.ask:.5f}")

        # Place a BUY order
        result = await client.buy("EURUSDm", 0.01, sl=1.08, tp=1.10)
        print(f"Order placed: deal={result.deal} order={result.order}")

        # Close the position
        await client.close_position(result.order)

asyncio.run(main())
```

## API Reference

### MT5Client

The main client class. Use as an async context manager.

```python
async with MT5Client(login, password, server, build=5830) as client:
    ...
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `login` | `int` | Your MT5 account number |
| `password` | `str` | Your MT5 password |
| `server` | `str` | Server name (e.g. `Exness-MT5Trial17`) |
| `build` | `int` | MT5 build number (default: `5830`) |

The client automatically:
1. Resolves the server name to an IP address via MetaQuotes search API
2. Establishes a WebSocket connection
3. Performs the auth handshake
4. Logs in
5. Loads your account info and all available symbols

---

### Account

```python
account = await client.get_account()
```

**Returns `Account`:**
| Field | Type | Description |
|-------|------|-------------|
| `login` | `int` | Account number |
| `balance` | `float` | Account balance |
| `equity` | `float` | Current equity |
| `margin` | `float` | Used margin |
| `free_margin` | `float` | Free margin |
| `currency` | `str` | Account currency (e.g. `USD`) |
| `group` | `str` | Account group (e.g. `Standard`) |
| `leverage` | `int` | Leverage (e.g. `5830` means 1:5830) |
| `server` | `str` | Server name |
| `profit` | `float` | Floating P/L |

---

### Symbols

```python
symbols = await client.get_symbols()  # dict[str, Symbol]
```

**Returns `dict[str, Symbol]`:** Keyed by symbol name.

**Symbol fields:**
| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Symbol name (e.g. `EURUSDm`) |
| `id` | `int` | Internal symbol ID (used for subscriptions) |
| `digits` | `int` | Price decimal places |
| `point` | `float` | Minimum price change |
| `spread` | `int` | Current spread |
| `trade_mode` | `int` | 0=disabled, 4=full trading |
| `contract_size` | `float` | Contract size (default 100000) |
| `tick_value` | `float` | Tick value |
| `tick_size` | `float` | Tick size |
| `calc_mode` | `int` | Calculation mode |

---

### Subscribe to Quotes

There are three ways to subscribe to real-time quotes:

#### 1. Single symbol (blocks until first quote)

```python
quote = await client.subscribe("EURUSDm")
print(f"bid={quote.bid:.5f} ask={quote.ask:.5f}")
```

#### 2. Batch subscribe (multiple symbols in ONE command)

```python
quotes = await client.subscribe_batch(["EURUSDm", "GBPUSDm", "XAUUSDm"])
for sym, q in quotes.items():
    print(f"{sym}: bid={q.bid:.5f} ask={q.ask:.5f}")
```

#### 3. Fire-and-forget subscribe (non-blocking)

```python
await client.send_subscribe(["EURUSDm", "GBPUSDm", "XAUUSDm"])
await asyncio.sleep(5)  # Wait for quotes to arrive
quote = client.get_quote("EURUSDm")  # Sync lookup
```

**Methods:**
| Method | Async | Description |
|--------|-------|-------------|
| `subscribe(symbol, timeout=10)` | Yes | Subscribe to one symbol, blocks until first quote |
| `subscribe_batch(symbols, timeout=15)` | Yes | Subscribe to multiple symbols, returns dict of received quotes |
| `send_subscribe(symbols)` | Yes | Low-level: sends subscribe command without waiting |
| `get_quote(symbol)` | No | Get cached quote (returns `None` if not subscribed) |

**Returns `Quote`:**
| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `str` | Symbol name |
| `symbol_id` | `int` | Internal ID |
| `bid` | `float` | Current bid price |
| `ask` | `float` | Current ask price |
| `raw_bid` | `int` | Raw bid (before digit conversion) |
| `raw_ask` | `int` | Raw ask (before digit conversion) |
| `time` | `datetime` | Quote timestamp (UTC) |

---

### Trading — Market Orders

```python
# BUY
result = await client.buy("EURUSDm", 0.01, sl=1.08, tp=1.10, comment="my order")

# SELL
result = await client.sell("EURUSDm", 0.01)

# BUY with magic number
result = await client.buy("EURUSDm", 0.01, magic=77777, comment="ea_trade")
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | `str` | required | Symbol name |
| `volume` | `float` | required | Lot size (e.g. `0.01`) |
| `sl` | `float` | `0` | Stop-loss price (0 = none) |
| `tp` | `float` | `0` | Take-profit price (0 = none) |
| `comment` | `str` | `""` | Order comment |
| `magic` | `int` | `0` | Magic number (embedded in comment as `#<magic> <comment>`) |

**Returns `TradeResult`:**
| Field | Type | Description |
|-------|------|-------------|
| `retcode` | `int` | `10009` = accepted, others = error |
| `deal` | `int` | Deal ticket |
| `order` | `int` | **Order ticket (= position ID for close/modify!)** |
| `volume` | `float` | Executed volume (raw, divide by 100000000 for lots) |
| `price` | `float` | Execution price |
| `comment` | `str` | Server comment |
| `position` | `int` | Position ticket |

---

### Trading — Pending Orders

```python
# BUY LIMIT — triggers when price drops to entry
result = await client.buy_limit("EURUSDm", 0.01, price=1.09, sl=1.08, tp=1.10)

# SELL LIMIT — triggers when price rises to entry
result = await client.sell_limit("EURUSDm", 0.01, price=1.15)

# BUY STOP — triggers when price rises to entry
result = await client.buy_stop("EURUSDm", 0.01, price=1.16)

# SELL STOP — triggers when price drops to entry
result = await client.sell_stop("EURUSDm", 0.01, price=1.07)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | `str` | required | Symbol name |
| `volume` | `float` | required | Lot size |
| `price` | `float` | required | Pending order trigger price |
| `sl` | `float` | `0` | Stop-loss (0 = none) |
| `tp` | `float` | `0` | Take-profit (0 = none) |
| `comment` | `str` | `""` | Order comment |
| `magic` | `int` | `0` | Magic number (embedded in comment) |

**Notes:**
- Pending price must be at least 100 × point away from current market price
- Uses `type_filling=2` (RETURN), not FOK/IOC
- Returns same `TradeResult` as market orders

---

### Magic Number

The MT5 WebSocket API does **not** support magic numbers in the wire protocol. This library works around it by embedding the magic in the `comment` field:

```
Format: #<magic> <comment>
Example: #77777 ea_trade
```

**Setting magic when opening a trade:**
```python
result = await client.buy("EURUSDm", 0.01, magic=77777, comment="ea_trade")
# Server stores comment as: "#77777 ea_trade"
```

**Reading magic from positions:**
```python
positions = await client.get_positions()
for pos in positions:
    print(f"ticket={pos.ticket} magic={pos.magic} comment={pos.comment}")
    # ticket=2278740652 magic=77777 comment='ea_trade'
```

**Reading magic from orders:**
```python
orders = await client.get_orders()
for order in orders:
    print(f"ticket={order.ticket} magic={order.magic} comment={order.comment}")
```

**Reading magic from deals:**
```python
deals = await client.get_deals()
for deal in deals:
    print(f"ticket={deal.ticket} magic={deal.magic} comment={deal.comment}")
```

**How it works:**
| Step | What happens |
|------|-------------|
| **Set** | `format_magic_comment(77777, "ea_trade")` → `"#77777 ea_trade"` |
| **Send** | Embedded in the 64-byte comment field of the trade request |
| **Parse** | `parse_magic_comment("#77777 ea_trade")` → `(77777, "ea_trade")` |
| **Read** | Magic and comment are separate fields on Position/Order/Deal models |

**Important:** This is a client-side workaround. The magic is stored in the comment field, not the native MT5 magic field. It cannot be used to filter trades in the MT5 terminal or by EAs.

---

### Close Position

```python
result = await client.close_position(ticket)
# Or with bypass (skip stale position list lookup):
result = await client.close_position(ticket, symbol="EURUSDm", pos_type=0, volume=0.01)
```

Closes an open position by its **order ticket** (the `order` field from `TradeResult`, NOT the `deal` field).

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ticket` | `int` | required | Order ticket (from `TradeResult.order`) |
| `symbol` | `str` | `None` | Symbol name (bypass: skip position list lookup) |
| `pos_type` | `int` | `None` | `0`=BUY, `1`=SELL (bypass) |
| `volume` | `float` | `None` | Position volume in lots (bypass) |

**Note:** The bypass params (`symbol`, `pos_type`, `volume`) are useful when the cmd4 position list is stale (e.g. in multi-account scenarios or high-frequency trading). Pass the values from the original trade result to avoid stale data issues.

---

### Partial Close

```python
result = await client.partial_close(ticket, volume=0.05)
# Or with bypass:
result = await client.partial_close(ticket, volume=0.05, symbol="EURUSDm", pos_type=0, pos_volume=0.1)
```

Closes part of an open position.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ticket` | `int` | required | Order ticket |
| `volume` | `float` | required | Volume to close in lots |
| `symbol` | `str` | `None` | Symbol name (bypass) |
| `pos_type` | `int` | `None` | `0`=BUY, `1`=SELL (bypass) |
| `pos_volume` | `float` | `None` | Total position volume (bypass) |

---

### Modify Position SL/TP

```python
result = await client.modify_position(ticket, sl=1.09, tp=1.11)
# Or with bypass:
result = await client.modify_position(ticket, sl=1.09, tp=1.11, symbol="EURUSDm", pos_type=0, volume=0.01)
```

Modifies stop-loss and/or take-profit on an open position.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ticket` | `int` | required | Order ticket |
| `sl` | `float` | `None` | New stop-loss (None = keep current) |
| `tp` | `float` | `None` | New take-profit (None = keep current) |
| `symbol` | `str` | `None` | Symbol name (bypass) |
| `pos_type` | `int` | `None` | `0`=BUY, `1`=SELL (bypass) |
| `volume` | `float` | `None` | Position volume in lots (bypass) |

---

### Cancel Pending Order

```python
result = await client.cancel_order(ticket)
# Or with bypass:
result = await client.cancel_order(ticket, symbol="EURUSDm", order_type=2, volume=0.01, price=1.08)
```

Cancels a pending order.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ticket` | `int` | required | Order ticket |
| `symbol` | `str` | `None` | Symbol name (bypass) |
| `order_type` | `int` | `None` | Order type: 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP (bypass) |
| `volume` | `float` | `None` | Volume in lots (bypass) |
| `price` | `float` | `0` | Order price (bypass) |

---

### Modify Pending Order

```python
# Modify SL/TP
result = await client.modify_order(ticket, sl=1.08, tp=1.12)

# Modify price
result = await client.modify_order(ticket, price=1.10)
```

Modifies a pending order's price, SL, and/or TP.

---

### Get Positions

```python
positions = await client.get_positions()  # list[Position]
```

**Returns `list[Position]`:**
| Field | Type | Description |
|-------|------|-------------|
| `ticket` | `int` | Order ticket (use this for close/modify!) |
| `symbol` | `str` | Symbol name |
| `type` | `int` | `0` = BUY, `1` = SELL |
| `volume` | `float` | Volume in lots |
| `price` | `float` | Open price |
| `sl` | `float` | Stop-loss |
| `tp` | `float` | Take-profit |
| `profit` | `float` | Floating P/L |
| `swap` | `float` | Swap |
| `commission` | `float` | Commission |
| `comment` | `str` | Comment |
| `time` | `datetime` | Open time (UTC) |

---

### Get Pending Orders

```python
orders = await client.get_orders()  # list[Order]
```

**Returns `list[Order]`:**
| Field | Type | Description |
|-------|------|-------------|
| `ticket` | `int` | Order ticket |
| `symbol` | `str` | Symbol name |
| `type` | `int` | Order type |
| `volume` | `float` | Volume |
| `price` | `float` | Order price |
| `sl` | `float` | Stop-loss |
| `tp` | `float` | Take-profit |
| `comment` | `str` | Comment |
| `time` | `datetime` | Creation time |

---

### Get Deal History

```python
deals = await client.get_deals()  # list[Deal]

# With time range
import time
deals = await client.get_deals(
    from_time=int(time.time()) - 86400,  # last 24h
    to_time=int(time.time())
)
```

**Returns `list[Deal]`:**
| Field | Type | Description |
|-------|------|-------------|
| `ticket` | `int` | Deal ticket |
| `order` | `int` | Order ticket |
| `symbol` | `str` | Symbol name |
| `type` | `int` | `0` = BUY, `1` = SELL |
| `volume` | `float` | Volume |
| `price` | `float` | Execution price |
| `profit` | `float` | Profit |
| `commission` | `float` | Commission |
| `swap` | `float` | Swap |
| `comment` | `str` | Comment |
| `time` | `datetime` | Deal time (UTC) |

---

### Get Candles

```python
candles = await client.get_candles("EURUSDm", "M1", 100)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | `str` | required | Symbol name |
| `timeframe` | `str` | required | `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, `MN1` |
| `count` | `int` | `100` | Number of candles |

**Returns `list[Candle]`:**
| Field | Type | Description |
|-------|------|-------------|
| `time` | `datetime` | Candle time (UTC) |
| `timestamp` | `int` | Unix timestamp |
| `open` | `float` | Open price |
| `high` | `float` | High price |
| `low` | `float` | Low price |
| `close` | `float` | Close price |
| `tick_volume` | `int` | Tick volume |
| `spread` | `int` | Spread |

---

### Callbacks

Register handlers for real-time events:

```python
def on_quote(quote):
    print(f"{quote.symbol}: bid={quote.bid:.5f} ask={quote.ask:.5f}")

def on_trade(event):
    print(f"Trade: retcode={event['retcode']} deal={event['deal']}")

client.on_quote(on_quote)
client.on_trade(on_trade)
```

---

## Exceptions

```python
from MT5API import MT5Error, AuthError, TradeError, ServerNotFoundError
```

| Exception | Description |
|-----------|-------------|
| `MT5Error` | Base exception |
| `AuthError` | Authentication failed |
| `TradeError` | Trade operation failed (has `.retcode` attribute) |
| `ServerNotFoundError` | Server name could not be resolved |

**Error handling example:**
```python
try:
    result = await client.buy("EURUSDm", 0.01)
    if result.retcode != 10009:
        print(f"Trade rejected: {result.comment}")
except TradeError as e:
    print(f"Error {e.retcode}: {e}")
except ServerNotFoundError:
    print("Server not found")
```

---

## Constants

```python
from MT5API.protocol import (
    TIMEFRAMES,           # {'M1': 1, 'M5': 5, ..., 'MN1': 49153}
    TRADE_MARKET,         # 3
    TRADE_MODIFY,         # 6
    TRADE_MODIFY_ORDER,   # 7
    TRADE_CANCEL,         # 8
    TYPE_BUY,             # 0
    TYPE_SELL,            # 1
    FILL_FOK,             # 0
    FILL_IOC,             # 1
    FILL_RETURN,          # 2
    LOT_MULTIPLIER,       # 100_000_000
)
```

---

## Broker Search (Standalone)

```python
from MT5API import search, search_mq, find_server, find_server_ips

# Search all Exness servers
results = search("Exness")

# Find specific server
info = find_server("Exness-MT5Trial17")
print(info["access"])  # ['13.213.81.113:443', ...]

# Get just IP list
ips = find_server_ips("Exness-MT5Trial17")
```

---

## Architecture

```
MT5Client
├── Connection          WebSocket layer (send lock, encrypt/decrypt)
├── Protocol            Wire format, schemas, Op builder
├── Crypto              AES-256-CBC, XOR chain cipher
├── Search              Server name → IP resolution
├── Models              Dataclasses for all response types
└── Exceptions          Error hierarchy
```

**Connection flow:**
1. `find_server_ips(server)` → resolve name to IP via MetaQuotes API
2. `WebSocket.connect()` → TLS connection to `wss://ip:443/terminal`
3. Auth handshake (cmd 0) → static key → session key
4. Login (cmd 28) → encrypted credentials
5. Background receive loop → dispatches quotes, trade events, matches pending requests

**Trade flow:**
1. Build 248-byte Op struct (symbol, volume, price, SL, TP, type, etc.)
2. Wrap in 380-byte Pp: `[action_id:4][Op:248][Ap:128]`
3. Encrypt with session key → send as cmd 12
4. Wait for TRADE_EVENT (cmd 19) → parse Ap at offset 252

---

## Critical Notes

1. **Position ID = ORDER ticket** — Use `TradeResult.order` for close/modify, NOT `TradeResult.deal`
2. **Volume encoding** — Internal volume = lots × 100,000,000 (e.g. 0.01 lots = 1,000,000)
3. **Subscribe before trading** — You need a live quote to place market orders
4. **Market hours** — Quotes only flow during market hours; subscribe may timeout outside sessions
5. **retcode 10009** = accepted, **10030** = position not found, **10036** = position doesn't exist

---

## Example — Full Trading Bot

```python
import asyncio
from MT5API import MT5Client

async def main():
    async with MT5Client(
        login=463558919,
        password="Trade@123",
        server="Exness-MT5Trial17"
    ) as client:
        # 1. Get account info
        acct = await client.get_account()
        print(f"Balance: ${acct.balance:.2f}")

        # 2. List symbols
        symbols = await client.get_symbols()
        print(f"Available: {len(symbols)} symbols")

        # 3. Subscribe to a quote
        quote = await client.subscribe("EURUSDm")
        print(f"EURUSDm: {quote.bid:.5f}/{quote.ask:.5f}")

        # 4. Get candle data
        candles = await client.get_candles("EURUSDm", "H1", 50)
        print(f"Got {len(candles)} hourly candles")

        # 5. Place trade
        result = await client.buy("EURUSDm", 0.01, sl=1.08, tp=1.12)
        print(f"Opened: order={result.order} @ {result.price:.5f}")

        # 6. Check positions
        positions = await client.get_positions()
        for pos in positions:
            print(f"  {pos.symbol} {'BUY' if pos.type==0 else 'SELL'} "
                  f"{pos.volume:.2f} @ {pos.price:.5f} P={pos.profit:.2f}")

        # 7. Modify SL/TP
        await client.modify_position(result.order, sl=1.085, tp=1.115)

        # 8. Partial close
        await client.partial_close(result.order, volume=0.005)

        # 9. Close remaining
        await client.close_position(result.order)

        # 10. Check deals
        deals = await client.get_deals()
        print(f"Total deals: {len(deals)}")

asyncio.run(main())
```

---

## Multi-Account Parallel Trading

Trade on multiple accounts simultaneously using `asyncio.gather`:

```python
import asyncio
from MT5API import MT5Client

ACCOUNTS = [
    {"login": 111111, "password": "pass1", "server": "Exness-MT5Trial17"},
    {"login": 222222, "password": "pass2", "server": "Exness-MT5Trial17"},
]

async def trade_account(acc):
    async with MT5Client(**acc) as client:
        await client.send_subscribe(["EURUSDm", "GBPUSDm"])
        await asyncio.sleep(5)  # Wait for quotes

        # All trades run in parallel within this account
        r1 = await client.buy("EURUSDm", 0.01)
        r2 = await client.buy("GBPUSDm", 0.01)
        print(f"Opened {r1.order} and {r2.order}")

        # Close using bypass params (avoids stale position list)
        await client.close_position(r1.order, symbol="EURUSDm", pos_type=0, volume=0.01)
        await client.close_position(r2.order, symbol="GBPUSDm", pos_type=0, volume=0.01)

async def main():
    await asyncio.gather(*[trade_account(acc) for acc in ACCOUNTS])

asyncio.run(main())
```

---

## Known Limitations

### Stale Position/Order List (cmd4)

The cmd4 response (used by `get_positions()` and `get_orders()`) does **not** update in real-time after trades. This means:
- A position opened via `buy()` may not appear in `get_positions()` immediately
- `close_position(ticket)` may raise `TradeError 10030` if the position list is stale

**Solution:** Use the bypass parameters. Pass `symbol`, `pos_type`, and `volume` from your original trade result to skip the stale list lookup:

```python
result = await client.buy("EURUSDm", 0.01)
# Don't rely on get_positions() — use bypass:
await client.close_position(result.order, symbol="EURUSDm", pos_type=0, volume=0.01)
```

### Position Cache

The client maintains an internal `_position_cache` that gets populated from successful `buy()`/`sell()` results. `close_position`, `modify_position`, and `cancel_order` check this cache as a fallback when the cmd4 list doesn't contain the ticket.

### Crypto SL/TP Offsets

For crypto symbols (BTCUSDm), SL/TP modify may fail with `retcode=10016` (INVALID_STOPS) if the offset is too small. Use the symbol's `point` value to calculate appropriate offsets.
