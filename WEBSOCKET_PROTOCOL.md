# MT5 WebSocket Protocol — Complete Technical Reference
## Exness Web Terminal (Pure Python Client)

---

## 1. Connection

```
URL: wss://15.206.31.153:443/terminal
TLS: *.exwebterm.com (Sectigo) — disable cert verification
Origin: https://15.206.31.153:443
```

## 1.1 Build / Terminal Version

**Build number is server-side only** — embedded in the HTML page served by the broker.

```html
<script type="text/javascript">
window.__terminal_params = {
    "build": 5830,
    "build_date": "24 Apr 2026",
    "trade_server_demo": "Exness-MT5Trial17",
    "trade_server_real": "Exness-MT5Trial17",
    "broker": { "name": "Exness Technologies Ltd", ... },
    ...
};
</script>
```

### How to Extract
```python
import re, requests

def get_build(url="https://15.206.31.153:443/terminal"):
    html = requests.get(url, verify=False, timeout=10).text
    m = re.search(r'"build"\s*:\s*(\d+)', html)
    build = int(m.group(1)) if m else None
    m2 = re.search(r'"build_date"\s*:\s*"([^"]+)"', html)
    build_date = m2.group(1) if m2 else None
    return build, build_date

# Usage:
build, build_date = get_build()  # (5830, '24 Apr 2026')
```

### Protocol Comparison

| Protocol | Build sent to server? | Build source |
|----------|----------------------|--------------|
| **Native TCP (desktop API)** | ✅ Client sends in 34-byte login packet | Config.Build (default: 5500) |
| **WebSocket (web terminal)** | ❌ NOT sent | HTML page: `window.__terminal_params.build` |

### What Happens When MT5 Updates?

- **WebSocket**: Server serves new HTML with new build number (e.g. 5880). The WS protocol stays the same — no changes needed in client code.
- **Native TCP**: Desktop API sends new build number. Server enables/disables features based on build. Client must send build ≥ minimum (5500).

### Current Values

| Field | Value | Source |
|-------|-------|--------|
| build | 5830 | `window.__terminal_params.build` |
| build_date | 24 Apr 2026 | `window.__terminal_params.build_date` |
| server_build (DLL) | 5500 | Default in MT5API DLL |
| server_build (alt) | 3900 | For certain hosts (line 6697) |

## 1.2 Broker Search API — Server Name → IP Resolution

MT5 desktop clients use `Broker.Search()` and `Broker.SearchMQ()` to resolve server names to IP addresses. Both APIs are implemented in `broker_search.py`.

### API Endpoints (decrypted from mt5api.dll via .NET reflection)

| API | Method | URL | Auth |
|---|---|---|---|
| Search | GET | `http://search.mtapi.io/Search?company={name}&mt5=true` | None |
| SearchMQ | POST | `https://updates.metaquotes.net/public/mt5/network` | HMAC + Cookie |

### SearchMQ Signature Scheme

The signature is computed as: `MD5(MD5(body_bytes) + key_32bytes)`

```python
import hashlib

HMAC_KEY = bytes([61, 123, 21, 22, 214, 234, 187, 52, 217, 214,
                  99, 227, 98, 62, 27, 215, 251, 220, 174, 244,
                  87, 59, 223, 53, 127, 168, 207, 11, 190, 173,
                  146, 127])

body = "company=Exness-MT5Trial17&code=mt5"
body_hash = hashlib.md5(body.encode('latin-1')).digest()
sig = hashlib.md5(body_hash + HMAC_KEY).hexdigest()
full_body = f"{body}&signature={sig}&ver=2"
```

### SearchMQ Cookie Format

```
_fz_uniq={val};uniq={val};age={age};tid={tid}
```

Where:
- `val` = `((unix_ts - 1420070400) | ((ms & 0x1FFFFFF) << 32)) | 0x4200000000000000`
- `age` = `unix_ts - 86400`
- `tid` = first 8 bytes of MD5(prng_state), hex-encoded

### Response Format

SearchMQ response has a prefix before JSON:
```
signature=ED7981D12ED604FF8B668748A2D8670D
{"result":[{"company":"Exness Technologies Ltd","results":[...]}]}
```

Strip everything before the first `{` before parsing JSON.

### Response JSON Schema

```json
{
  "result": [
    {
      "company": "Exness Technologies Ltd",
      "results": [
        {
          "name": "Exness-MT5Trial17",
          "logo_url": "https://download.terminal.free/cdn/mobile/logos/...",
          "logo_hash": "D1DF1F68BBC141252E24E64D777A5390",
          "site": "www.exness.com",
          "access": ["13.213.81.113:443", "16.78.218.32:443", ...],
          "is_demo": 0
        }
      ]
    }
  ]
}
```

### MT5API Constructor Fallback Chain (from MT5API.cs line 2169)

```
MT5API(user, password, "Exness-MT5Trial17")
  → Check 4-hour cache
  → Broker.SearchAsync(server)  // HTTP GET, 10s timeout
    → Fail → Broker.SearchMQ(server)  // HTTP POST + HMAC
      → Fail → Check cache again
        → Fail → throw ServerNotFoundException
```

### Usage

```python
from broker_search import search, search_mq, find_server, find_server_ips

# SearchMQ (MetaQuotes official endpoint)
results = search_mq("Exness-MT5Trial17")

# Find specific server
info = find_server("Exness-MT5Trial17")
# → {"company": "Exness Technologies Ltd", "access": ["13.213.81.113:443", ...]}

# Just IPs
ips = find_server_ips("Exness-MT5Trial17")
# → ["13.213.81.113:443", "16.78.218.32:443", ...]
```

## 2. Encryption

**Algorithm**: AES-256-CBC with ZERO IV (16 zero bytes), PKCS7 padding

**Static Key** (auth phase only):
```
02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964
```

**Session Key**: Last 32 bytes of decrypted auth response body (bytes 66+). Changes every session. Used for ALL subsequent frames.

```python
from Crypto.Cipher import AES

ZERO_IV = bytes(16)

def aes_encrypt(key, plaintext):
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len] * pad_len)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(padded)

def aes_decrypt(key, ciphertext):
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    pad_len = pt[-1]
    if 1 <= pad_len <= 16 and all(b == pad_len for b in pt[-pad_len:]):
        return pt[:-pad_len]
    return pt
```

## 3. Wire Format

### Outgoing (Client → Server)
```
[payload_length:4 LE uint32][version=1:4 LE uint32][encrypted_data]
```
- `payload_length` = length of encrypted_data
- `version` = always 1
- `encrypted_data` = AES-256-CBC encrypted command

### Incoming (Server → Client)
Same format: `[payload_length:4][version:4][encrypted_data]`

## 4. Command Format (inside encrypted payload)

```
[random1:1 byte][random2:1 byte][cmd_id:2 LE uint16][payload:N bytes]
```
- `random1, random2` = random bytes (not used for anything)
- `cmd_id` = command identifier (little-endian uint16)
- `payload` = command-specific data

## 5. Response Format (inside decrypted data)

```
[tag:2 LE uint16][cmd_id:2 LE uint16][res_code:1 byte][res_body:N bytes]
```
- `tag` = response tag (usually matches cmd_id or related)
- `cmd_id` = command identifier
- `res_code` = 0 = success, nonzero = error
- `res_body` = response-specific data

## 6. Serialization System (Ac.parse / Ac.serialize)

**CRITICAL**: Type 6 = uint32 (4 bytes), NOT uint64!

