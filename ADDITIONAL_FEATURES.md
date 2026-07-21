# MT5 API Additional Features — Implementation Reference

## 1. Symbol Details from cmd_id=18

### SymbolInfo (1696 bytes, or 1952 for builds > 2097)

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0       8   int64   UpdateTime
  8      64   string  Currency (symbol name)
 72      32   string  ISIN
104     128   string  Description
232     128   string  (internal)
360      64   string  Basis
424      64   string  (internal)
488     512   string  RefToSite
1000     4   int32   Custom
1004    28   bytes   (padding)
1032    32   string  (internal)
1064    32   string  ProfitCurrency
1096    32   string  MarginCurrency
1128     4   int32   (internal)
1132     4   int32   Precision
1168     4   int32   Digits
1172     8   double  Points
1180     8   double  LimitPoints
1188     4   int32   Id (symbol_id)
1360     8   int64   Flags (bit 0 = inverse quote)
1368     4   int32   Spread
1376     8   double  TickValue
1384     8   double  TickSize
1392     8   double  ContractSize
1400     4   int32   GTCMode
1404     4   int32   CalcMode (0=Forex,1=Futures,2=CFD,3=CFDIndex,4=CFDLeverage,5=CalcMode5)
1416     8   double  SettlementPrice
1424     8   double  LowerLimit
1432     8   double  UpperLimit
1452     8   double  FaceValue
1460     8   double  AccruedInterest
1488     8   int64   FirstTradeTime
1496     8   int64   LastTradeTime
```

### SymGroup (1228 bytes)

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  8     256   string  GroupName
264      4   int32   DeviationRate
268      4   int32   RoundRate
304      4   int32   TradeMode
308      4   int32   SL
312      4   int32   TP
316      4   int32   TradeType (ExecutionType)
320      4   int32   FillPolicy
324      4   int32   Expiration
328      4   int32   OrderFlags
744      8   uint64  MinVolume
752      8   uint64  MaxVolume
760      8   int64   VolumeStep
836      8   double  InitialMargin
844      8   double  MaintenanceMargin
852     64   double  InitMarginRate[8]
916     64   double  MntnMarginRate[8]
988      8   double  HedgedMargin
1044     4   int32   SwapType
1048     8   double  SwapLong
1056     8   double  SwapShort
1064     4   int32   ThreeDaysSwap
```

---

## 2. Partial Close

**How it works:** Close a PORTION of an existing position (e.g., close 0.01 of a 0.10 lot position).

### C# Source (MT5API.cs line 1784-1842)

```csharp
// OrderCloseAsync — partial close uses same ticket, just less volume
tradeRequest.ByCloseTicket = closeByTicket;  // offset 244 (ByCloseTicket field)
tradeRequest._E019 = (ulong)Math.Round(lots * 100000000.0, 0);  // partial volume
tradeRequest.DealTicket = ticket;  // position ticket
tradeRequest.TradeType = (TradeType)(Symbols.GetGroup(symbol).TradeType + 1);  // MARKET
tradeRequest.OrderType = opposite_type;  // BUY→SELL, SELL→BUY
```

### Key Difference: Full Close vs Partial Close

| Parameter | Full Close | Partial Close |
|-----------|-----------|---------------|
| `lots` | Full volume (e.g., 0.10) | Partial volume (e.g., 0.01) |
| `ticket` | Position ORDER ticket | Position ORDER ticket |
| `trade_action` | 3 (MARKET) | 3 (MARKET) |
| `trade_type` | Opposite of position | Opposite of position |
| `type_filling` | 0 (FOK) | 0 (FOK) |

**Same payload** — just set `volume` to the partial amount you want to close.

### WebSocket Implementation

```python
async def partial_close(ws, sk, position_ticket, symbol, close_lots, position_type):
    """Close part of a position."""
    opposite_type = 1 if position_type == 0 else 0  # BUY(0)→SELL(1), SELL(1)→BUY(0)
    op = bytearray(248)
    struct.pack_into('<I', op, 0, random.randint(0, 65535))  # action_id
    struct.pack_into('<I', op, 4, 3)  # trade_action = MARKET
    op[8:72] = symbol.encode('utf-16-le')[:64]  # symbol
    struct.pack_into('<Q', op, 72, int(close_lots * 100000000))  # volume = partial
    struct.pack_into('<Q', op, 84, 0)  # trade_order
    struct.pack_into('<I', op, 92, opposite_type)  # trade_type (opposite)
    struct.pack_into('<I', op, 96, 0)  # type_filling = FOK
    struct.pack_into('<Q', op, 228, position_ticket)  # trade_position = ORDER ticket
    # Send with cmd_id=12, trade_action=3
```

