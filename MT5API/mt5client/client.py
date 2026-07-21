"""High-level MT5 async client."""
import asyncio
import struct
import time
import random
import zlib
from datetime import datetime, timezone

from .connection import Connection
from .search import find_server_ips
from .crypto import STATIC_KEY
from .protocol import (
    build_command, pack_wire, parse_response, pack_op,
    FL_SCHEMA, FL_SIZE, MH_SCHEMA, MH_SIZE,
    QUOTE_SCHEMA, QUOTE_SIZE,
    POS_SCHEMA, POS_SIZE,
    DEAL_SCHEMA, DEAL_SIZE,
    OP_SIZE, parse_series, parse_candles, ts_to_dt,
    TIMEFRAMES, estimate_seconds_per_candle,
    LOT_MULTIPLIER, CMD_NAMES,
    TRADE_MARKET, TRADE_PENDING, TRADE_MODIFY, TRADE_MODIFY_ORDER, TRADE_CANCEL,
    TYPE_BUY, TYPE_SELL, FILL_FOK, FILL_RETURN, TIME_GTC,
)
from .models import Account, Symbol, Quote, Position, Order, Deal, Candle, TradeResult
from .exceptions import MT5Error, AuthError, TradeError, ServerNotFoundError


class MT5Client:
    """Async MT5 client. Usage:
        async with MT5Client(login=..., password=..., server=...) as client:
            ...
    """

    def __init__(self, login: int, password: str, server: str, build: int = 5830):
        self.login = login
        self.password = password
        self.server = server
        self.build = build
        self.conn: Connection | None = None
        self._cancelled = asyncio.Event()
        self._recv_task = None
        self._heartbeat_task = None
        self._account: Account | None = None
        self._symbols: dict[str, Symbol] = {}
        self._quotes: dict[str, Quote] = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._trade_events: asyncio.Queue = asyncio.Queue()
        self._on_quote_callback = None
        self._on_trade_callback = None
        self._on_spec_callback = None
        self._spec_data: dict[int, bytes] = {}  # symbol_id -> spec bytes
        self._position_cache: dict[int, Position] = {}  # ticket -> Position (from TRADE_EVENT)
        self._order_cache: dict[int, Order] = {}  # ticket -> Order (from pending trade results)

    async def __aenter__(self):
        # 1. Resolve server IP
        ips = find_server_ips(self.server)
        if not ips:
            raise ServerNotFoundError(f"Server not found: {self.server}")
        host = ips[0].split(':')[0]
        port = int(ips[0].split(':')[1]) if ':' in ips[0] else 443

        # 2. Connect WebSocket
        self.conn = Connection(host, port)
        await self.conn.connect()

        # 3. Auth handshake (cmd 0)
        await self._auth()

        # 4. Login (cmd 28)
        await self._login()

        # 5. Start background receive loop + heartbeat
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # 6. Load account + symbols
        acct_resp = await self.send_and_wait(3, b'', 3)
        if acct_resp:
            self._account = self._parse_account(acct_resp['res_body'])

        sym_resp = await self.send_and_wait(34, b'', 34)
        if sym_resp:
            self._symbols = self._parse_symbols(sym_resp['res_body'])

        return self

    async def __aexit__(self, *args):
        self._cancelled.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        await self.conn.close()

    # --- Connection ---

    async def _auth(self):
        auth_cmd = build_command(0, bytes(64))
        await self.conn.send_auth(auth_cmd)
        resp = await self.conn.recv_auth()
        if not resp or resp['res_code'] != 0:
            raise AuthError(f"Auth failed: code={resp['res_code'] if resp else 'no response'}")
        self.conn.session_key = resp['res_body'][66:]

    async def _login(self):
        login_pl = bytearray(912)
        pw = self.password.encode('utf-16-le')
        login_pl[4:4 + len(pw)] = pw
        server_ip = self.server
        ip_bytes = server_ip.encode('utf-16-le')
        struct.pack_into('<I', login_pl, 476, len(ip_bytes) // 2)
        login_pl[480:480 + len(ip_bytes)] = ip_bytes
        struct.pack_into('<Q', login_pl, 736, self.login)
        await self.conn.send_encrypted(28, build_command(28, bytes(login_pl)))
        resp = await self.conn.recv()
        if not resp or resp['cmd_id'] != 28:
            raise AuthError("Login failed")

    async def _recv_loop(self):
        while not self._cancelled.is_set():
            try:
                msg = await self.conn.recv(timeout=30)
                if not msg:
                    continue

                cmd = msg['cmd_id']

                if cmd in self._pending:
                    future = self._pending.pop(cmd)
                    if not future.done():
                        future.set_result(msg)
                    continue

                if cmd == 8:
                    await self._handle_quote(msg)
                elif cmd == 19:
                    await self._handle_trade_event(msg)
                elif cmd == 17:
                    await self._handle_spec(msg)
                elif cmd in (22, 14, 15, 51):
                    pass
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _heartbeat_loop(self):
        """Send heartbeat (cmd 51) every 3 seconds to keep connection alive."""
        while not self._cancelled.is_set():
            try:
                await asyncio.sleep(3)
                await self.conn.send_encrypted(51, build_command(51))
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def send_and_wait(self, cmd_id: int, payload: bytes = b'',
                            expected_cmd: int = None, timeout: float = 10) -> dict | None:
        future = asyncio.get_event_loop().create_future()
        self._pending[expected_cmd or cmd_id] = future
        await self.conn.send_encrypted(cmd_id, build_command(cmd_id, payload))
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(expected_cmd or cmd_id, None)
            return None

    # --- Public API ---

    async def get_account(self) -> Account:
        resp = await self.send_and_wait(3, b'', 3)
        if resp:
            self._account = self._parse_account(resp['res_body'])
        return self._account

    async def get_symbols(self) -> dict[str, Symbol]:
        resp = await self.send_and_wait(34, b'', 34)
        if resp:
            self._symbols = self._parse_symbols(resp['res_body'])
        return self._symbols

    async def send_subscribe(self, symbols):
        """Send subscribe request(s). Accepts single symbol name or list."""
        if isinstance(symbols, str):
            symbols = [symbols]
        ids = []
        for name in symbols:
            sym = self._symbols.get(name)
            if sym:
                ids.append(sym.id)
        if not ids:
            return
        payload = struct.pack('<I', len(ids))
        for sid in ids:
            payload += struct.pack('<I', sid)
        await self.conn.send_encrypted(7, build_command(7, payload))

    async def subscribe(self, symbol: str, timeout: float = 10) -> Quote:
        sym = self._symbols.get(symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {symbol}")
        await self.send_subscribe([symbol])
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if symbol in self._quotes:
                return self._quotes[symbol]
            await asyncio.sleep(0.2)
        raise MT5Error(f"No quote received for {symbol}")

    async def subscribe_batch(self, symbols: list[str], timeout: float = 15) -> dict[str, Quote]:
        """Subscribe to multiple symbols in ONE command, then wait for quotes."""
        await self.send_subscribe(symbols)
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            received = [s for s in symbols if s in self._quotes]
            if len(received) >= len(symbols):
                break
            await asyncio.sleep(0.5)
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    def get_quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

    async def buy(self, symbol: str, volume: float, sl: float = 0, tp: float = 0,
                  comment: str = '') -> TradeResult:
        return await self._trade(symbol, TYPE_BUY, volume, sl, tp, comment)

    async def sell(self, symbol: str, volume: float, sl: float = 0, tp: float = 0,
                   comment: str = '') -> TradeResult:
        return await self._trade(symbol, TYPE_SELL, volume, sl, tp, comment)

    async def buy_limit(self, symbol: str, volume: float, price: float,
                        sl: float = 0, tp: float = 0, comment: str = '') -> TradeResult:
        return await self._trade(symbol, 0, volume, sl, tp, comment,
                                 trade_action=TRADE_PENDING, type_filling=FILL_RETURN,
                                 price=price, order_type=2)

    async def sell_limit(self, symbol: str, volume: float, price: float,
                         sl: float = 0, tp: float = 0, comment: str = '') -> TradeResult:
        return await self._trade(symbol, 1, volume, sl, tp, comment,
                                 trade_action=TRADE_PENDING, type_filling=FILL_RETURN,
                                 price=price, order_type=3)

    async def buy_stop(self, symbol: str, volume: float, price: float,
                       sl: float = 0, tp: float = 0, comment: str = '') -> TradeResult:
        return await self._trade(symbol, 0, volume, sl, tp, comment,
                                 trade_action=TRADE_PENDING, type_filling=FILL_RETURN,
                                 price=price, order_type=4)

    async def sell_stop(self, symbol: str, volume: float, price: float,
                        sl: float = 0, tp: float = 0, comment: str = '') -> TradeResult:
        return await self._trade(symbol, 1, volume, sl, tp, comment,
                                 trade_action=TRADE_PENDING, type_filling=FILL_RETURN,
                                 price=price, order_type=5)

    async def _send_trade(self, op: bytes, timeout: float = 15) -> TradeResult:
        """Send a trade command and wait for result.

        Some commands (buy/sell/close) get TRADE_EVENT (cmd 19) with full details.
        Others (modify/cancel) get only TRADE response (cmd 12) with retcode.
        This method handles both by polling both result paths.
        """
        cmd12_future = asyncio.get_event_loop().create_future()
        self._pending[12] = cmd12_future
        self._trade_events = asyncio.Queue()
        start_time = asyncio.get_event_loop().time()

        await self.conn.send_encrypted(12, build_command(12, op))

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            # Check if TRADE_EVENT (cmd 19) arrived — this has the real result
            while not self._trade_events.empty():
                event = self._trade_events.get_nowait()
                if event['retcode'] != 10002:
                    self._pending.pop(12, None)
                    return TradeResult(
                        retcode=event['retcode'], deal=event['deal'], order=event['order'],
                        volume=event['volume'], price=event['price'], comment=event['comment'],
                        position=event['position'],
                    )
            # Only fall back to cmd12 after 3s to let TRADE_EVENT arrive first
            if cmd12_future.done() and asyncio.get_event_loop().time() - start_time > 3:
                msg = cmd12_future.result()
                self._pending.pop(12, None)
                body = msg['res_body']
                retcode = struct.unpack_from('<I', body, 0)[0] if len(body) >= 4 else 0
                return TradeResult(retcode=retcode, comment=f"retcode={retcode}")
            await asyncio.sleep(0.05)

        self._pending.pop(12, None)
        raise asyncio.TimeoutError("No trade response received")

    async def _trade(self, symbol: str, trade_type: int, volume: float,
                     sl: float, tp: float, comment: str,
                     trade_action: int = TRADE_MARKET, type_filling: int = FILL_FOK,
                     price: float = 0.0, order_type: int = 0,
                     deviation: int = 30) -> TradeResult:
        sym = self._symbols.get(symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {symbol}")

        quote = self._quotes.get(symbol)
        if not quote:
            raise MT5Error(f"No quote for {symbol}. Call subscribe() first.")

        if price == 0:
            price = quote.ask if trade_type == TYPE_BUY else quote.bid

        lots = int(volume * LOT_MULTIPLIER)

        op = pack_op(
            symbol=symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=trade_action,
            volume=lots,
            digits=sym.digits,
            trade_type=order_type if order_type else trade_type,
            price=price,
            sl=sl, tp=tp,
            type_filling=type_filling,
            type_time=TIME_GTC,
            comment=comment,
            price_deviation=deviation,
        )

        result = await self._send_trade(op)

        if result.retcode == 10009 and trade_action == TRADE_MARKET and result.order:
            self._position_cache[result.order] = Position(
                ticket=result.order, symbol=symbol, type=trade_type,
                volume=result.volume / LOT_MULTIPLIER if result.volume else volume,
                price=result.price if result.price else price,
            )

        return result

    async def get_positions(self) -> list[Position]:
        resp = await self.send_and_wait(4, b'', 4)
        if not resp:
            return []
        return self._parse_positions(resp['res_body'])

    async def get_orders(self) -> list[Order]:
        resp = await self.send_and_wait(4, b'', 4)
        if not resp:
            return []
        return self._parse_orders(resp['res_body'])

    async def get_deals(self, from_time: int = 0, to_time: int = 0) -> list[Deal]:
        payload = struct.pack('<II', from_time or 0, to_time or int(time.time()))
        resp = await self.send_and_wait(5, payload, 5)
        if not resp:
            return []
        return self._parse_deals(resp['res_body'])

    async def get_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        tf_val = TIMEFRAMES.get(timeframe, timeframe) if isinstance(timeframe, str) else timeframe
        now = int(time.time())
        sec_per = estimate_seconds_per_candle(tf_val)
        from_sec = now - (count + 10) * sec_per

        pl = bytearray(74)
        sym_bytes = symbol.encode('utf-16-le')
        pl[0:len(sym_bytes)] = sym_bytes
        struct.pack_into('<H', pl, 64, tf_val)
        struct.pack_into('<i', pl, 66, from_sec)
        struct.pack_into('<i', pl, 70, now)

        resp = await self.send_and_wait(11, bytes(pl), 11, timeout=10)
        if resp and len(resp['res_body']) >= 48:
            return [Candle(**c) for c in parse_candles(resp['res_body'])]
        return []

    async def close_position(self, ticket: int, symbol: str = None, pos_type: int = None,
                             volume: float = None) -> TradeResult:
        pos = None
        if symbol and pos_type is not None and volume is not None:
            pos = Position(ticket=ticket, symbol=symbol, type=pos_type, volume=volume, price=0)
        else:
            positions = await self.get_positions()
            pos = next((p for p in positions if p.ticket == ticket), None)
            if not pos:
                pos = self._position_cache.get(ticket)
            if not pos:
                raise TradeError(10030, "Position not found")

        sym = self._symbols.get(pos.symbol)
        quote = self._quotes.get(pos.symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {pos.symbol}")

        opposite = TYPE_SELL if pos.type == TYPE_BUY else TYPE_BUY
        if quote:
            price = quote.bid if pos.type == TYPE_BUY else quote.ask
        else:
            price = pos.price

        op = pack_op(
            symbol=pos.symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=TRADE_MARKET,
            volume=int(pos.volume * LOT_MULTIPLIER),
            digits=sym.digits,
            trade_type=opposite,
            price=price,
            type_filling=FILL_FOK,
            trade_position=ticket,
        )
        return await self._send_trade(op)

    async def partial_close(self, ticket: int, volume: float, symbol: str = None,
                            pos_type: int = None, pos_volume: float = None) -> TradeResult:
        pos = None
        if symbol and pos_type is not None and pos_volume is not None:
            pos = Position(ticket=ticket, symbol=symbol, type=pos_type, volume=pos_volume, price=0)
        else:
            positions = await self.get_positions()
            pos = next((p for p in positions if p.ticket == ticket), None)
            if not pos:
                pos = self._position_cache.get(ticket)
            if not pos:
                raise TradeError(10030, "Position not found")

        sym = self._symbols.get(pos.symbol)
        quote = self._quotes.get(pos.symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {pos.symbol}")

        opposite = TYPE_SELL if pos.type == TYPE_BUY else TYPE_BUY
        if quote:
            price = quote.bid if pos.type == TYPE_BUY else quote.ask
        else:
            price = pos.price

        op = pack_op(
            symbol=pos.symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=TRADE_MARKET,
            volume=int(volume * LOT_MULTIPLIER),
            digits=sym.digits,
            trade_type=opposite,
            price=price,
            type_filling=FILL_FOK,
            trade_position=ticket,
        )
        return await self._send_trade(op)

    async def cancel_order(self, ticket: int, symbol: str = None, order_type: int = None,
                           volume: float = None, price: float = 0) -> TradeResult:
        order = None
        if symbol and order_type is not None and volume is not None:
            order = Order(ticket=ticket, symbol=symbol, type=order_type, volume=volume, price=price)
        else:
            orders = await self.get_orders()
            order = next((o for o in orders if o.ticket == ticket), None)
            if not order:
                order = self._order_cache.get(ticket)
            if not order:
                raise TradeError(10030, "Order not found")

        sym = self._symbols.get(order.symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {order.symbol}")

        op = pack_op(
            symbol=order.symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=TRADE_CANCEL,
            volume=int(order.volume * LOT_MULTIPLIER),
            digits=sym.digits,
            trade_type=order.type,
            price=order.price,
            trade_order=ticket,
            type_filling=FILL_FOK,
        )
        return await self._send_trade(op)

    async def modify_position(self, ticket: int, sl: float = None, tp: float = None,
                              symbol: str = None, pos_type: int = None, volume: float = None) -> TradeResult:
        pos = None
        if symbol and pos_type is not None and volume is not None:
            pos = Position(ticket=ticket, symbol=symbol, type=pos_type, volume=volume, price=0)
        else:
            positions = await self.get_positions()
            pos = next((p for p in positions if p.ticket == ticket), None)
            if not pos:
                pos = self._position_cache.get(ticket)
            if not pos:
                raise TradeError(10030, "Position not found")

        sym = self._symbols.get(pos.symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {pos.symbol}")

        op = pack_op(
            symbol=pos.symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=TRADE_MODIFY,
            volume=int(pos.volume * LOT_MULTIPLIER),
            digits=sym.digits,
            trade_type=pos.type,
            price=0,
            sl=sl if sl is not None else pos.sl,
            tp=tp if tp is not None else pos.tp,
            trade_order=ticket,
            trade_position=ticket,
            type_filling=FILL_RETURN,
        )
        return await self._send_trade(op)

    async def modify_order(self, ticket: int, sl: float = None, tp: float = None,
                           price: float = None) -> TradeResult:
        orders = await self.get_orders()
        order = next((o for o in orders if o.ticket == ticket), None)
        if not order:
            raise TradeError(10030, "Order not found")

        sym = self._symbols.get(order.symbol)
        if not sym:
            raise MT5Error(f"Symbol not found: {order.symbol}")

        op = pack_op(
            symbol=order.symbol,
            action_id=random.randint(0, 0xFFFFFFFF),
            trade_action=TRADE_MODIFY_ORDER,
            volume=int(order.volume * LOT_MULTIPLIER),
            digits=sym.digits,
            trade_type=order.type,
            price=price if price is not None else order.price,
            sl=sl if sl is not None else order.sl,
            tp=tp if tp is not None else order.tp,
            trade_order=ticket,
            type_filling=FILL_RETURN,
        )
        return await self._send_trade(op)

    # --- Callbacks ---

    def on_quote(self, callback):
        self._on_quote_callback = callback

    def on_trade(self, callback):
        self._on_trade_callback = callback

    def on_spec(self, callback):
        self._on_spec_callback = callback

    # --- Internal handlers ---

    async def _handle_quote(self, msg):
        body = msg['res_body']
        p = 0
        while p + QUOTE_SIZE <= len(body):
            qv, p = parse_series(body, QUOTE_SCHEMA, p)
            if qv is None:
                break
            sym_id = qv[0]
            raw_bid = qv[3]
            raw_ask = qv[4]
            if raw_bid is None or raw_ask is None:
                continue
            sym_name = next((n for n, s in self._symbols.items() if s.id == sym_id), None)
            if sym_name:
                sym = self._symbols[sym_name]
                quote = Quote(
                    symbol=sym_name,
                    symbol_id=sym_id,
                    bid=raw_bid / 10 ** sym.digits,
                    ask=raw_ask / 10 ** sym.digits,
                    raw_bid=raw_bid,
                    raw_ask=raw_ask,
                    time=datetime.now(timezone.utc),
                )
                self._quotes[sym_name] = quote
                if self._on_quote_callback:
                    try:
                        self._on_quote_callback(quote)
                    except Exception:
                        pass

    async def _handle_trade_event(self, msg):
        body = msg['res_body']
        ap_off = 4 + OP_SIZE
        if len(body) >= ap_off + 32:
            event = {
                'retcode': struct.unpack_from('<I', body, ap_off)[0],
                'deal': struct.unpack_from('<q', body, ap_off + 4)[0],
                'order': struct.unpack_from('<q', body, ap_off + 12)[0],
                'position': struct.unpack_from('<q', body, ap_off + 12)[0],
                'volume': struct.unpack_from('<q', body, ap_off + 20)[0],
                'price': struct.unpack_from('<d', body, ap_off + 28)[0],
                'comment': body[ap_off + 64:ap_off + 128].decode('utf-16-le', errors='ignore').rstrip('\x00'),
            }
            await self._trade_events.put(event)
            if self._on_trade_callback:
                try:
                    self._on_trade_callback(event)
                except Exception:
                    pass

    async def _handle_spec(self, msg):
        """Handle symbol spec push (cmd 17) — 1696-byte SymbolInfo struct."""
        body = msg['res_body']
        if len(body) >= 1696:
            # Find symbol name at start of spec
            try:
                name_raw = body[0:64]
                name = name_raw.decode('utf-16-le').split('\x00')[0]
            except Exception:
                name = ""

            spec = {
                'name': name,
                'tick_value': struct.unpack_from('<d', body, 1376)[0],
                'tick_size': struct.unpack_from('<d', body, 1384)[0],
                'contract_size': struct.unpack_from('<d', body, 1392)[0],
                'digits': struct.unpack_from('<i', body, 1168)[0],
                'point': struct.unpack_from('<d', body, 1172)[0],
                'spread': struct.unpack_from('<i', body, 1368)[0],
                'calc_mode': struct.unpack_from('<i', body, 1404)[0],
                'flags': struct.unpack_from('<q', body, 1360)[0],
                'raw': body,
            }

            # Update symbol with spec data
            if name in self._symbols:
                sym = self._symbols[name]
                sym.tick_value = spec['tick_value']
                sym.tick_size = spec['tick_size']
                sym.contract_size = spec['contract_size']
                sym.point = spec['point']
                sym.spread = spec['spread']

            # Cache by symbol_id (first uint32 in body)
            if len(body) >= 4:
                sym_id = struct.unpack_from('<I', body, 0)[0]
                self._spec_data[sym_id] = body

            if self._on_spec_callback:
                try:
                    self._on_spec_callback(spec)
                except Exception:
                    pass

    # --- Parsers ---

    def _parse_account(self, body: bytes) -> Account:
        acct, _ = parse_series(body, FL_SCHEMA, 0)
        return Account(
            login=acct[1],
            balance=acct[3],
            equity=acct[4],
            currency=acct[5] or "",
            group=acct[8] or "",
            leverage=acct[9],
            server=acct[10] or "",
            profit=acct[16] if acct[16] is not None else 0,
            margin=acct[17] if acct[17] is not None else 0,
        )

    def _parse_symbols(self, body: bytes) -> dict[str, Symbol]:
        try:
            decomp = zlib.decompress(body[4:])
        except Exception:
            decomp = zlib.decompress(body[4:], -15)
        count = struct.unpack_from('<I', decomp, 0)[0]
        off = 4
        result = {}
        for _ in range(count):
            if off + MH_SIZE > len(decomp):
                break
            vals, off = parse_series(decomp, MH_SCHEMA, off)
            result[vals[0]] = Symbol(
                name=vals[0],
                id=vals[3],
                digits=vals[2],
                calc_mode=vals[5],
            )
        return result

    def _parse_positions(self, body: bytes) -> list[Position]:
        if len(body) < 4:
            return []
        pos_cnt = struct.unpack_from('<I', body, 0)[0]
        order_cnt = struct.unpack_from('<I', body, 4)[0] if len(body) >= 8 else 0
        off = 4
        result = []
        for _ in range(pos_cnt):
            if off + POS_SIZE > len(body):
                break
            vals, off = parse_series(body, POS_SCHEMA, off)
            result.append(Position(
                ticket=vals[0],
                symbol=vals[4] or "",
                type=vals[5],
                volume=vals[10] / LOT_MULTIPLIER if vals[10] else 0,
                price=vals[6],
                sl=vals[8],
                tp=vals[9],
                profit=vals[11] or 0,
                swap=vals[15] or 0,
                commission=vals[14] or 0,
                comment=vals[18] or "",
                time=ts_to_dt(vals[2] if vals[2] else 0),
            ))
        return result

    ORDER_SIZE = 356

    def _parse_orders(self, body: bytes) -> list[Order]:
        if len(body) < 4:
            return []
        pos_cnt = struct.unpack_from('<I', body, 0)[0]
        off = 4 + pos_cnt * POS_SIZE
        if off + 4 > len(body):
            return []
        order_cnt = struct.unpack_from('<I', body, off)[0]
        off += 4
        result = []
        for _ in range(order_cnt):
            if off + self.ORDER_SIZE > len(body):
                break
            rec = body[off:off + self.ORDER_SIZE]
            ticket = struct.unpack_from('<q', rec, 0)[0]
            sym_raw = rec[72:136]
            symbol = sym_raw.decode('utf-16-le', errors='ignore').split('\x00')[0]
            order_type = struct.unpack_from('<I', rec, 148)[0]
            vol_raw = struct.unpack_from('<I', rec, 204)[0]
            order_price = struct.unpack_from('<d', rec, 336)[0]
            result.append(Order(
                ticket=ticket,
                symbol=symbol,
                type=order_type,
                volume=vol_raw / LOT_MULTIPLIER if vol_raw else 0,
                price=order_price,
            ))
            off += self.ORDER_SIZE
        return result

    def _parse_deals(self, body: bytes) -> list[Deal]:
        if len(body) < 4:
            return []
        count = struct.unpack_from('<I', body, 0)[0]
        off = 4
        result = []
        for _ in range(count):
            if off + DEAL_SIZE > len(body):
                break
            vals, off = parse_series(body, DEAL_SCHEMA, off)
            result.append(Deal(
                ticket=vals[0],
                order=vals[2],
                symbol=vals[5] or "",
                type=vals[7],
                volume=vals[12] / LOT_MULTIPLIER if vals[12] else 0,
                price=vals[8],
                profit=vals[13] or 0,
                commission=vals[16] or 0,
                swap=vals[17] or 0,
                comment=vals[20] or "",
                time=ts_to_dt(vals[3] if vals[3] else 0),
            ))
        return result