| Type | Name | Size | JS Implementation |
|------|------|------|-------------------|
| 1 | int8 | 1B | `getInt8` |
| 2 | int16 | 2B | `getInt16(LE)` |
| 3 | int32 | 4B | `getInt32(LE)` |
| 4 | uint8 | 1B | `getUint8` |
| 5 | uint16 | 2B | `getUint16(LE)` |
| 6 | uint32 | 4B | `getUint32(LE)` |
| 7 | float32 | 4B | `getFloat32(LE)` |
| 8 | float64 | 8B | `getFloat64(LE)` |
| 11 | UTF-16LE | propLength B | Decode as UTF-16LE, null-terminated |
| 12 | raw | propLength B | Raw bytes |
| 17 | int64 | 8B | `getBigInt64(LE)` |
| 18 | uint64 | 8B | `getBigUint64(LE)` |

Schema is array of `{propType, propLength?}` objects.

## 7. Auth Flow

```
1. Client → Server: cmd_id=0, payload=bytes(64)
   Encrypted with: STATIC_KEY
   
2. Server → Client: response with res_code=0
   Decrypted body structure:
   [tag:2][cmd_id=0:2][res_code=0:1]
   [sub_tag_1:1][sub_tag_2:1]     ← bytes 5-6
   [session_token:64 bytes ASCII]  ← bytes 7-70
   [session_key:32 bytes raw]      ← bytes 71-102
   
3. Import session_key for all subsequent encryption
```

## 8. Login (cmd_id=28)

**NOTE**: Build number is NOT sent here. It's only in the HTML page (`window.__terminal_params.build`).
Server already knows the build because it served the HTML.

912-byte payload built with Ac.serialize:
```python
def build_login_payload(login_id, password, url):
    h = bytearray(912)
    pw = password.encode('utf-16-le')
    h[4:4+len(pw)] = pw                              # password (64B)
    struct.pack_into('<I', h, 476, len(url))          # url length
    ip = url.encode('utf-16-le')
    h[480:480+len(ip)] = ip                           # server IP (256B)
    struct.pack_into('<Q', h, 736, login_id)          # login ID (uint64)
    return bytes(h)
```

Login response (Dc schema):
```
[{propType:12, propLength:160}, {propType:18}]
→ [account_data:160 bytes raw][account_id:uint64]
```

After login, server sends cmd_id=15 (system notification, 4B uint32).

## 9. Get Account (cmd_id=3)

**No payload needed.** Response: 6496B body.

Parse with `kl()` function using fl schema (816B header) + accounts array + sub-arrays.

```python
FL_SCHEMA = [
    {'propType':4},          # flags
    {'propType':3},          # login_id_2
    {'propType':3},          # permissions
    {'propType':8},          # balance ← float64
    {'propType':8},          # equity ← float64
    {'propType':11,'propLength':64},   # currency (UTF-16LE)
    {'propType':6},          # field6 (uint32)
    {'propType':6},          # field7 (uint32)
    {'propType':11,'propLength':256},  # group (UTF-16LE)
    {'propType':5},          # leverage (uint16)
    {'propType':11,'propLength':128},  # server (UTF-16LE)
    {'propType':11,'propLength':256},  # account_name (UTF-16LE)
    {'propType':3},          # trade_mode
    {'propType':1},          # some_flag
    {'propType':6},          # credit (uint32)
    {'propType':6},          # bonus (uint32)
    {'propType':8},          # profit ← float64
    {'propType':8},          # margin ← float64
    {'propType':6},          # field18 (uint32)
    {'propType':8},          # margin_float3
    {'propType':6},          # stop_out (uint32)
    {'propType':8},          # margin_float4
    {'propType':8},          # margin_float5
    {'propType':8},          # margin_float6
    {'propType':6},          # password_min (uint32)
    {'propType':6},          # password_flags (uint32)
]
FL_SIZE = 816
```

## 10. Get Symbols (cmd_id=34)

**No payload needed.** Response is gzip-compressed.

```python
decompressed = zlib.decompress(response_body[4:])  # skip 4-byte header
count = struct.unpack_from('<I', decompressed, 0)[0]  # symbol count
off = 4
for i in range(count):
    vals, off = parse_series(decompressed, MH_SCHEMA, off)
    # vals[0]=name, vals[1]=description, vals[2]=digits, vals[3]=symbol_id, vals[4]=path
```

```python
MH_SCHEMA = [
    {'propType':11,'propLength':64},   # name
    {'propType':11,'propLength':128},  # description
    {'propType':6},                    # digits (uint32)
    {'propType':6},                    # symbol_id (uint32) ← FOR SUBSCRIPTION
    {'propType':11,'propLength':256},  # path
    {'propType':6},                    # trade_calc_mode
    {'propType':11,'propLength':64},   # basis
    {'propType':5},                    # sector (uint16)
]
MH_SIZE = 526
```

## 11. Subscribe to Live Quotes (cmd_id=7)

```python
# Payload: [count:uint32, symbol_id_1:uint32, symbol_id_2:uint32, ...]
payload = struct.pack('<I', len(symbol_ids))
for sid in symbol_ids:
    payload += struct.pack('<I', sid)
```

Symbol IDs come from cmd_id=34 response (field index 3).

Server pushes cmd_id=8 (QUOTES) frames:
```python
QUOTE_SCHEMA = [
    {'propType':6},   # symbol_id (uint32)
    {'propType':3},   # tick_time (int32, unix seconds)
    {'propType':6},   # fields (uint32, bitmask)
    {'propType':8},   # bid (float64, RAW)
    {'propType':8},   # ask (float64, RAW)
    {'propType':8},   # last (float64, RAW)
    {'propType':17},  # tick_volume (int64)
    {'propType':6},   # time_ms_delta (uint32)
    {'propType':5},   # flags (uint16)
]
QUOTE_SIZE = 50
```

**Prices are RAW**: actual_price = raw_value / 10^digits
- EURUSDm (digits=5): bid=114011 → 1.14011
- BTCUSDm (digits=2): bid=6260133 → 62601.33

Multiple quotes can be in one frame: `body_size / 50 = quote_count`

## 12. Trade Orders (cmd_id=12)

248-byte serialized payload (Op schema):

```python
OP_SCHEMA = [
    {'propType':6},          # [0]  action_id (uint32) = 0
    {'propType':6},          # [1]  trade_action (uint32) ← SEE TABLE BELOW
    {'propType':11,'propLength':64},   # [2]  symbol (UTF-16LE 64B)
    {'propType':18},         # [3]  volume (uint64) = lots × 100000
    {'propType':6},          # [4]  digits (uint32) = symbol digits
    {'propType':18},         # [5]  trade_order (uint64) = 0 for new order
    {'propType':6},          # [6]  trade_type (uint32) = 0=BUY, 1=SELL
    {'propType':6},          # [7]  type_filling (uint32) = 0=FOK, 1=IOC, 2=RETURN
    {'propType':6},          # [8]  type_time (uint32) = 0=GTC, 1=DAY, 2=SPECIFIED
    {'propType':6},          # [9]  type_flags (uint32) = 2 for new orders
    {'propType':6},          # [10] type_reason (uint32) = 0
    {'propType':8},          # [11] price_order (float64) = ask for buy, bid for sell
    {'propType':8},          # [12] price_trigger (float64) = 0
    {'propType':8},          # [13] price_sl (float64) = stop loss price
    {'propType':8},          # [14] price_tp (float64) = take profit price
    {'propType':6},          # [15] price_deviation (uint32) = 0
    {'propType':8},          # [16] price_top (float64) = 0
    {'propType':8},          # [17] price_bottom (float64) = 0
    {'propType':11,'propLength':64},   # [18] comment (UTF-16LE 64B)
    {'propType':18},         # [19] trade_position (uint64) = position_id for close/modify
    {'propType':18},         # [20] position_by (uint64) = 0
    {'propType':6},          # [21] time_expiration (uint32) = 0
]
OP_SIZE = 248
```

### Field-by-Field Reference

#### [0] action_id (uint32) = 0
Internal action counter, always 0 for new orders.