---

## 3. Modify Pending Price

**How it works:** Change the price of a pending order (not just SL/TP).

### C# Source (MT5API.cs line 1952-1984)

```csharp
// OrderModifyAsync — for pending orders
tradeRequest.Price = price;  // offset 112 (price_order) — NEW pending price
tradeRequest.OrderPrice = stoplimit;  // offset 120 (price_trigger) — for stop-limit
tradeRequest.StopLoss = sl;  // offset 128
tradeRequest.TakeProfit = tp;  // offset 136

if (type == OrderType.Buy || type == OrderType.Sell)
{
    tradeRequest.DealTicket = ticket;  // position ticket
    tradeRequest.TradeType = TradeType.ModifyDeal;  // action=6
}
else
{
    tradeRequest.OrderTicket = ticket;  // pending order ticket
    tradeRequest.TradeType = TradeType.ModifyOrder;  // action=7
}
```

### Key Point: trade_action=7 modifies BOTH price AND SL/TP

| Field | Offset | Purpose |
|-------|--------|---------|
| `price_order` | 112 | New pending price |
| `price_trigger` | 120 | Stop-limit trigger price |
| `sl` | 128 | Stop loss |
| `tp` | 136 | Take profit |
| `trade_order` | 84 | Order ticket |

### WebSocket Implementation

```python
async def modify_pending_price(ws, sk, order_ticket, symbol, new_price, sl=0, tp=0):
    """Modify pending order price + SL/TP."""
    op = bytearray(248)
    struct.pack_into('<I', op, 0, random.randint(0, 65535))  # action_id
    struct.pack_into('<I', op, 4, 7)  # trade_action = MODIFY_ORDER
    op[8:72] = symbol.encode('utf-16-le')[:64]  # symbol
    struct.pack_into('<Q', op, 84, order_ticket)  # trade_order = ticket
    struct.pack_into('<d', op, 112, new_price)  # price_order = NEW price
    struct.pack_into('<d', op, 128, sl)  # price_sl
    struct.pack_into('<d', op, 136, tp)  # price_tp
    struct.pack_into('<I', op, 96, 2)  # type_filling = RETURN
    # Send with cmd_id=12, trade_action=7
```

---

## 4. Timeframe Conversion

### ConvertToTimeframe (MT5API.cs line 3614-3748)

**Algorithm:**
1. Find first bar aligned to timeframe boundary
2. For each subsequent bar:
   - If bar.Time >= next boundary: close current bar, start new one
   - Else: aggregate into current bar (update OHLCV)
3. OHLCV aggregation:
   - Open = first bar's Open
   - High = max of all bars' High
   - Low = min of all bars' Low
   - Close = last bar's Close
   - Volume = sum of all bars' Volume
   - TickVolume = sum of all bars' TickVolume

**Timeframe boundaries:**
```python
def get_boundary(dt, minutes):
    if minutes >= 43200:  # Monthly
        return datetime(dt.year, dt.month, 1)
    if minutes >= 10080:  # Weekly
        day_of_week = dt.weekday()
        return dt - timedelta(days=day_of_week)
    if minutes >= 1440:  # Daily
        return datetime(dt.year, dt.month, dt.day)
    # Intraday
    aligned_minute = (dt.hour * 60 + dt.minute) // minutes * minutes
    return dt.replace(hour=aligned_minute // 60, minute=aligned_minute % 60, second=0)
```

### ConvertToW1FromDaily (MT5API.cs line 3770-3830)

**Algorithm:** Group daily bars by week (Monday-Sunday).

### ConvertToMNFromDaily (MT5API.cs line 3832-3892)

**Algorithm:** Group daily bars by month.

### Python Implementation

