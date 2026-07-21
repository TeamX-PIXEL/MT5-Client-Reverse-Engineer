# BUILD.md — Clean Client Library Plan

## Goal

Package all verified MT5 operations into a single reusable `mt5client/` Python async package.

## Current State

| File | Lines | Status |
|------|-------|--------|
| `ws_client.py` | 736 | Working but monolithic, hardcoded values, mixed concerns |
| `broker_search.py` | 170 | Working — SearchMQ + Search APIs |
| 50+ test scripts | — | Scattered in directory, not reusable |

## Target API

```python
from mt5client import MT5Client

async with MT5Client(
    login=463558919,
    password="Trade@123",
    server="Exness-MT5Trial17"   # auto-resolves to IP
) as client:
    account = await client.get_account()
    symbols = await client.get_symbols()
    quote = await client.subscribe("EURUSDm")
    order = await client.buy("EURUSDm", 0.01, sl=1.08, tp=1.10)
    positions = await client.get_positions()
    candles = await client.get_candles("EURUSDm", "M1", 100)
    await client.close_position(order.ticket)
    await client.cancel_order(pending_ticket)
    await client.modify_position(order.ticket, sl=1.09, tp=1.11)
```

---

## Module Structure

```
mt5client/
├── __init__.py          # Export MT5Client + models
├── client.py            # Main MT5Client class (async context manager)
├── connection.py        # WebSocket + TCP layer (send/recv with lock)
├── crypto.py            # AES-256-CBC encrypt/decrypt, XOR chain cipher
├── protocol.py          # Wire format, command builder, response parser
├── models.py            # Dataclasses: Account, Symbol, Quote, Position, Order, Deal, Candle
├── search.py            # Broker Search API (SearchMQ + Search)
└── exceptions.py        # MT5Error, AuthError, TradeError
```

---

## C# → Python asyncio Mapping

| C# Pattern | Python asyncio Equivalent | Why |
|------------|---------------------------|-----|
| `SemaphoreSlim(1,1)` | `asyncio.Lock()` | Only one coroutine sends at a time |
| `Task.Run(() => ...)` | `asyncio.create_task(...)` | Background task |
| `ConcurrentDictionary<K,V>` | `dict` | Single-threaded, no concurrent needed |
| `CancellationToken` | `asyncio.Event()` | Graceful shutdown signal |
| `Task.WhenAny(task, Task.Delay(ms))` | `asyncio.wait_for(task, timeout)` | Timeout pattern |
| `ManualResetEvent` | `asyncio.Event()` | Wait/notify pattern |
| `Interlocked.CompareExchange` | Not needed | asyncio is single-threaded |
| `while (!cancelled)` | `while not cancelled.is_set()` | Receive loop |

### Key Insight from C# Code

The C# MT5API uses:
1. **Single receive loop** (`_E020._E000()`) — runs in `Task.Run()`, processes ALL incoming messages
2. **SemaphoreSlim for send** (`_E022.m__E00C`) — ensures one send at a time
3. **ConcurrentDictionary for pending** (`_E020.m__E005`) — matches responses to requests by ID
4. **Interlocked for events** — thread-safe event handler registration (not needed in asyncio)

Python asyncio simplifies this because everything runs in a single thread with cooperative scheduling.

---

## Implementation Steps

### Step 1: `models.py` — Dataclasses

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Account:
    balance: float
    equity: float
    margin: float
    free_margin: float
    currency: str
    group: str
    leverage: int
    server: str
    profit: float
    login: int

@dataclass
class Symbol:
    name: str
    id: int
    digits: int
    point: float
    spread: int
    trade_mode: int  # 0=disabled, 4=full
    contract_size: float
    tick_value: float
    tick_size: float
    calc_mode: int
    min_volume: int
    max_volume: int
    volume_step: int
    swap_long: float
    swap_short: float
    initial_margin: float
    maintenance_margin: float