#### [1] trade_action (uint32) — ORDER TYPE
| Value | Action | When To Use |
|-------|--------|-------------|
| 0 | DEAL | Legacy instant deal |
| 1 | PENDING | **DO NOT USE** — returns 10013 (INVALID) |
| 2 | INSTANT | Instant execution (web terminal market) |
| 3 | MARKET | Market execution **(verified for open/close)** |
| 4 | EXCHANGE | Exchange execution |
| 5 | PENDING | **Place pending order (limit/stop)** — VERIFIED |
| 8 | CANCEL | **Cancel pending order** — VERIFIED |
| 10 | CLOSE | **DO NOT USE** — returns 10030. Use action=3 + opposite type |
| 201 | MODIFY | **DO NOT USE** — returns 10030 |

**Summary (verified):**
- **Market BUY/SELL**: `trade_action=3` (MARKET), `trade_type=0` (BUY) or `1` (SELL)
- **Pending orders**: `trade_action=5` (PENDING), `trade_type=2/3/4/5` (LIMIT/STOP)
- **Close position**: `trade_action=3` (MARKET) + opposite `trade_type` + `trade_position`

#### [2] symbol (UTF-16LE 64B)
Symbol name, e.g. `EURUSDm`, `BTCUSDm`.

#### [3] volume (uint64)
Volume in **raw units**. Formula: `lots × 100000000`
| Lots | Raw Value |
|------|-----------|
| 0.01 | 1,000,000 |
| 0.10 | 10,000,000 |
| 1.00 | 100,000,000 |
| 10.0 | 1,000,000,000 |

**Verified**: 0.01 lots = 1,000,000 raw units (confirmed by P/L calculation: -3.42 = (1.14191-1.14533) × 1000 units)

#### [4] digits (uint32)
Symbol decimal places. EURUSDm=5, BTCUSDm=2, XAUUSDm=2.

#### [5] trade_order (uint64) = 0
Order ticket. `0` for new orders. For close/modify, use the order ticket from cmd_id=4.

#### [6] trade_type (uint32) — DIRECTION
| Value | Type |
|-------|------|
| 0 | BUY |
| 1 | SELL |

For pending orders:
| Value | Type |
|-------|------|
| 2 | BUY LIMIT |
| 3 | SELL LIMIT |
| 4 | BUY STOP |
| 5 | SELL STOP |

#### [7] type_filling (uint32) — FILL MODE
| Value | Mode | Description |
|-------|------|-------------|
| 0 | FOK | **Fill or Kill** — fill all or cancel (use for market) |
| 1 | IOC | Immediate or Cancel — fill what's available |
| 2 | RETURN | Return unfilled portion |

#### [8] type_time (uint32) — ORDER DURATION
| Value | Mode | Description |
|-------|------|-------------|
| 0 | GTC | Good Till Cancel (default) |
| 1 | DAY | Valid for 1 day only |
| 2 | SPECIFIED | Valid until specific time |

#### [9] type_flags (uint32) = 2
Flags for the order. `2` = new order. Internal flag.

#### [10] type_reason (uint32) = 0
Order reason. `0` = client request.

#### [11] price_order (float64)
Order price:
- **BUY**: set to current **ask** price
- **SELL**: set to current **bid** price
- **Pending**: set to your limit/stop price

#### [12] price_trigger (float64) = 0
Stop price for stop orders. `0` for market orders.

#### [13] price_sl (float64)
Stop Loss price. `0` = no SL.

#### [14] price_tp (float64)
Take Profit price. `0` = no TP.

#### [15] price_deviation (uint32) = 0
Max slippage allowed (in points). `0` = any.

#### [16] price_top (float64) = 0
Upper price limit (for pending orders). `0` = no limit.

#### [17] price_bottom (float64) = 0
Lower price limit (for pending orders). `0` = no limit.

#### [18] comment (UTF-16LE 64B)
Order comment. `0` = empty.

#### [19] trade_position (uint64) = 0
Position ticket:
- `0` for new orders
- `position_id` from cmd_id=4 for **close/modify**

#### [20] position_by (uint64) = 0
Close-by position ID. `0` = normal close.

#### [21] time_expiration (uint32) = 0
Expiration time (unix timestamp). `0` = no expiration.

### Response — VERIFIED WORKING

**Immediate response (cmd_id=12)**: 4B body with uint32 `retcode` (may arrive before TRADE_EVENT)

**TRADE_EVENT (cmd_id=19)**: 380B body — TWO events per trade:
1. First event: `retcode=10002` (ACK) — trade received, being processed
2. Second event: `retcode=10009` (ACCEPTED) with deal details — trade executed

**Response body layout (380 bytes):**
```
[prefix:4 bytes]     — sequence/event ID (not action_id)
[Op:248 bytes]       — echoed trade request (same as sent, with server corrections)
[Ap:128 bytes]       — server response with deal details
```

**Op echo (offset 4, 248 bytes):**
```
[0:4]   action_id (uint32) — echoed from sent request
[4:8]   trade_action (uint32)
[8:72]  symbol (UTF-16LE 64B) — may be zeroed in some events
[72:80] volume (uint64)
[80:84] digits (uint32)
[84:92] trade_order (uint64) — order ticket assigned by server
[92:96] trade_type (uint32)
[112:120] price (float64) — execution price
```

**Ap response (offset 252, 128 bytes):**
```
[0:4]   retcode (uint32) — 10009=success, 10013=invalid params, etc.
[4:12]  deal (int64) — deal ticket number
[12:20] order (int64) — order ticket number
[20:28] volume (int64) — executed volume
[28:36] price (float64) — execution price
[64:128] comment (UTF-16LE 64B) — 'ok' on success
```

**Return codes:**
| Code | Meaning |
|------|---------|
| 0 | SUCCESS |
| 10002 | TRADE_NOT_ACCEPTED (intermediate ACK) |
| 10009 | TRADE_ACCEPTED (final success) |
| 10013 | INVALID_PARAMETERS |
| 10014 | INVALID_VOLUME |
| 10015 | INVALID_PRICE |
| 10016 | INVALID_STOPS |
| 10017 | TRADE_DISABLED |
| 10030 | INVALID_TRADE_ACTION |

### Order Examples

**Market BUY 0.01 lots EURUSDm:**
```python
op = bytearray(248)
struct.pack_into('<I', op, 0, 0)                          # [0]  action_id = 0
struct.pack_into('<I', op, 4, 3)                          # [1]  trade_action = MARKET
op[8:8+len('EURUSDm'.encode('utf-16-le'))] = 'EURUSDm'.encode('utf-16-le')  # [2]  symbol
struct.pack_into('<Q', op, 72, 100000)                    # [3]  volume = 0.01 lots
struct.pack_into('<I', op, 80, 5)                         # [4]  digits = 5
struct.pack_into('<Q', op, 84, 0)                         # [5]  trade_order = 0 (new)
struct.pack_into('<I', op, 92, 0)                         # [6]  BUY
struct.pack_into('<I', op, 96, 0)                         # [7]  FOK
struct.pack_into('<I', op, 100, 0)                        # [8]  GTC
struct.pack_into('<I', op, 104, 2)                        # [9]  flags = 2
struct.pack_into('<I', op, 108, 0)                        # [10] reason = 0
struct.pack_into('<d', op, 112, ask_price)                # [11] price = ask
struct.pack_into('<d', op, 120, 0)                        # [12] trigger = 0
struct.pack_into('<d', op, 128, sl_price)                 # [13] SL = 0 (or price)
struct.pack_into('<d', op, 136, tp_price)                 # [14] TP = 0 (or price)
struct.pack_into('<I', op, 144, 0)                        # [15] deviation = 0
struct.pack_into('<d', op, 148, 0)                        # [16] top = 0
struct.pack_into('<d', op, 156, 0)                        # [17] bottom = 0
# [18] comment at offset 164, 64B — leave empty
struct.pack_into('<Q', op, 228, 0)                        # [19] position = 0
struct.pack_into('<Q', op, 236, 0)                        # [20] position_by = 0
# [21] expiration at offset 244, uint32 = 0
```