```python
def convert_to_timeframe(bars, minutes):
    """Convert 1-minute bars to higher timeframe."""
    if minutes == 1:
        return bars
    
    result = []
    current_bar = None
    next_boundary = None
    
    for bar in bars:
        if current_bar is None:
            current_bar = {
                'time': get_boundary(bar['time'], minutes),
                'open': bar['open'], 'high': bar['high'],
                'low': bar['low'], 'close': bar['close'],
                'volume': bar.get('volume', 0),
                'tick_volume': bar.get('tick_volume', 0),
            }
            next_boundary = current_bar['time'] + timedelta(minutes=minutes)
        
        if bar['time'] >= next_boundary:
            result.append(current_bar)
            current_bar = {
                'time': get_boundary(bar['time'], minutes),
                'open': bar['open'], 'high': bar['high'],
                'low': bar['low'], 'close': bar['close'],
                'volume': bar.get('volume', 0),
                'tick_volume': bar.get('tick_volume', 0),
            }
            next_boundary = current_bar['time'] + timedelta(minutes=minutes)
        else:
            current_bar['high'] = max(current_bar['high'], bar['high'])
            current_bar['low'] = min(current_bar['low'], bar['low'])
            current_bar['close'] = bar['close']
            current_bar['volume'] += bar.get('volume', 0)
            current_bar['tick_volume'] += bar.get('tick_volume', 0)
    
    if current_bar:
        result.append(current_bar)
    
    return result
```

---

## 5. Profit/Margin Calculations

### UpdateAccountProfit (MT5API.cs line 1326-1353)

```python
def calculate_account_profit(orders):
    """Sum profit + commission + swap for all open orders."""
    total = 0.0
    for order in orders:
        total += order.profit + order.commission + order.swap
    return total
```

### CalculateOrderProfit (MT5API.cs line 1530-1557)

```python
def calculate_order_profit(symbol, open_price, close_price, lots, buy):
    """Calculate profit for a single order."""
    # Create internal order, call profit calculator
    # The actual formula depends on CalcMode:
    # Forex: profit = (close - open) * lots * contract_size * direction
    # CFD: profit = (close - open) * lots * contract_size * direction
    # Futures: profit = (close - open) * lots * tick_value / tick_size * direction
```

### RequiredMargin (MT5API.cs line 1433-1528)

```python
async def required_margin(symbol, lots, deal_type='buy', price=0):
    """Calculate required margin for a position."""
    if price == 0:
        quote = await get_quote(symbol)
        price = quote.ask if deal_type == 'buy' else quote.bid
    
    info = get_symbol_info(symbol)
    group = get_symbol_group(symbol)
    
    # Volume rate calculation
    if symbol.startswith(account_currency):
        volume_rate = 1.0
    elif info.calc_mode == 4:  # CFDLeverage
        volume_rate = 1.0
    elif account_currency == info.margin_currency:
        volume_rate = 1.0
    else:
        # Find cross rate
        cross_symbol = find_cross_symbol(account_currency, info.margin_currency)
        cross_quote = await get_quote(cross_symbol)
        volume_rate = cross_quote.bid  # or 1/bid depending on direction
    
    # Margin = lots * contract_size * price * volume_rate / leverage
    margin = lots * info.contract_size * price * volume_rate
    margin /= account_leverage
    
    if account_currency in ('JPY', 'XXX'):  # JPY accounts
        margin *= 100
    
    return margin
```

---

## 6. Session Queries

### IsQuoteSession / IsTradeSession (MT5API.cs line 4526-4548)

```python
def is_quote_session(symbol, server_time):
    """Check if quotes are available for symbol."""
    sessions = symbol_sessions[symbol].quotes
    day_sessions = sessions[server_time.weekday()]  # 0=Sun..6=Sat
    
    for session in day_sessions:
        minutes = server_time.hour * 60 + server_time.minute
        if session.start_time < minutes < session.end_time:
            return True
    return False

def is_trade_session(symbol, server_time):
    """Check if trading is active for symbol."""
    sessions = symbol_sessions[symbol].trades
    day_sessions = sessions[server_time.weekday()]
    
    for session in day_sessions:
        minutes = server_time.hour * 60 + server_time.minute
        if session.start_time < minutes < session.end_time:
            return True
    return False
```

### Session Data Source

Sessions come from cmd_id=18 response (SymbolSessions structure):
- 7 days × (quote_count + N×40 bytes + trade_count + M×40 bytes)
- Each session record: `[start_time:i32][end_time:i32] + 32 bytes padding`

---

## 7. Contract Size

**Already available** from cmd_id=18 SymbolInfo at offset 1392.

