# MT5 Web Terminal Binary Record Schemas

Reverse-engineered from `BMwCVz0-.js` (web terminal JavaScript). Parser uses `Ac.parse()` with schema-driven `DataView` reads.

## Type System (propType)

| Type | Size | DataView Method |
|------|------|-----------------|
| 1 | 1B | `getInt8` |
| 2 | 2B | `getInt16LE` |
| 3 | 4B | `getInt32LE` |
| 4 | 1B | `getUint8` |
| 5 | 2B | `getUint16LE` |
| 6 | 4B | `getUint32LE` |
| 7 | 4B | `getFloat32LE` |
| 8 | 8B | `getFloat64LE` |
| 9 | 8B | FILETIME (int64 → epoch ms) |
| 10 | var | raw bytes (propLength) |
| 11 | var | UTF-16LE string (propLength) |
| 12 | var | raw buffer (propLength + parser) |
| 17 | 8B | `getBigInt64LE` |
| 18 | 8B | `getBigUint64LE` |

---

## POSITION Record (schema `uu`) — 344 bytes

Used by cmd4 (position list) and cmd10 (live position push).

JS parser: `function mu(t,e=0)` → `Ac.parse(t, uu, e)` → `new fd({...})`

| # | Offset | Size | Type | JS Field | Description |
|---|--------|------|------|----------|-------------|
| 0 | @0 | 8B | i64 | position_id | **Ticket** (position ID = ORDER ticket) |
| 1 | @8 | 8B | i64 | trade_order | Order ticket |
| 2 | @16 | 4B | u32 | time_create_sec | Open time (seconds) |
| 3 | @20 | 4B | u32 | time_update_sec | Update time (seconds) |
| 4 | @24 | 64B | UTF-16 | trade_symbol | Symbol name |
| 5 | @88 | 4B | u32 | trade_action | 0=BUY, 1=SELL |
| 6 | @92 | 8B | f64 | price_open | Open price |
| 7 | @100 | 8B | f64 | price_close | Close/current price |
| 8 | @108 | 8B | f64 | sl | Stop loss |
| 9 | @116 | 8B | f64 | tp | Take profit |
| 10 | @124 | 8B | u64 | trade_volume | Volume (lots × 100,000,000) |
| 11 | @132 | 8B | f64 | profit | **Profit** (server-provided) |
| 12 | @140 | 8B | f64 | rate_profit | Profit rate |
| 13 | @148 | 8B | f64 | rate_margin | Margin rate |
| 14 | @156 | 8B | f64 | commission | **Commission** |
| 15 | @164 | 8B | f64 | storage_ | **Swap** |
| 16 | @172 | 8B | i64 | expert | **Magic number** (native field) |
| 17 | @180 | 8B | i64 | expert_position_id | Expert position ID |
| 18 | @188 | 64B | UTF-16 | comment | Comment (may contain `#magic` prefix) |
| 19 | @252 | 8B | f64 | contract_size | Contract size |
| 20 | @260 | 4B | u32 | digits | Price digits |
| 21 | @264 | 4B | u32 | digits_currency | Currency digits |
| 22 | @268 | 4B | u32 | trade_reason | Trade reason |
| 23 | @272 | 64B | UTF-16 | external_id | External ID |
| 24 | @336 | 4B | i32 | time_create_ms | Open time (milliseconds) |
| 25 | @340 | 4B | i32 | time_update_ms | Update time (milliseconds) |

**Full time** = `1000 * time_create_sec + time_create_ms`

---

## DEAL Record (schema `xd`) — 356 bytes

Used by cmd5 (deal history).

JS parser: `function Pd(t,e=0)` → `Ac.parse(t, xd, e)` → `new fd({...})`

| # | Offset | Size | Type | JS Field | Description |
|---|--------|------|------|----------|-------------|
| 0 | @0 | 8B | i64 | deal | Deal ticket |
| 1 | @8 | 64B | UTF-16 | deal_id | Deal ID string |
| 2 | @72 | 8B | i64 | trade_order | Order ticket |
| 3 | @80 | 4B | u32 | time_create_sec | Deal time (seconds) |
| 4 | @84 | 4B | u32 | time_update_sec | Update time (seconds) |
| 5 | @88 | 64B | UTF-16 | trade_symbol | Symbol name |
| 6 | @152 | 4B | u32 | trade_action | 0=BUY, 1=SELL, 2=BALANCE... |
| 7 | @156 | 4B | u32 | entry | 0=in, 1=out, 2=in/out, 3=out by |
| 8 | @160 | 8B | f64 | price_open | Open price |
| 9 | @168 | 8B | f64 | price_close | Close price |
| 10 | @176 | 8B | f64 | sl | Stop loss |
| 11 | @184 | 8B | f64 | tp | Take profit |
| 12 | @192 | 8B | u64 | trade_volume | Volume |
| 13 | @200 | 8B | f64 | profit | **Profit** |
| 14 | @208 | 8B | f64 | rate_profit | Profit rate |
| 15 | @216 | 8B | f64 | rate_margin | Margin rate |
| 16 | @224 | 8B | f64 | commission | Commission |
| 17 | @232 | 8B | f64 | storage_ | Swap |
| 18 | @240 | 8B | i64 | expert | **Magic number** |
| 19 | @248 | 8B | i64 | position_id | Position ID |
| 20 | @256 | 64B | UTF-16 | comment | Comment |
| 21 | @320 | 8B | f64 | contract_size | Contract size |
| 22 | @328 | 4B | u32 | digits | Price digits |
| 23 | @332 | 4B | u32 | digits_currency | Currency digits |
| 24 | @336 | 4B | u32 | trade_reason | Trade reason |
| 25 | @340 | 4B | i32 | time_create_ms | Deal time (ms) |
| 26 | @344 | 4B | i32 | time_update_ms | Update time (ms) |
| 27 | @348 | 8B | f64 | commission_fee | Commission fee |