**Market SELL 0.01 lots:**
```python
# Same as BUY but:
struct.pack_into('<I', op, 92, 1)          # [6] SELL
struct.pack_into('<d', op, 112, bid_price)  # [11] price = bid
```

**Close Position (sell to close a BUY) — VERIFIED WORKING:**
```python
# Use trade_action=3 (MARKET), opposite trade_type, and set position_id
struct.pack_into('<I', op, 4, 3)                         # [1] MARKET (NOT 10=CLOSE)
struct.pack_into('<I', op, 92, 1)                        # [6] SELL (opposite of open)
struct.pack_into('<d', op, 112, bid_price)               # [11] price = BID for closing BUY
struct.pack_into('<Q', op, 228, position_id)             # [19] position to close
```
**Note**: trade_action=10 (CLOSE) does NOT work — server returns 10030 (INVALID_TRADE_ACTION).
The correct approach is to use trade_action=3 (MARKET) with the opposite trade_type and position_id.

**Set SL/TP on existing position — VERIFIED WORKING (trade_action=6):**
```python
op = bytearray(248)
struct.pack_into('<I', op, 4, 6)                         # [1] trade_action = 6 (MODIFY_DEAL)
op[8:8+len('EURUSDm'.encode('utf-16-le'))] = 'EURUSDm'.encode('utf-16-le')  # symbol
struct.pack_into('<Q', op, 72, volume)                    # [3] volume = position volume
struct.pack_into('<I', op, 80, 5)                         # [4] digits = 5
struct.pack_into('<I', op, 92, 0)                         # [6] trade_type = BUY (0) or SELL (1)
struct.pack_into('<I', op, 96, 2)                         # [7] type_filling = 2 (RETURN)
struct.pack_into('<d', op, 112, bid_price)                # [11] price = current BID
struct.pack_into('<d', op, 128, new_sl_price)             # [13] new SL price
struct.pack_into('<d', op, 136, new_tp_price)             # [14] new TP price
struct.pack_into('<Q', op, 228, order_ticket)             # [19] ORDER ticket (NOT deal ticket!)
```

**CRITICAL: Position ID = ORDER ticket** from TRADE_EVENT response (field `order`), NOT the `deal` field!
- TRADE_EVENT returns: `retcode=10009, deal=<deal_ticket>, order=<order_ticket>`
- The `order` value is what you use as `pos_id` (offset 228) for modify AND close operations
- The `deal` value does NOT work — server returns 10036 (POSITION_NOT_EXISTS)

**Failed approaches:**
- trade_action=201 (ForOrderPrice) → 10030 (UNSUPPORTED_FILLING_MODE)
- trade_action=10 (ClosePosition) → 10030 (UNSUPPORTED_FILLING_MODE)
- Using deal ticket instead of order ticket → 10036 (POSITION_NOT_EXISTS)

### Pending Orders (Limit/Stop)

Pending orders wait for price to reach a trigger level, then convert to open positions.

#### Order Types

| trade_type | Name | Triggers When |
|------------|------|---------------|
| 2 | BUY LIMIT | Price goes **DOWN** to your price (below current) |
| 3 | SELL LIMIT | Price goes **UP** to your price (above current) |
| 4 | BUY STOP | Price goes **UP** to your price (above current) |
| 5 | SELL STOP | Price goes **DOWN** to your price (below current) |
| 6 | BUY STOP LIMIT | Price goes UP → triggers BUY LIMIT (Limit <= Stop) |
| 7 | SELL STOP LIMIT | Price goes DOWN → triggers SELL LIMIT (Limit >= Stop) |

```
Current Price = 1.10000

BUY LIMIT @ 1.09500  → triggers when price drops to 1.09500
SELL LIMIT @ 1.10500 → triggers when price rises to 1.10500
BUY STOP @ 1.10500   → triggers when price rises to 1.10500
SELL STOP @ 1.09500  → triggers when price drops to 1.09500
BUY STOP LIMIT   → Stop=1.10500, Limit=1.10490 → triggers at 1.10500, places BUY LIMIT @ 1.10490
SELL STOP LIMIT  → Stop=1.09500, Limit=1.09510 → triggers at 1.09500, places SELL LIMIT @ 1.09510
```

#### Place Pending Order — VERIFIED WORKING

```python
op = bytearray(248)
struct.pack_into('<I', op, 0, 0)                          # action_id = 0
struct.pack_into('<I', op, 4, 5)                          # trade_action = 5 (PENDING) ← NOT 1!
op[8:8+len('EURUSDm'.encode('utf-16-le'))] = 'EURUSDm'.encode('utf-16-le')  # symbol
struct.pack_into('<Q', op, 72, 100000)                    # volume = 0.01 lots (×100000000)
struct.pack_into('<I', op, 80, 5)                         # digits = 5
struct.pack_into('<Q', op, 84, 0)                         # trade_order = 0 (new)
struct.pack_into('<I', op, 92, 2)                         # trade_type = BUY LIMIT
struct.pack_into('<I', op, 96, 2)                         # type_filling = 2 (RETURN) ← NOT 0!
struct.pack_into('<I', op, 100, 0)                        # type_time = GTC
struct.pack_into('<I', op, 104, 2)                        # type_flags = 2
struct.pack_into('<d', op, 112, 1.09500)                  # price_order = your limit/stop price
struct.pack_into('<d', op, 120, 0)                        # price_trigger = 0 (nonzero for stop-limit)
struct.pack_into('<d', op, 128, 1.09000)                  # SL
struct.pack_into('<d', op, 136, 1.10000)                  # TP
```

**Key differences from market orders:**
- `trade_action=5` (PENDING), not `3` (MARKET)
- `type_filling=2` (RETURN), not `0` (FOK)
- Price must be 100+ _Point away from current price (e.g. 0.00100 for 5-digit pair)
- `price_trigger=0` for regular limit/stop; set for stop-limit orders

#### Place Stop-Limit Order — VERIFIED WORKING

Stop-limit = Stop price triggers → places Limit order.

```python
# BUY STOP LIMIT (trade_type=6): Stop above current, Limit <= Stop
stop_price = round(ask + 200*POINT, 5)
limit_price = round(stop_price - 10*POINT, 5)  # Limit <= Stop for BUY

aid = random.randint(1, 0x7FFFFFFE)
op = bytearray(248)
struct.pack_into('<I', op, 0, aid)
struct.pack_into('<I', op, 4, 5)                          # trade_action = 5 (PENDING)
op[8:8+len('EURUSDm'.encode('utf-16-le'))] = 'EURUSDm'.encode('utf-16-le')
struct.pack_into('<Q', op, 72, 1000000)                    # volume
struct.pack_into('<I', op, 80, 5)                          # digits
struct.pack_into('<I', op, 92, 6)                          # trade_type = 6 (BUY STOP LIMIT)
struct.pack_into('<I', op, 96, 2)                          # type_filling = RETURN
struct.pack_into('<d', op, 112, stop_price)                # price_order = stop price (trigger)
struct.pack_into('<d', op, 120, limit_price)               # price_trigger = limit price (actual order)
struct.pack_into('<d', op, 128, sl_price)                  # SL
struct.pack_into('<d', op, 136, tp_price)                  # TP
# Send as cmd_id=12 (TRADE)
```

**Key rules for stop-limit:**
- **BUY STOP LIMIT (type=6)**: Stop above current Ask, Limit <= Stop
- **SELL STOP LIMIT (type=7)**: Stop below current Bid, Limit >= Stop
- Both `price_order` (stop) and `price_trigger` (limit) must be set
- SL/TP can be set during placement

#### Check Pending Orders

```python
send(cmd=4)  # same as positions
# Response: [pos_count, positions, order_count, orders]
#                              ↑                      ↑
#                         market trades         pending orders
```