```python
contract_size = symbol_info['contract_size']  # e.g., 100000 for EURUSD
```

---

## 8. Tick Size / Tick Value

**Already available** from cmd_id=18 SymbolInfo:
- TickSize at offset 1384
- TickValue at offset 1376

```python
tick_size = symbol_info['tick_size']  # e.g., 0.00001 for EURUSD
tick_value = symbol_info['tick_value']  # e.g., 1.0 for EURUSD (per lot)
```

### Tick Value Calculation by CalcMode

```python
def get_tick_value(symbol, quote, side='bid'):
    info = get_symbol_info(symbol)
    
    if info.calc_mode in (1, 33, 36):  # Futures, ExchangeFutures, ExchangeMarginOption
        return info.tick_value  # Direct value
    
    if info.calc_mode == 0:  # Forex
        if info.flags & 1:  # Inverse quote
            return info.contract_size / quote.bid if side == 'bid' else info.contract_size / quote.ask
        return info.contract_size / quote.bid if side == 'bid' else info.contract_size / quote.ask
    
    return info.contract_size  # Default for CFD
```

---

## 9. Server List Parsing (servers.dat)

### Overview
`servers.dat` is a binary file used by MT5 terminals containing a list of all available broker servers and their IP address mappings.

### File Location
| Operating System | Path |
|-----------------|------|
| Windows | `%APPDATA%\MetaQuotes\Terminal\<hash>\config\servers.dat` |
| Linux/Wine | `~/.wine/drive_c/users/<user>/AppData/Roaming/MetaQuotes/Terminal/<hash>/config/servers.dat` |
| macOS | `~/Library/Application Support/MetaQuotes/Terminal/<hash>/config/servers.dat` |

### How to Get servers.dat

**Important: servers.dat cannot be downloaded directly from the internet!**

This file is automatically generated and updated by the MT5 terminal:

1. **Install MT5 Terminal**
   - Download MT5 installer from your broker's website (e.g., Exness.com)
   - Run MT5 after installation

2. **Automatic Download on First Run**
   - MT5 connects to `updates.metaquotes.net` on startup
   - Automatically downloads the latest server list
   - File is saved to the path above

3. **Automatic Updates**
   - MT5 checks for updates each time it starts
   - If the server list has changed, it automatically updates the file
   - You can also trigger manually: Tools → Options → Servers → Click "Update"

4. **Copy from Existing Installation**
   - If you already have MT5 installed, you can directly copy the servers.dat file
   - The format is the same across different brokers

### Encryption
- Uses the same XOR chain cipher as the WebSocket protocol
- **Header (428 bytes) is NOT encrypted**
- **Server count fields are NOT encrypted**
- **All server records ARE encrypted** (each block decrypted individually with prev=0)

### Data Structure

#### DatHeader (428 bytes)

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0       4   uint32  Id (503/504/505/506)
  4     128   string  Copyright
132      32   string  DataType
164       8   int64   FileTime
172       4   int32   ObjNumber (server count)
176      16   byte[]  Md5Key
192     228   byte[]  (padding)
420       4   int32   (internal)
424       4   int32   (internal)
```

### ServerInfo (660 bytes) — for Id=503/504

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0     128   string  ServerName
128     256   string  CompanyName
384       4   int32   (internal)
388       4   int32   (internal)
392       4   int32   DST
396       4   int32   TimeZone
400       4   int32   (internal)
404     128   string  Address
532       4   int32   PingTime
536       4   int32   (internal)
540       4   int32   (internal)
544     116   byte[]  (padding)
```

### ServerInfoEx (1716 bytes) — for Id=505/506

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0     128   string  ServerName
128     256   string  CompanyName
384       4   int32   (internal)
388       4   int32   (internal)
392       4   int32   DST
396       4   int32   TimeZone
400       4   int32   (internal)
404     128   string  Address
532       4   int32   PingTime
536       4   int32   (internal)
540       4   int32   (internal)
544     116   byte[]  (padding)
660       4   int32   (internal)
664       4   int32   (internal)
668       8   int64   (internal)
676       8   int64   (internal)
684     512   string  CompanyLink
1196    512   string  (internal)
1708      8   int64   (internal)
```

### AccessRec (356 bytes)

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0      64   string  ServerName
 64     128   byte[]  (internal)
192       4   int32   (internal)
196       4   int32   (internal) — port number (default 2177)
200     156   byte[]  (padding)
```