@dataclass
class Quote:
    symbol: str
    symbol_id: int
    bid: float
    ask: float
    raw_bid: int
    raw_ask: int
    time: datetime

@dataclass
class Position:
    ticket: int           # ORDER ticket (not deal!)
    symbol: str
    type: int             # 0=BUY, 1=SELL
    volume: float
    price: float
    sl: float
    tp: float
    profit: float
    swap: float
    commission: float
    comment: str
    time: datetime

@dataclass
class Order:
    ticket: int
    symbol: str
    type: int             # 0=BUY_LIMIT, 1=SELL_LIMIT, 2=BUY_STOP, 3=SELL_STOP, ...
    status: int
    volume: float
    price: float
    sl: float
    tp: float
    comment: str
    time: datetime

@dataclass
class Deal:
    ticket: int
    order: int            # ORDER ticket
    position: int         # Position ticket
    symbol: str
    type: int             # 0=BUY, 1=SELL
    volume: float
    price: float
    profit: float
    commission: float
    swap: float
    comment: str
    time: datetime

@dataclass
class Candle:
    time: datetime
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int

@dataclass
class TradeResult:
    retcode: int
    deal: int
    order: int
    volume: float
    price: float
    comment: str
```

### Step 2: `exceptions.py` — Exception Hierarchy

```python
class MT5Error(Exception):
    """Base exception for MT5 client"""
    pass

class AuthError(MT5Error):
    """Authentication failed"""
    pass

class TradeError(MT5Error):
    """Trade operation failed"""
    def __init__(self, retcode, message=""):
        self.retcode = retcode
        super().__init__(f"Trade error {retcode}: {message}")

class ServerNotFoundError(MT5Error):
    """Server name not found"""
    pass

class ConnectionError(MT5Error):
    """WebSocket connection failed"""
    pass

class TimeoutError(MT5Error):
    """Operation timed out"""
    pass
```

### Step 3: `crypto.py` — Encryption

```python
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

def aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    pad_len = 16 - (len(plaintext) % 16)
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(
        plaintext + bytes([pad_len] * pad_len)
    )

def aes_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    pt = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    p = pt[-1]
    return pt[:-p] if 1 <= p <= 16 and all(b == p for b in pt[-p:]) else pt

# XOR chain cipher (for servers.dat, same as WebSocket)
XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45,
                 123, 57, 8, 254, 33, 187, 65, 88])

def xor_encrypt(data: bytes) -> bytes:
    result = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        result[i] = b ^ (prev + XOR_KEY[i & 0xF]) & 0xFF
        prev = b
    return bytes(result)

def xor_decrypt(data: bytes) -> bytes:
    result = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        result[i] = b ^ (prev + XOR_KEY[i & 0xF]) & 0xFF
        prev = result[i]
    return bytes(result)
```

### Step 4: `protocol.py` — Wire Format

```python
import struct, random

# Response format: [tag:2][cmd_id:2 LE][res_code:1][res_body:N]
# Command format:  [random:2][cmd_id:2 LE][payload:N]
# Wire format:     [length:4 LE][version:4 LE][encrypted_data:N]