The `order_count` section contains pending orders. Each pending order has similar fields to positions.

#### Cancel Pending Order — VERIFIED WORKING

```python
# Use trade_action=8 (CancelOrder), original trade_type, and order ticket
aid = random.randint(1, 0x7FFFFFFE)
op = bytearray(248)
struct.pack_into('<I', op, 0, aid)                      # action_id
struct.pack_into('<I', op, 4, 8)                        # trade_action = 8 (CancelOrder)
op[8:8+len('EURUSDm'.encode('utf-16-le'))] = 'EURUSDm'.encode('utf-16-le')
struct.pack_into('<Q', op, 72, 100000)                  # volume
struct.pack_into('<I', op, 80, 5)                       # digits
struct.pack_into('<Q', op, 84, order_ticket)            # trade_order = order ticket to cancel
struct.pack_into('<I', op, 92, 2)                       # trade_type = original type (2=BUY_LIMIT)
struct.pack_into('<I', op, 96, 2)                       # type_filling = 2 (RETURN)
# Send as cmd_id=12 (TRADE)
```

**Key**: `trade_type` must match the original order type (2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP). Using `trade_type=8` returns 10023.

#### Modify Pending Order SL/TP — VERIFIED WORKING

```python
# Use trade_action=7 (ModifyOrder), original trade_type, and order ticket at trade_order (offset 84)
aid = random.randint(1, 0x7FFFFFFE)
op = bytearray(248)
struct.pack_into('<I', op, 0, aid)                      # action_id
struct.pack_into('<I', op, 4, 7)                        # trade_action = 7 (MODIFY_ORDER)
op[8:8+len('EURUSDm'.encode('utf-16-le'))] = 'EURUSDm'.encode('utf-16-le')
struct.pack_into('<Q', op, 72, 100000)                  # volume
struct.pack_into('<I', op, 80, 5)                       # digits
struct.pack_into('<Q', op, 84, order_ticket)            # trade_order = order ticket to modify ← NOT offset 228!
struct.pack_into('<I', op, 92, 2)                       # trade_type = original type (2=BUY_LIMIT)
struct.pack_into('<I', op, 96, 2)                       # type_filling = 2 (RETURN)
struct.pack_into('<d', op, 112, original_price)         # price_order = original limit/stop price
struct.pack_into('<d', op, 128, new_sl_price)           # new SL (0 to remove)
struct.pack_into('<d', op, 136, new_tp_price)           # new TP (0 to remove)
# Send as cmd_id=12 (TRADE)
```

**Key differences from position modify (trade_action=6):**
- `trade_action=7` (MODIFY_ORDER), not `6` (MODIFY_DEAL)
- Order ticket goes in `trade_order` (offset **84**), NOT `trade_position` (offset 228)
- Must include original `price_order` value

**Status**: VERIFIED WORKING — tested 2025-07-15

## 13. Get Positions (cmd_id=4)

**No payload needed.**

Response format:
```
[pos_count:uint32, positions × POS_SIZE, order_count:uint32, orders × ORDER_SIZE]
```

```python
POS_SCHEMA = [
    {'propType':17},          # position_id (int64)
    {'propType':17},          # trade_order (int64)
    {'propType':6},           # time_create (uint32)
    {'propType':6},           # time_update (uint32)
    {'propType':11,64},       # trade_symbol
    {'propType':6},           # trade_action (0=buy, 1=sell)
    {'propType':8},           # price_open
    {'propType':8},           # price_close
    {'propType':8},           # sl
    {'propType':8},           # tp
    {'propType':18},          # trade_volume (uint64 raw)
    {'propType':8},           # profit
    {'propType':8},           # rate_profit
    {'propType':8},           # rate_margin
    {'propType':8},           # commission
    {'propType':8},           # storage_
    {'propType':17},          # expert
    {'propType':17},          # expert_position_id
    {'propType':11,64},       # comment
    {'propType':8},           # contract_size
    {'propType':6},           # digits
    {'propType':6},           # digits_currency
    {'propType':6},           # magic
    {'propType':11,64},       # reason
    {'propType':3},           # time_create_ms
    {'propType':3},           # time_update_ms
]
POS_SIZE = 344
```

## 14. Get History — Deals, Orders, Closed Positions (cmd_id=5)

Payload: `[from:uint32, to:uint32]` (unix timestamps, 0=from beginning, 0=to now)

Response: `[deal_count:uint32, deals × DEAL_SIZE, order_count:uint32, orders × ORDER_SIZE]`

**IMPORTANT**: cmd_id=5 returns BOTH deals AND orders in a single response!

```python
payload = struct.pack('<II', 0, 0)  # from=0, to=0 (all history)
response = send(5, payload)
buf = response.res_body

# Parse deals
deal_count = struct.unpack_from('<I', buf, 0)[0]
off = 4
deals = []
for i in range(deal_count):
    deal = parse_deal(buf, off)
    deals.append(deal)
    off += 356  # DEAL_SIZE

# Parse orders (right after deals)
order_count = struct.unpack_from('<I', buf, off)[0]
off += 4
orders = []
for i in range(order_count):
    order = parse_order(buf, off)
    orders.append(order)
    off += ORDER_SIZE
```

### Deal Schema (xd) — 28 fields, 356 bytes each

```python
DEAL_SCHEMA = [
    {'propType':17},         # [0]  deal (int64) — deal ticket
    {'propType':11,'propLength':64},   # [1]  deal_id (UTF-16LE, 64B)
    {'propType':17},         # [2]  trade_order (int64) — order ticket
    {'propType':6},          # [3]  time_create (uint32) — unix seconds
    {'propType':6},          # [4]  time_update (uint32) — unix seconds
    {'propType':11,'propLength':64},   # [5]  trade_symbol (UTF-16LE)
    {'propType':6},          # [6]  trade_action (uint32) — 0=BUY, 1=SELL
    {'propType':6},          # [7]  entry (uint32) — 0=IN (open), 1=OUT (close)
    {'propType':8},          # [8]  price_open (float64)
    {'propType':8},          # [9]  price_close (float64)
    {'propType':8},          # [10] sl (float64)
    {'propType':8},          # [11] tp (float64)
    {'propType':18},         # [12] trade_volume (uint64) — lots × 100000
    {'propType':8},          # [13] profit (float64)
    {'propType':8},          # [14] rate_profit (float64)
    {'propType':8},          # [15] rate_margin (float64)
    {'propType':8},          # [16] commission (float64)
    {'propType':8},          # [17] storage_ (float64) — swap
    {'propType':17},         # [18] expert (int64)
    {'propType':17},         # [19] position_id (int64)
    {'propType':11,'propLength':64},   # [20] comment (UTF-16LE)
    {'propType':8},          # [21] contract_size (float64)
    {'propType':6},          # [22] digits (uint32)
    {'propType':6},          # [23] digits_currency (uint32)
    {'propType':6},          # [24] trade_reason (uint32)
    {'propType':3},          # [25] time_create_ms (int32) — milliseconds
    {'propType':3},          # [26] time_update_ms (int32) — milliseconds
    {'propType':8},          # [27] commission_fee (float64)
]
DEAL_SIZE = 356  # exact per JS: Ac.getSeriesSize(xd)
```

**Timestamp conversion** (from JS `Pd` function):
```python
time_create_ms = 1000 * time_create + time_create_ms
time_update_ms = 1000 * time_update + time_update_ms
```

### Order Schema (Wd) — 30 fields

Parses pending orders, limit/stop orders, and history orders.