### AddressRec (148 bytes)

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0     128   string  Address (IP:port, e.g. "13.213.81.113:443")
128       4   int32   (internal)
132       4   int32   (internal)
136       4   int32   (internal)
140       4   int32   (internal)
144       4   int32   (internal)
```

**Note**: The Address field contains the IP address AND port together (e.g., "13.213.81.113:443"). The int32 fields after the address are internal and not needed for connection.

### AccessRecEx (3160 bytes) — for Id=505/506

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0     128   string  (internal)
128     128   string  (internal)
256     256   string  (internal)
512      64   string  ServerName
576      24   byte[]  (internal)
600     256   string  Host (e.g. "*.exwebterm.com")
856    2048   string  Path (e.g. "/terminal")
2904    256   byte[]  (internal)
```

### AddressRecEx (1284 bytes)

```
OFFSET  SIZE  TYPE     FIELD
------  ----  ------  ------
  0       4   int32   Type
  4     512   string  Address
516     512   string  Description
1028    256   byte[]  (internal)
```

### Parsing Algorithm

```python
import struct

XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45, 123, 57, 8, 254, 33, 187, 65, 88])

def xor_decrypt_block(data):
    """XOR chain decrypt a block (each block starts with prev=0)."""
    result = bytearray(data)
    prev = 0
    for i in range(len(result)):
        current = result[i]
        result[i] ^= ((prev + XOR_KEY[i & 0xF]) & 0xFF)
        prev = current
    return bytes(result)

def read_utf16_string(data, offset, max_bytes):
    """Read a UTF-16LE string, stopping at null terminator."""
    raw = data[offset:offset+max_bytes]
    for i in range(0, len(raw), 2):
        if raw[i:i+2] == b'\x00\x00':
            raw = raw[:i]
            break
    return raw.decode('utf-16-le', errors='ignore')

def parse_servers_dat(filepath):
    """Parse servers.dat and return list of servers."""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    pos = 0
    
    # Header (428 bytes) - NOT encrypted
    header_id = struct.unpack_from('<I', data, pos)[0]; pos += 4
    copyright = read_utf16_string(data, pos, 128); pos += 128
    data_type = read_utf16_string(data, pos, 32); pos += 32
    file_time = struct.unpack_from('<q', data, pos)[0]; pos += 8
    obj_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
    pos += 16 + 228 + 4 + 4  # md5_key + padding + internals
    
    servers = []
    for _ in range(obj_count):
        server = {}
        
        if header_id in (505, 506):
            # ServerInfoEx (1716 bytes) - encrypted
            server_info = xor_decrypt_block(data[pos:pos+1716]); pos += 1716
            server['name'] = read_utf16_string(server_info, 0, 128)
            server['company'] = read_utf16_string(server_info, 128, 256)
            server['address'] = read_utf16_string(server_info, 404, 128)
        else:
            # ServerInfo (660 bytes) - encrypted
            server_info = xor_decrypt_block(data[pos:pos+660]); pos += 660
            server['name'] = read_utf16_string(server_info, 0, 128)
            server['company'] = read_utf16_string(server_info, 128, 256)
            server['address'] = read_utf16_string(server_info, 404, 128)
        
        # AccessRec count (NOT encrypted)
        access_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
        server['accesses'] = []
        for _ in range(access_count):
            access = {}
            # AccessRec (356 bytes) - encrypted
            access_data = xor_decrypt_block(data[pos:pos+356]); pos += 356
            access['name'] = read_utf16_string(access_data, 0, 64)
            
            # AddressRec count (NOT encrypted)
            addr_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
            access['addresses'] = []
            for _ in range(addr_count):
                # AddressRec (148 bytes) - encrypted
                addr_data = xor_decrypt_block(data[pos:pos+148]); pos += 148
                access['addresses'].append(read_utf16_string(addr_data, 0, 128))
            
            server['accesses'].append(access)
        
        # AccessRecEx count (NOT encrypted) - only for Id=505/506
        if header_id in (505, 506):
            accessex_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
            server['accesses_ex'] = []
            for _ in range(accessex_count):
                # AccessRecEx (3160 bytes) - encrypted
                pos += 3160
                
                # AddressRecEx count (NOT encrypted)
                addrex_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
                for _ in range(addrex_count):
                    # AddressRecEx (1284 bytes) - encrypted
                    pos += 1284
        
        servers.append(server)
    
    return servers
```