def build_command(cmd_id: int, payload: bytes = b'') -> bytes:
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0, 255)
    cmd[1] = random.randint(0, 255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload:
        cmd[4:] = payload
    return bytes(cmd)

def pack_data(cmd_id: int, encrypted: bytes) -> bytes:
    return struct.pack('<II', len(encrypted), 1) + encrypted

def parse_response(data: bytes) -> dict | None:
    if len(data) < 5:
        return None
    return {
        'cmd_id': struct.unpack_from('<H', data, 2)[0],
        'res_code': data[4],
        'res_body': data[5:],
    }

def pack_op(
    symbol: str, action_id: int, trade_action: int,
    volume: int, digits: int, trade_type: int,
    price: float, sl: float, tp: float,
    trade_order: int = 0, type_filling: int = 0,
    type_time: int = 0, type_flags: int = 2,
    comment: str = '', trade_position: int = 0,
) -> bytes:
    op = bytearray(248)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, trade_action)
    sym_bytes = symbol.encode('utf-16-le')
    op[8:8+len(sym_bytes)] = sym_bytes
    struct.pack_into('<Q', op, 72, volume)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<Q', op, 84, trade_order)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, type_filling)
    struct.pack_into('<I', op, 100, type_time)
    struct.pack_into('<I', op, 104, type_flags)
    struct.pack_into('<I', op, 108, 0)  # type_reason
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<d', op, 120, 0)  # price_trigger
    struct.pack_into('<d', op, 128, sl)
    struct.pack_into('<d', op, 136, tp)
    struct.pack_into('<I', op, 144, 0)  # price_deviation
    struct.pack_into('<Q', op, 228, trade_position)
    comment_bytes = comment.encode('utf-16-le')[:64]
    op[164:164+len(comment_bytes)] = comment_bytes
    return bytes(op)

# Schemas for parsing
FL_SCHEMA = [...]  # Account schema (26 fields)
MH_SCHEMA = [...]  # Symbol schema
POS_SCHEMA = [...] # Position schema
QUOTE_SCHEMA = [...] # Quote schema
```

### Step 5: `search.py` — Broker Search API

```python
import hashlib
import json
import time
import requests

HMAC_KEY = bytes([61, 123, 21, 22, 214, 234, 187, 52, 217, 214,
                  99, 227, 98, 62, 27, 215, 251, 220, 174, 244,
                  87, 59, 223, 53, 127, 168, 207, 11, 190, 173,
                  146, 127])

SEARCH_URL = "http://search.mtapi.io/Search?company={company}&mt5=true"
SEARCHMQ_URL = "https://updates.metaquotes.net/public/mt5/network"

def _compute_signature(body: str) -> str:
    body_hash = hashlib.md5(body.encode('latin-1')).digest()
    return hashlib.md5(body_hash + HMAC_KEY).hexdigest()

def _generate_cookie() -> str:
    ts = int(time.time())
    ms = int(time.monotonic_ns() // 1_000_000) & 0x1FFFFFF
    val = ((ts - 1420070400) | (ms << 32)) | 0x4200000000000000
    seed = int(time.time_ns() & 0xFFFFFFFF)
    rand = bytearray(256)
    for i in range(256):
        seed = (seed * 214013 + 2531011) & 0xFFFFFFFF
        rand[i] = (seed >> 16) & 0xFF
    md5 = bytearray(hashlib.md5(bytes(rand)).digest())
    md5[0] = 0
    for j in range(1, 16):
        md5[0] = (md5[0] + md5[j]) & 0xFF
    tid = md5[:8].hex().upper()[:17]
    return f"_fz_uniq={val};uniq={val};age={ts-86400};tid={tid}"

def search_mq(company: str) -> list:
    body = f"company={company}&code=mt5"
    sig = _compute_signature(body)
    full_body = f"{body}&signature={sig}&ver=2"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en",
        "User-Agent": "MetaTrader 5 Terminal/5.5830 (Windows NT 10.0.22621; x64)",
        "Cookie": _generate_cookie(),
    }
    resp = requests.post(SEARCHMQ_URL, data=full_body, headers=headers, timeout=10)
    resp.raise_for_status()
    text = resp.text
    json_start = text.find("{")
    return json.loads(text[json_start:]).get("result", [])