```python
ORDER_SCHEMA = [
    {'propType':17},         # [0]  trade_order (int64) — order ticket
    {'propType':11,'propLength':64},   # [1]  order_id (UTF-16LE, 64B)
    {'propType':11,'propLength':64},   # [2]  trade_symbol (UTF-16LE, 64B)
    {'propType':6},          # [3]  time_setup (uint32) — unix seconds
    {'propType':6},          # [4]  time_expiration (uint32) — unix seconds
    {'propType':6},          # [5]  time_done (uint32) — unix seconds
    {'propType':6},          # [6]  order_type (uint32) — 0=BUY, 1=SELL, 2=BUY LIMIT, 3=SELL LIMIT, 4=BUY STOP, 5=SELL STOP
    {'propType':6},          # [7]  type_filling (uint32) — 0=FOK, 1=IOC, 2=RETURN
    {'propType':6},          # [8]  type_time (uint32) — 0=GTC, 1=DAY, 2=SPECIFIED
    {'propType':6},          # [9]  type_reason (uint32)
    {'propType':8},          # [10] price_order (float64) — limit/stop price
    {'propType':8},          # [11] price_trigger (float64) — stop-limit trigger
    {'propType':8},          # [12] price_current (float64) — current market price
    {'propType':8},          # [13] price_sl (float64) — stop loss
    {'propType':8},          # [14] price_tp (float64) — take profit
    {'propType':17},         # [15] volume_initial (int64) — original volume
    {'propType':17},         # [16] volume_current (int64) — remaining volume
    {'propType':6},          # [17] order_state (uint32) — 0=started, 2=canceled, 4=filled, 5=rejected
    {'propType':17},         # [18] expert (int64) — magic number
    {'propType':17},         # [19] position_id (int64) — linked position
    {'propType':11,'propLength':64},   # [20] comment (UTF-16LE, 64B)
    {'propType':8},          # [21] contract_size (float64)
    {'propType':6},          # [22] digits (uint32)
    {'propType':6},          # [23] digits_currency (uint32)
    {'propType':8},          # [24] commission_daily (float64)
    {'propType':8},          # [25] commission_monthly (float64)
    {'propType':8},          # [26] margin_rate (float64)
    {'propType':6},          # [27] activation_mode (uint32)
    {'propType':3},          # [28] time_setup_ms (int32) — milliseconds
    {'propType':3},          # [29] time_done_ms (int32) — milliseconds
]
# ORDER_SIZE = Ac.getSeriesSize(Wd) = 356 bytes per order
```

**Timestamp conversion** (from JS `Ld` function):
```python
time_setup = 1000 * time_setup + time_setup_ms
time_expiration *= 1000  # no ms field
time_done = 1000 * time_done + time_done_ms
```

**Order states**: 0=started, 2=canceled, 4=filled, 5=rejected

### Closed Positions (built from deals)

The web terminal builds closed position history by grouping deals by `position_id`:

```python
# From JS: kd.buildPositions() groups deals by position_id
# Each closed position has: open_time, open_price, close_time, close_price, profit, volume
# A position is "closed" when its close_volume > 0
```

## 15. Get Open Positions + Pending Orders (cmd_id=4)

**No payload needed.**

Response: `[pos_count:uint32, positions × POS_SIZE, order_count:uint32, orders × ORDER_SIZE]`

```python
response = send(4)
buf = response.res_body

# Parse positions (open market orders: BUY/SELL)
pos_count = struct.unpack_from('<I', buf, 0)[0]
off = 4
positions = []
for i in range(pos_count):
    pos = parse_position(buf, off)
    positions.append(pos)
    off += POS_SIZE  # 344

# Parse orders (pending: LIMIT/STOP/STOP-LIMIT)
order_count = struct.unpack_from('<I', buf, off)[0]
off += 4
orders = []
for i in range(order_count):
    order = parse_order(buf, off)
    orders.append(order)
    off += ORDER_SIZE
```

### Position Schema (uu) — 26 fields, 344 bytes each

```python
POS_SCHEMA = [
    {'propType':17},         # [0]  position_id (int64)
    {'propType':17},         # [1]  trade_order (int64)
    {'propType':6},          # [2]  time_create (uint32) — unix seconds
    {'propType':6},          # [3]  time_update (uint32) — unix seconds
    {'propType':11,'propLength':64},   # [4]  trade_symbol (UTF-16LE)
    {'propType':6},          # [5]  trade_action (uint32) — 0=BUY, 1=SELL
    {'propType':8},          # [6]  price_open (float64)
    {'propType':8},          # [7]  price_close (float64)
    {'propType':8},          # [8]  sl (float64)
    {'propType':8},          # [9]  tp (float64)
    {'propType':18},         # [10] trade_volume (uint64) — lots × 100000
    {'propType':8},          # [11] profit (float64)
    {'propType':8},          # [12] rate_profit (float64)
    {'propType':8},          # [13] rate_margin (float64)
    {'propType':8},          # [14] commission (float64)
    {'propType':8},          # [15] storage_ (float64) — swap
    {'propType':17},         # [16] expert (int64) — magic number
    {'propType':17},         # [17] expert_position_id (int64)
    {'propType':11,'propLength':64},   # [18] comment (UTF-16LE)
    {'propType':8},          # [19] contract_size (float64)
    {'propType':6},          # [20] digits (uint32)
    {'propType':6},          # [21] digits_currency (uint32)
    {'propType':6},          # [22] magic (uint32)
    {'propType':11,'propLength':64},   # [23] reason (UTF-16LE)
    {'propType':3},          # [24] time_create_ms (int32) — milliseconds
    {'propType':3},          # [25] time_update_ms (int32) — milliseconds
]
POS_SIZE = 344  # exact per JS: Ac.getSeriesSize(uu)
```

**Timestamp conversion** (from JS `mu` function):
```python
time_create_ms = 1000 * time_create + time_create_ms
time_update_ms = 1000 * time_update + time_update_ms
```

## 16. Server Push Notifications (cmd_id=10)

The server pushes real-time trade updates on cmd_id=10. This is how the terminal knows when orders fill, positions change, etc.

### Push Format

```python
# First 4 bytes: flag_mask (uint32)
flag_mask = struct.unpack_from('<I', buf, 0)[0]

if flag_mask == 0 or flag_mask == 1:
    # Order update (Yu schema) — 16 bytes
    Yu = [
        {'propType':6},   # flag_mask (0=open order, 1=history order)
        {'propType':6},   # transaction_id
        {'propType':6},   # transaction_type (0=add, 1=update, 2=delete)
        {'propType':6},   # trade_order (order ticket)
    ]

elif flag_mask == 2:
    # Deal + Position update (Uu schema) — complex nested
    Uu = [
        {'propType':6},   # flag_mask = 2
        {'propType':6},   # transaction_id
        {'propType':6},   # transaction_type (0=add, 1=update, 2=delete)
        {'propType':12, 'parser': Pd, 'propLength': 356},  # deal (full deal record)
        {'propType':12, 'parser': mu, 'propLength': 344},  # trade_position (full position)
        {'propType':8},   # balance
        {'propType':8},   # credit
        {'propType':8},   # commission_daily
        {'propType':8},   # commission_monthly
        {'propType':8},   # acc_profit
    ]
    # After Uu header: deal array + position array (same as cmd_id=5 response)
```

### Transaction Types

| flag_mask | Entity | transaction_type | Meaning |
|-----------|--------|-----------------|---------|
| 0 | Open Order | 0 | Order added/modified |
| 0 | Open Order | 1 | Order updated |
| 0 | Open Order | 2 | Order deleted (filled/canceled) |
| 1 | History Order | 0 | Order moved to history |
| 1 | History Order | 1 | History order updated |
| 1 | History Order | 2 | History order deleted |
| 2 | Deal/Position | 0 | Deal created + position opened/updated |
| 2 | Deal/Position | 1 | Position updated |
| 2 | Deal/Position | 2 | Deal/position deleted |

### How the Terminal Processes Push Notifications