---

## ORDER Record (schema `Wd`) — 356 bytes

Used by cmd4 (order list) for pending orders.

JS parser: `function Ld(t,e=0)` → `Ac.parse(t, Wd, e)` → `new sd({...})`

| # | Offset | Size | Type | JS Field | Description |
|---|--------|------|------|----------|-------------|
| 0 | @0 | 8B | i64 | trade_order | **Order ticket** |
| 1 | @8 | 64B | UTF-16 | order_id | Order ID string |
| 2 | @72 | 64B | UTF-16 | trade_symbol | Symbol name |
| 3 | @136 | 4B | u32 | time_setup_sec | Setup time (seconds) |
| 4 | @140 | 4B | u32 | time_expiration_sec | Expiration (seconds) |
| 5 | @144 | 4B | u32 | time_done_sec | Done time (seconds) |
| 6 | @148 | 4B | u32 | order_type | 0=BUY_LIMIT... |
| 7 | @152 | 4B | u32 | type_filling | Filling type |
| 8 | @156 | 4B | u32 | type_time | Time type |
| 9 | @160 | 4B | u32 | type_reason | Reason |
| 10 | @164 | 8B | f64 | price_order | Order price |
| 11 | @172 | 8B | f64 | price_trigger | Trigger price |
| 12 | @180 | 8B | f64 | price_current | Current price |
| 13 | @188 | 8B | f64 | price_sl | Stop loss |
| 14 | @196 | 8B | f64 | price_tp | Take profit |
| 15 | @204 | 8B | i64 | volume_initial | Initial volume |
| 16 | @212 | 8B | i64 | volume_current | Current volume |
| 17 | @220 | 4B | u32 | order_state | State |
| 18 | @224 | 8B | i64 | expert | **Magic number** |
| 19 | @232 | 8B | i64 | position_id | Position ID |
| 20 | @240 | 64B | UTF-16 | comment | Comment |
| 21 | @304 | 8B | f64 | contract_size | Contract size |
| 22 | @312 | 4B | u32 | digits | Price digits |
| 23 | @316 | 4B | u32 | digits_currency | Currency digits |
| 24 | @320 | 8B | f64 | commission_daily | Daily commission |
| 25 | @328 | 8B | f64 | commission_monthly | Monthly commission |
| 26 | @336 | 8B | f64 | margin_rate | Margin rate |
| 27 | @344 | 4B | u32 | activation_mode | Activation mode |
| 28 | @348 | 4B | i32 | time_setup_ms | Setup time (ms) |
| 29 | @352 | 4B | i32 | time_done_ms | Done time (ms) |

---

## ACCOUNT Record (schema `fl` + `al`) — Variable

Used by cmd3 (account info). Complex nested structure:
- `fl` (26 fields): base account data
- `al` (39 fields): symbol trade settings (repeated array)

### Base Account (schema `fl`)

| Index | Type | Field |
|-------|------|-------|
| 0 | u8 | account_type |
| 1 | i32 | rights |
| 2 | i32 | permissions_flags |
| 3 | i32 | balance |
| 4 | i32 | credit |
| 5 | UTF-16 256B | account_currency... no wait |

Actually, the account is parsed differently — `wl()` function at `fl` schema offset. See `kl()` function for full parsing.

---

## cmd10 368-byte Body — UNKNOWN FORMAT

The cmd10 live position push sends 368-byte bodies. This does NOT match any confirmed schema:

| Schema | Size | Match? |
|--------|------|--------|
| Position (uu) | 344B | ✗ (368 ≠ 344) |
| Deal (xd) | 356B | ✗ (368 ≠ 356) |
| Order (Wd) | 356B | ✗ (368 ≠ 356) |

Previous hex-scan offsets (ticket@12, symbol@84, type@160, open_price@192) are **inconsistent** with all three schemas. The format needs empirical re-verification via raw data capture.

**Theory**: 24-byte header + 344-byte position record = 368 bytes. This would mean the position record starts at offset 24, giving:
- ticket @24 (i64)
- symbol @48 (UTF-16 64B)
- type @112 (u32)
- open_price @116 (f64)
- sl @124 (f64)
- tp @132 (f64)
- volume @140 (u64)
- profit @156 (f64)
- magic @196 (i64)
- comment @212 (UTF-16 64B)

**Needs verification via diagnostic script.**

---

## Wire Protocol Summary

- **Packet**: `[length:4 LE][version:4 LE][encrypted_payload]`
- **Command**: `[random:2][cmd_id:2 LE][serialized_payload]`
- **Response**: `[tag:2][cmd_id:2 LE][res_code:1][res_body...]`
- **Encryption**: AES-256-CBC, ZERO IV, static+session key
- **Volume**: lots × 100,000,000 (e.g. 0.01 lots = 1,000,000)
- **Tickets**: unsigned int64 (`<Q`)

## Key Commands

| Cmd | Purpose | Record Format |
|-----|---------|---------------|
| 3 | Account info | Nested (fl schema) |
| 4 | Position + order list | Position 344B + Order 356B |
| 5 | Deal history | Deal 356B |
| 10 | Live position push | **Unknown 368B** |
| 12 | Trade request | N/A (outbound) |
| 19 | Trade result | N/A (response) |
| 22 | Position push (alt) | Same as cmd10 |