### Usage Example

```python
from parse_servers_dat import parse_servers_dat

# Parse servers.dat
servers = parse_servers_dat("/path/to/servers.dat")

# Find a specific server
for s in servers:
    if s['name'] == 'Exness-MT5Trial17':
        print(f"Server: {s['name']}")
        print(f"Company: {s['company']}")
        for a in s.get('accesses', []):
            for addr in a['addresses']:
                print(f"  - {addr}")  # e.g. "13.213.81.113:443"
```

### How to Obtain servers.dat

**Important: servers.dat cannot be downloaded directly from the internet!**

The file is automatically generated and updated by the MT5 terminal:

1. **Install MT5 Terminal**
   - Download from your broker's website (e.g., Exness.com)
   - Install and run MT5

2. **Automatic Download on First Run**
   - MT5 connects to `updates.metaquotes.net` on startup
   - Downloads the latest server list automatically
   - Saves to the path above

3. **Automatic Updates**
   - MT5 checks for updates each time it starts
   - You can also trigger manually: Tools → Options → Servers → "Update"

4. **Copy from Existing Installation**
   - The format is the same across all brokers
   - Just copy servers.dat from another MT5 installation

### Connection Priority

When connecting to a broker, MT5 uses servers.dat in this order:

1. **Desktop binary**: Connect via TCP to IP:port from servers.dat (e.g., `15.206.31.153:443`)
2. **Web terminal**: Connect via WSS to the same IP (e.g., `wss://15.206.31.153:443/terminal`)
3. **Mobile app**: Same as desktop binary

The IP addresses in servers.dat are the actual broker servers. No DNS resolution needed - connect directly to the IP.

## 10. Broker Search API — Online Server Resolution

### Overview

MT5 desktop clients use `Broker.Search()` and `Broker.SearchMQ()` to resolve server names to IP addresses at runtime. These HTTP APIs are called by the C# MT5API DLL when a user provides a server name instead of an IP address.

Both APIs are implemented in `broker_search.py`.

### API Endpoints (decrypted from mt5api.dll via .NET reflection)

| API | Method | URL | Auth |
|---|---|---|---|
| Search | GET | `http://search.mtapi.io/Search?company={name}&mt5=true` | None |
| SearchMQ | POST | `https://updates.metaquotes.net/public/mt5/network` | HMAC + Cookie |

**Note**: `search.mtapi.io` (not `search.mt5api.io`) — the domain was decrypted from the C# binary.

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

### Other Decrypted Endpoints

| Endpoint | URL | Purpose |
|----------|-----|---------|
| LoginId | `http://loginid-mt5.mtapi.io` | Login ID resolution |
| API Key | `5a5a59f3-c4a1-4150-91b0-f823427ad3ca` | MT5API key |
| User-Agent | `MetaTrader 5 Terminal/5.{build} (Windows NT 10.0.22621; x64)` | Terminal identification |

### Usage

```python
from broker_search import search, search_mq, find_server, find_server_ips

# SearchMQ (MetaQuotes official endpoint)
results = search_mq("Exness-MT5Trial17")

# Search (simpler, no auth)
results = search("Exness")

# Find specific server
info = find_server("Exness-MT5Trial17")
# → {"company": "Exness Technologies Ltd", "access": ["13.213.81.113:443", ...]}

# Just IPs
ips = find_server_ips("Exness-MT5Trial17")
# → ["13.213.81.113:443", "16.78.218.32:443", ...]
```

### Comparison: Online API vs Local servers.dat

| Feature | Online API (broker_search.py) | Local servers.dat |
|---------|-------------------------------|-------------------|
| Requires MT5 installation | No | Yes |
| Always up-to-date | Yes | Needs periodic updates |
| Includes company info | Yes | No |
| Includes logo/site | Yes | No |
| Works offline | No | Yes |
| Speed | Network latency | Instant |

### Server Name Resolution in ws_client.py

`find_server_ip()` in `ws_client.py` uses a 3-tier fallback:

1. **Online APIs** (SearchMQ → Search via `broker_search.py`)
2. **Local servers.dat** (if available)
3. **Return None** (not found)