```python
# From JS Qu class:
def on_push(buf):
    # Parse multiple records in one push frame
    flag = struct.unpack_from('<I', buf, 0)[0]
    offset = 0
    while offset < len(buf):
        flag = struct.unpack_from('<I', buf, offset)[0]
        if flag == 2:
            # Uu record: parse deal + position
            record = parse_Uu(buf, offset)
            offset += Uu_SIZE
            # Parse embedded deals
            deal_count = struct.unpack_from('<I', buf, offset)[0]
            offset += 4
            for _ in range(deal_count):
                deal = parse_deal(buf, offset)
                offset += 356
            # Parse embedded positions
            pos_count = struct.unpack_from('<I', buf, offset)[0]
            offset += 4
            for _ in range(pos_count):
                pos = parse_position(buf, offset)
                offset += 344
        else:
            # Yu record: simple order update
            record = parse_Yu(buf, offset)
            offset += 16
```

## 17. Heartbeat (cmd_id=51)

Empty payload. Server responds with cmd_id=51, empty body.
Send every 3-5 seconds to keep connection alive.

## 18. Get Historical Candles (cmd_id=11) — VERIFIED WORKING

### Payload

```
[Symbol:UTF-16LE(64B)] [Timeframe:uint16] [From:uint32] [To:uint32]  = 74 bytes
```

| Field | Size | Type | Description |
|-------|------|------|-------------|
| Symbol | 64B | UTF-16LE | Symbol name, null-padded (e.g. `EURUSDm`) |
| Timeframe | 2B | uint16 | MT5 timeframe constant (see table below) |
| From | 4B | int32 | Start time (unix seconds) |
| To | 4B | int32 | End time (unix seconds, usually `int(time.time())`) |

### Response

Raw stream of **48-byte candles** — no count prefix, no header. Just `body_size / 48` = number of candles.

```
[timestamp:i32] [open:f64] [high:f64] [low:f64] [close:f64] [volume:i64] [spread:i32]
  4 bytes         8 bytes    8 bytes    8 bytes    8 bytes      8 bytes      4 bytes
                                                                    = 48 bytes total
```

| Field | Offset | Size | Type | Description |
|-------|--------|------|------|-------------|
| timestamp | 0 | 4B | int32 | Unix timestamp (seconds) |
| open | 4 | 8B | float64 | Open price |
| high | 12 | 8B | float64 | High price |
| low | 20 | 8B | float64 | Low price |
| close | 28 | 8B | float64 | Close price |
| tick_volume | 36 | 8B | int64 | Tick volume |
| spread | 44 | 4B | int32 | Spread (points) |

### Python Parser

```python
import struct, datetime

CANDLE_SIZE = 48

def parse_candles(body):
    """Parse candle response: raw stream of 48-byte candles."""
    if len(body) < CANDLE_SIZE:
        return []
    num = len(body) // CANDLE_SIZE
    candles = []
    off = 0
    for _ in range(num):
        ts = struct.unpack_from('<i', body, off)[0]
        o = struct.unpack_from('<d', body, off+4)[0]
        h = struct.unpack_from('<d', body, off+12)[0]
        l = struct.unpack_from('<d', body, off+20)[0]
        c = struct.unpack_from('<d', body, off+28)[0]
        vol = struct.unpack_from('<q', body, off+36)[0]
        spread = struct.unpack_from('<i', body, off+44)[0]
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        candles.append({
            'time': dt, 'timestamp': ts,
            'open': o, 'high': h, 'low': l, 'close': c,
            'tick_volume': vol, 'spread': spread,
        })
        off += CANDLE_SIZE
    return candles
```

### MT5 Timeframe Constants

| Name | Value | Seconds/Candle | Max Candles/Request | Approx Range |
|------|-------|----------------|---------------------|--------------|
| M1 | 1 | 60 | ~7,182 | 5 days |
| M2 | 2 | 120 | ~7,182 | 10 days |
| M3 | 3 | 180 | ~7,182 | 15 days |
| M4 | 4 | 240 | ~7,182 | 20 days |
| M5 | 5 | 300 | ~6,328 | 22 days |
| M6 | 6 | 360 | ~6,328 | 27 days |
| M10 | 10 | 600 | ~6,328 | 44 days |
| M12 | 12 | 720 | ~6,328 | 53 days |
| M15 | 15 | 900 | ~14,051 | 146 days |
| M20 | 20 | 1200 | ~6,328 | 88 days |
| M30 | 30 | 1800 | ~6,146 | 128 days |
| H1 | 16385 | 3600 | ~6,216 | 259 days |
| H2 | 16386 | 7200 | ~6,216 | 518 days |
| H3 | 16387 | 10800 | ~6,216 | 777 days |
| H4 | 16388 | 14400 | ~3,216 | 536 days |
| H6 | 16390 | 21600 | ~3,216 | 804 days |
| H8 | 16392 | 28800 | ~3,216 | 1072 days |
| H12 | 16396 | 43200 | ~3,216 | 1608 days |
| D1 | 16408 | 86400 | ~1,562 | 4.3 years |
| W1 | 32769 | 604800 | ~521 | 10 years |
| MN1 | 49153 | 2592000 | ~120 | 10 years |

### Usage Examples

```python
import time

# Get last 100 M15 candles
now = int(time.time())
from_sec = now - 100 * 900  # 900s per M15 candle
await sc(ws, sk, 11, build_candles_payload('EURUSDm', 15, from_sec, now))

# Get M15 candles for specific date range
from datetime import datetime, timezone
from_dt = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
to_dt = datetime(2026, 7, 7, 23, 59, 0, tzinfo=timezone.utc)
await sc(ws, sk, 11, build_candles_payload('EURUSDm', 15, 
    int(from_dt.timestamp()), int(to_dt.timestamp())))

# Get last 5 years of daily candles
from_sec = now - 5 * 365 * 86400
await sc(ws, sk, 11, build_candles_payload('EURUSDm', 16408, from_sec, now))
```

### Key Notes
- **No count prefix** in response — just raw 48-byte candle stream
- **Datetime-based selection** — specify exact `from`/`to` unix timestamps
- **Server returns all data** in the range (up to ~14K candles per request)
- **Prices are actual values** (e.g., 1.14533), NOT raw
- **Weekend gaps** — No candles for Saturday/Sunday (market closed)
- **For >14K candles** — Split into multiple requests with overlapping date ranges

## 19. Full Flow: Trade Order → Position → Close — VERIFIED WORKING

```
┌─────────────────────────────────────────────────────────────────┐
│                    COMPLETE TRADE FLOW                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. SUBSCRIBE TO QUOTES                                         │
│     Client → Server: cmd_id=7, payload=[count, symbol_id[]]     │
│     Server → Client: cmd_id=8 (quotes, 50B each, price ticks)  │
│                     cmd_id=17 (symbol spec, auto-push)          │
│                                                                 │
│  2. GET CURRENT PRICE                                           │
│     From cmd_id=8 quote: bid=raw/10^digits, ask=raw/10^digits  │
│     BUY price = ask, SELL price = bid                           │
│                                                                 │
│  3. PLACE TRADE ORDER (cmd_id=12)                               │
│     Send: 248-byte Op (trade_action=3, symbol, volume, price)   │
│     Recv: cmd_id=19 TRADE_EVENT with retcode=10009 (success)   │
│     Note: Server sends TWO events: 10002 (ACK) then 10009      │
│                                                                 │
│  4. CHECK OPEN POSITIONS (cmd_id=4)                             │
│     Recv: [pos_count, positions×344B, order_count, orders]     │
│     Each position: id, symbol, buy/sell, volume, open price     │
│                                                                 │
│  5. CLOSE POSITION (cmd_id=12)                                  │
│     Send: 248-byte Op (trade_action=3, opposite type,           │
│           position_id, price=bid for closing BUY)               │
│     Recv: cmd_id=19 TRADE_EVENT with retcode=10009              │
│     Note: trade_action=10 (CLOSE) does NOT work!                │
│                                                                 │
│  6. CHECK DEAL HISTORY (cmd_id=5)                               │
│     Send: [from=unix_ts, to=unix_ts]                            │
│     Recv: [deal_count, deals×356B] — all closed trades          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 20. Full Initialization Sequence

```python
# 1. Auth
send(cmd=0, payload=bytes(64), key=STATIC_KEY)
session_key = response.body[66:]