def search(company: str) -> list:
    resp = requests.get(SEARCH_URL.format(company=company), timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", [])

def find_server(server_name: str) -> dict | None:
    for api_fn in [search_mq, search]:
        try:
            for company in api_fn(server_name):
                for result in company.get("results", []):
                    if result["name"].lower() == server_name.lower():
                        return {
                            "company": company.get("company"),
                            "name": result["name"],
                            "site": result.get("site"),
                            "access": result.get("access", []),
                            "is_demo": result.get("is_demo", 0),
                        }
        except Exception:
            continue
    return None

def find_server_ips(server_name: str) -> list[str]:
    info = find_server(server_name)
    return info["access"] if info else []
```

### Step 6: `connection.py` — WebSocket Layer

```python
import asyncio
import ssl
import websockets
from .crypto import aes_encrypt, aes_decrypt, STATIC_KEY
from .protocol import pack_data, parse_response

class Connection:
    def __init__(self, host: str, port: int = 443):
        self.host = host
        self.port = port
        self.ws = None
        self._send_lock = asyncio.Lock()
        self.session_key = None
    
    async def connect(self):
        url = f"wss://{self.host}:{self.port}/terminal"
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self.ws = await websockets.connect(
            url, ssl=ssl_ctx, ping_interval=None,
            additional_headers={'Origin': f'https://{self.host}:{self.port}'},
        )
    
    async def send(self, cmd_id: int, payload: bytes = b'', encrypted: bool = False):
        async with self._send_lock:
            if encrypted:
                data = pack_data(cmd_id, payload)
            else:
                data = pack_data(cmd_id, aes_encrypt(self.session_key, payload))
            await self.ws.send(data)
    
    async def recv(self, timeout: float = 10) -> dict | None:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        if isinstance(raw, bytes) and len(raw) > 8:
            return parse_response(aes_decrypt(self.session_key, raw[8:]))
        return None
    
    async def send_auth(self, payload: bytes) -> dict:
        await self.ws.send(pack_data(0, aes_encrypt(STATIC_KEY, payload)))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        return parse_response(aes_decrypt(STATIC_KEY, raw[8:]))
    
    async def close(self):
        if self.ws:
            await self.ws.close()
```

### Step 7: `client.py` — Main MT5Client

```python
import asyncio
import struct
import time
from datetime import datetime, timezone
from .connection import Connection
from .search import find_server_ips
from .models import Account, Symbol, Quote, Position, Order, Deal, Candle, TradeResult
from .exceptions import MT5Error, AuthError, TradeError, ServerNotFoundError
from .protocol import build_command, pack_op

class MT5Client:
    def __init__(self, login: int, password: str, server: str, build: int = 5830):
        self.login = login
        self.password = password
        self.server = server
        self.build = build
        self.conn = None
        self._cancelled = asyncio.Event()
        self._recv_task = None
        self._account = None
        self._symbols = {}
        self._quotes = {}
        self._pending = {}
        self._trade_events = asyncio.Queue()
        self._callbacks = {
            'on_quote': None,
            'on_order_update': None,
            'on_symbol_update': None,
        }
    
    async def __aenter__(await self):
        # 1. Resolve server IP
        ips = find_server_ips(self.server)
        if not ips:
            raise ServerNotFoundError(f"Server not found: {self.server}")
        host = ips[0].split(':')[0]
        port = int(ips[0].split(':')[1]) if ':' in ips[0] else 443
        
        # 2. Connect WebSocket
        self.conn = Connection(host, port)
        await self.conn.connect()
        
        # 3. Auth handshake
        await self._auth()
        
        # 4. Login
        await self._login()
        
        # 5. Start background receive loop
        self._recv_task = asyncio.create_task(self._recv_loop())
        
        # 6. Load account + symbols
        self._account = await self._get_account()
        self._symbols = await self._get_symbols()
        
        return self
    
    async def __aexit__(self, *args):
        self._cancelled.set()
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        await self.conn.close()
    
    async def _auth(self):
        auth_cmd = build_command(0, bytes(64))
        resp = await self.conn.send_auth(auth_cmd)
        if resp['res_code'] != 0:
            raise AuthError(f"Auth failed: code={resp['res_code']}")
        self.conn.session_key = resp['res_body'][66:]
    
    async def _login(self):
        # Build login payload (same as ws_client.py)
        login_pl = self._build_login_payload()
        await self.conn.send(28, build_command(28, login_pl))
        resp = await self.conn.recv()
        if not resp or resp['cmd_id'] != 28:
            raise AuthError("Login failed")
    
    async def _recv_loop(self):
        """Background message loop — mirrors C# _E020._E000()"""
        while not self._cancelled.is_set():
            try:
                msg = await self.conn.recv(timeout=30)
                if not msg:
                    continue
                
                cmd = msg['cmd_id']
                
                # Match pending request
                if cmd in self._pending:
                    future = self._pending.pop(cmd)
                    if not future.done():
                        future.set_result(msg)
                    continue
                
                # Dispatch to handlers
                if cmd == 8:    # Quote
                    await self._handle_quote(msg)
                elif cmd == 19: # Trade event
                    await self._handle_trade_event(msg)
                elif cmd == 22: # Position update
                    pass
                elif cmd == 14: # Account update
                    pass
                elif cmd == 15: # System message
                    pass
                elif cmd == 51: # Heartbeat
                    pass
                
            except asyncio.TimeoutError:
                await self._send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                pass  # Log error
    
    async def send_and_wait(self, cmd_id: int, payload: bytes = b'',
                            expected_cmd: int = None, timeout: float = 10) -> dict:
        future = asyncio.get_event_loop().create_future()
        self._pending[expected_cmd or cmd_id] = future
        await self.conn.send(cmd_id, build_command(cmd_id, payload))
        return await asyncio.wait_for(future, timeout)
    
    # === PUBLIC API ===
    
    async def get_account(self) -> Account:
        resp = await self.send_and_wait(3, b'', 3)
        # Parse account from resp['res_body'] using FL_SCHEMA
        return Account(...)
    
    async def get_symbols(self) -> dict[str, Symbol]:
        resp = await self.send_and_wait(34, b'', 34)
        # Parse symbols from resp['res_body'] (gzip compressed)
        return {...}
    
    async def subscribe(self, symbol: str) -> Quote:
        sym = self._symbols.get(symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {symbol}")
        payload = struct.pack('<II', 1, sym.id)
        await self.send_and_wait(7, payload, 7)
        # Wait for first quote
        for _ in range(60):
            if symbol in self._quotes:
                return self._quotes[symbol]
            await asyncio.sleep(0.5)
        raise MT5Error("No quote received")
    
    async def buy(self, symbol: str, volume: float, sl: float = 0, tp: float = 0,
                  comment: str = '') -> TradeResult:
        return await self._trade(symbol, 0, volume, sl, tp, comment)
    
    async def sell(self, symbol: str, volume: float, sl: float = 0, tp: float = 0,
                   comment: str = '') -> TradeResult:
        return await self._trade(symbol, 1, volume, sl, tp, comment)
    
    async def _trade(self, symbol: str, trade_type: int, volume: float,
                     sl: float, tp: float, comment: str) -> TradeResult:
        sym = self._symbols.get(symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {symbol}")
        
        # Get current quote for price
        quote = self._quotes.get(symbol)
        if not quote:
            raise MT5Error(f"No quote for {symbol}")
        
        price = quote.ask if trade_type == 0 else quote.bid
        lots = int(volume * 100_000_000)  # Convert to internal units
        
        op = pack_op(
            symbol=symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=3,  # MARKET
            volume=lots,
            digits=sym.digits,
            trade_type=trade_type,
            price=price,
            sl=sl, tp=tp,
            type_filling=0,  # FOK
            comment=comment,
        )
        ap = bytes(128)
        pp = struct.pack('<I', random.randint(0, 0xFFFFFFFF)) + op + ap
        
        # Send and wait for trade event
        await self.conn.send(12, pp)
        event = await asyncio.wait_for(self._trade_events.get(), timeout=30)
        return TradeResult(
            retcode=event['retcode'],
            deal=event['deal'],
            order=event['order'],
            volume=event['volume'],
            price=event['price'],
            comment=event['comment'],
        )
    
    async def get_positions(self) -> list[Position]:
        resp = await self.send_and_wait(4, b'', 4)
        # Parse positions from resp['res_body']
        return [...]
    
    async def get_deals(self, from_time: int = 0, to_time: int = 0) -> list[Deal]:
        payload = struct.pack('<II', from_time or 0, to_time or int(time.time()))
        resp = await self.send_and_wait(5, payload, 5)
        return [...]
    
    async def get_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        tf_val = TIMEFRAMES.get(timeframe, timeframe)
        now = int(time.time())
        sec_per = self._estimate_seconds(tf_val)
        from_sec = now - (count + 10) * sec_per
        
        pl = bytearray(80)
        sym_bytes = symbol.encode('utf-16-le')
        pl[0:len(sym_bytes)] = sym_bytes
        struct.pack_into('<H', pl, 64, tf_val)
        struct.pack_into('<i', pl, 66, from_sec)
        struct.pack_into('<i', pl, 70, now)
        
        resp = await self.send_and_wait(11, bytes(pl), 11)
        return self._parse_candles(resp['res_body'])
    
    async def close_position(self, ticket: int) -> TradeResult:
        positions = await self.get_positions()
        pos = next((p for p in positions if p.ticket == ticket), None)
        if not pos:
            raise TradeError(10030, "Position not found")
        
        opposite = 1 if pos.type == 0 else 0
        return await self._trade(pos.symbol, opposite, pos.volume, 0, 0, "")
    
    async def cancel_order(self, ticket: int) -> TradeResult:
        # Find pending order and send cancel
        ...
    
    async def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> TradeResult:
        # Send modify with trade_action=6
        ...
    
    async def modify_order(self, ticket: int, sl: float = None, tp: float = None,
                           price: float = None) -> TradeResult:
        # Send modify with trade_action=7
        ...
    
    # === CALLBACKS ===
    
    def on_quote(self, callback):
        self._callbacks['on_quote'] = callback
    
    def on_order_update(self, callback):
        self._callbacks['on_order_update'] = callback
    
    # === INTERNAL HANDLERS ===
    
    async def _handle_quote(self, msg):
        body = msg['res_body']
        qcount = struct.unpack_from('<I', body, 0)[0]
        p = 4
        for _ in range(qcount):
            # Parse 50-byte quote frame
            sym_id = struct.unpack_from('<I', body, p)[0]
            raw_bid = struct.unpack_from('<q', body, p+4)[0]
            raw_ask = struct.unpack_from('<q', body, p+12)[0]
            # Find symbol name
            sym_name = next((n for n, s in self._symbols.items() if s.id == sym_id), None)
            if sym_name:
                sym = self._symbols[sym_name]
                quote = Quote(
                    symbol=sym_name,
                    symbol_id=sym_id,
                    bid=raw_bid / 10**sym.digits,
                    ask=raw_ask / 10**sym.digits,
                    raw_bid=raw_bid,
                    raw_ask=raw_ask,
                    time=datetime.now(timezone.utc),
                )
                self._quotes[sym_name] = quote
                if self._callbacks['on_quote']:
                    self._callbacks['on_quote'](quote)
            p += 50
    
    async def _handle_trade_event(self, msg):
        body = msg['res_body']
        # Parse Ap at offset 4+248=252
        ap_off = 252
        if len(body) >= ap_off + 128:
            event = {
                'retcode': struct.unpack_from('<I', body, ap_off)[0],
                'deal': struct.unpack_from('<q', body, ap_off+4)[0],
                'order': struct.unpack_from('<q', body, ap_off+12)[0],
                'volume': struct.unpack_from('<q', body, ap_off+20)[0],
                'price': struct.unpack_from('<d', body, ap_off+28)[0],
                'comment': body[ap_off+64:ap_off+128].decode('utf-16-le', errors='ignore').rstrip('\x00'),
            }
            await self._trade_events.put(event)
```

### Step 8: `__init__.py` — Public API

```python
from .client import MT5Client
from .models import Account, Symbol, Quote, Position, Order, Deal, Candle, TradeResult
from .exceptions import MT5Error, AuthError, TradeError, ServerNotFoundError
from .search import find_server, find_server_ips, search, search_mq

__all__ = [
    'MT5Client',
    'Account', 'Symbol', 'Quote', 'Position', 'Order', 'Deal', 'Candle', 'TradeResult',
    'MT5Error', 'AuthError', 'TradeError', 'ServerNotFoundError',
    'find_server', 'find_server_ips', 'search', 'search_mq',
]
```

---

## Implementation Order

| # | Step | File | Lines Est. | Dependencies |
|---|------|------|-----------|--------------|
| 1 | Dataclasses | `models.py` | ~100 | None |
| 2 | Exceptions | `exceptions.py` | ~30 | None |
| 3 | Encryption | `crypto.py` | ~60 | `pycryptodome` |
| 4 | Wire format | `protocol.py` | ~200 | None |
| 5 | Broker search | `search.py` | ~120 | `requests` |
| 6 | WebSocket layer | `connection.py` | ~80 | `websockets` |
| 7 | Main client | `client.py` | ~500 | All above |
| 8 | Public API | `__init__.py` | ~20 | `client.py` |

**Total estimated: ~1100 lines** (vs current 736 in monolithic ws_client.py)

---

## Key Constants (verified)

```python
# Encryption
STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

# HMAC key for SearchMQ
HMAC_KEY = bytes([61, 123, 21, 22, 214, 234, 187, 52, 217, 214,
                  99, 227, 98, 62, 27, 215, 251, 220, 174, 244,
                  87, 59, 223, 53, 127, 168, 207, 11, 190, 173,
                  146, 127])

# Wire format
VERSION = 1  # Sent to server
VERSION = 0  # Received from server

# Trade actions
TRADE_MARKET = 3
TRADE_INSTANT = 2
TRADE_REQUEST = 1
TRADE_EXCHANGE = 4
TRADE_PENDING = 5
TRADE_MODIFY = 6
TRADE_MODIFY_ORDER = 7
TRADE_CANCEL = 8

# Trade types
TYPE_BUY = 0
TYPE_SELL = 1

# Fill policies
FILL_FOK = 0
FILL_IOC = 1
FILL_RETURN = 2

# Time types
TIME_GTC = 0
TIME_DAY = 1
TIME_SPECIFIED = 2

# Symbol trade modes
TRADE_DISABLED = 0
TRADE_LONGONLY = 1
TRADE_SHORTONLY = 2
TRADE_CLOSEONLY = 3
TRADE_FULL = 4

# Timeframes
TIMEFRAMES = {
    'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30,
    'H1': 16385, 'H4': 16388,
    'D1': 16408, 'W1': 32769, 'MN1': 49153,
}

# Volume
LOT_MULTIPLIER = 100_000_000  # 0.01 lots = 1,000,000
```

---

## Critical Rules (from reverse engineering)

1. **Position ID = ORDER ticket** (not deal ticket!)
2. **Volume encoding**: lots × 100,000,000
3. **Prices are raw** (divide by 10^digits)
4. **cmd_id=4 position list may be STALE** — use cmd_id=5 deal history for verification
5. **Quote frames are 50 bytes** — raw prices ÷ 10^digits
6. **Candle frames are 48 bytes** — datetime + OHLC + volume + spread
7. **Session key** is at bytes 66-103 of decrypted auth response (before PKCS7 unpad)

---

## Cleanup After Implementation

- Move all `ws_*.py` test scripts to `tests/` directory
- Remove `debug_*.py` scripts
- Update PLAN.md with completion status
- Update FUNCTION_COVERAGE.md