# 2. Login
send(cmd=28, payload=login_912B, key=session_key)

# 3. Get account + symbols
send(cmd=3, key=session_key)     # → account data (6496B)
send(cmd=34, key=session_key)    # → gzipped symbols (gzip decompress body[4:])

# 4. Subscribe to quotes
symbol_ids = [sym_map[name]['id'] for name in ['EURUSDm', 'BTCUSDm']]
payload = struct.pack('<I', len(symbol_ids)) + struct.pack(f'<{len(symbol_ids)}I', *symbol_ids)
send(cmd=7, payload=payload, key=session_key)

# 5. Read quote stream
while True:
    frame = recv()
    if frame.cmd_id == 8:
        parse_quotes(frame.body)  # 50B per quote, prices raw / 10^digits
    elif frame.cmd_id == 17:
        pass  # symbol spec (server push after subscription)
    elif frame.cmd_id == 51:
        pass  # heartbeat
    
    # Send heartbeat every 3s
    if time.time() - last_hb > 3:
        send(cmd=51, key=session_key)

# 6. Place trade
send(cmd=12, payload=trade_order_248B, key=session_key)
# → response: retcode=0 (success)
# → server push: cmd_id=19 (TRADE_EVENT, 380B)

# 7. Check positions
send(cmd=4, key=session_key)     # → [pos_count, positions, order_count, orders]

# 8. Check deal history
send(cmd=5, payload=struct.pack('<II', 0, 0), key=session_key)  # → [deal_count, deals]
```

## 21. Python Dependencies

```bash
pip install websockets pycryptodome
```

## 22. Known Issues — UPDATED

### WORKING (verified):
1. **Market BUY/SELL (trade_action=3)** — Opens positions successfully with type_filling=0 (FOK)
2. **Position close (trade_action=3 + opposite type + position_id)** — Closes positions correctly
3. **Deal history (cmd_id=5)** — Full 356-byte deal schema parsed correctly
4. **Positions (cmd_id=4)** — 344-byte position schema parsed correctly
5. **Quotes (cmd_id=8)** — Live bid/ask prices, raw/10^digits
6. **Symbol spec (cmd_id=18)** — Full spec with trade_mode, volume_min etc.
7. **Historical candles (cmd_id=11)** — 48-byte candle format, all 9 timeframes verified (M1-MN1)
8. **Datetime-based candle range** — Select candles by exact from/to timestamps

### NOT YET WORKED:
8. **Quote delivery** — Quotes only arrive on actual market ticks; may be rate-limited on repeated reconnects

### ALL VERIFIED TRADE ACTIONS:
| Action | trade_action | Status | Notes |
|--------|-------------|--------|-------|
| Market Open | 3 | VERIFIED | BUY type=0, SELL type=1 |
| Market Close | 3 + opposite type | VERIFIED | Set trade_position=order_ticket |
| Pending Open | 5 | VERIFIED | type=2/3/4/5, type_filling=2 |
| Pending Cancel | 8 | VERIFIED | type=original type, trade_order=ticket |
| Position SL/TP Modify | 6 | VERIFIED | trade_position=order_ticket, sl/tp at offsets 128/136 |
| **Pending Order SL/TP Modify** | **7** | **VERIFIED** | **trade_order=ticket, sl/tp at offsets 128/136** |

### KEY DISCOVERIES:
- **CRITICAL: Position ID = ORDER ticket** — The `order` field from TRADE_EVENT is the position ID for modify AND close. NOT the `deal` field!
- **trade_action=3 (MARKET)** — For market open AND close. Use opposite trade_type + order_ticket to close
- **trade_action=5 (PENDING)** — For pending orders (LIMIT/STOP). NOT trade_action=1!
- **trade_action=6 (MODIFY_DEAL)** — For position SL/TP modify. Use order_ticket in trade_position field (offset 228)
- **trade_action=7 (MODIFY_ORDER)** — For pending order SL/TP modify. Use order_ticket in trade_order field (offset 84)
- **trade_action=8 (CANCEL)** — For canceling pending orders. NOT trade_action=10!
- **trade_action=10 (CLOSE) does NOT work** — Returns 10030. Use action=3 + opposite type
- **trade_action=201 (ForOrderPrice) does NOT work** — Returns 10030
- **type_filling for pending = 2 (RETURN)** — NOT 0 (FOK) as for market orders
- **Two TRADE_EVENTs per trade**: First is ACK (10002), second is result (10009)
- **EURUSDm trade_mode=4** (FULL trading), volume_min=1000000 (0.01 lots minimum)
- **volume encoding**: lots × 100000000 (e.g., 0.01 lots = 1000000)
- **Price in Op is actual price** (e.g., 1.14533), NOT raw (114533)
- **Pending price distance**: Must be 100+ _Point away from current price
- **cmd_id=4 position list may be stale** — Newly opened positions may not appear immediately; use order_ticket from TRADE_EVENT as authoritative position ID

### Order Schema (from hex dump analysis) — 356 bytes
| Offset | Size | Type | Field | Notes |
|--------|------|------|-------|-------|
| 0 | 8 | int64 | trade_order | Order ticket |
| 8 | 128 | UTF-16LE | order_id | Order ID string |
| 72 | 128 | UTF-16LE | symbol | e.g. "EURUSDm" |
| 136 | 8 | ??? | — | Unknown |
| 144 | 4 | uint32 | — | Unknown |
| 148 | 4 | uint32 | order_type | 0=BUY,1=SELL,2=BUY_LIMIT,3=SELL_LIMIT,4=BUY_STOP,5=SELL_STOP |
| 152 | 4 | uint32 | type_filling | 0=FOK, 1=IOC, 2=RETURN |
| 156 | 4 | uint32 | type_time | 0=GTC, 1=DAY, 2=SPECIFIED |
| 160 | 4 | uint32 | — | Unknown (0x11) |
| 164 | 8 | float64 | price_order | Limit/stop price |
| 172 | 8 | float64 | — | Unknown |
| 180 | 8 | float64 | price_current | Current market price |
| 188 | 8 | float64 | price_sl | Stop loss |
| 196 | 8 | float64 | price_tp | Take profit |
| 204 | 8 | int64 | volume_initial | Original volume |
| 212 | 8 | int64 | volume_current | Remaining volume |
| 220 | 4 | uint32 | order_state | 0=started, 2=canceled, 4=filled, 5=rejected |

---

## Stale Data & Bypass Parameters

### Problem

`cmd_id=4` (position/order list) does **not** update in real-time. After a market buy/sell:
- The new position may not appear in `get_positions()` immediately
- `close_position(ticket)` looks up the position from this stale list → raises `TradeError 10030`

### Solution: Bypass Parameters

The client methods `close_position`, `modify_position`, `partial_close`, and `cancel_order` accept optional bypass parameters. When provided, the client skips the stale list lookup and builds the trade command directly:

```python
# Instead of relying on stale list:
result = await client.buy("EURUSDm", 0.01)

# Use bypass params from the trade result:
await client.close_position(
    result.order,
    symbol="EURUSDm",  # from original trade
    pos_type=0,         # 0=BUY, 1=SELL
    volume=0.01         # from original trade
)
```

### Position Cache

The client maintains `_position_cache` (dict mapping ticket → Position) that gets populated from successful `buy()`/`sell()` results. When `close_position`/`modify_position` can't find a ticket in the stale cmd4 list, it falls back to this cache as a last resort.
