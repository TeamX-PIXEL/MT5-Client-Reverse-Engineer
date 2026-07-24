"""Data models for MT5 API responses."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Account:
    login: int = 0
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    currency: str = ""
    group: str = ""
    leverage: int = 0
    server: str = ""
    profit: float = 0.0


@dataclass
class Symbol:
    name: str = ""
    id: int = 0
    digits: int = 0
    point: float = 0.0
    spread: int = 0
    trade_mode: int = 0
    contract_size: float = 100000.0
    tick_value: float = 0.0
    tick_size: float = 0.0
    calc_mode: int = 0
    min_volume: int = 0
    max_volume: int = 0
    volume_step: int = 0
    swap_long: float = 0.0
    swap_short: float = 0.0
    initial_margin: float = 0.0
    maintenance_margin: float = 0.0


@dataclass
class Quote:
    symbol: str = ""
    symbol_id: int = 0
    bid: float = 0.0
    ask: float = 0.0
    raw_bid: int = 0
    raw_ask: int = 0
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Position:
    ticket: int = 0        # ORDER ticket (not deal!)
    symbol: str = ""
    type: int = 0          # 0=BUY, 1=SELL
    volume: float = 0.0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    magic: int = 0
    comment: str = ""
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    time_open: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_price: float = 0.0


@dataclass
class Order:
    ticket: int = 0
    symbol: str = ""
    type: int = 0          # 0=BUY_LIMIT, 1=SELL_LIMIT, 2=BUY_STOP, 3=SELL_STOP, ...
    status: int = 0
    volume: float = 0.0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    magic: int = 0
    comment: str = ""
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Deal:
    ticket: int = 0
    order: int = 0         # ORDER ticket
    position: int = 0      # Position ticket
    symbol: str = ""
    type: int = 0          # 0=BUY, 1=SELL
    volume: float = 0.0
    price: float = 0.0
    profit: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    magic: int = 0
    comment: str = ""
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Candle:
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timestamp: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    tick_volume: int = 0
    spread: int = 0


@dataclass
class TradeResult:
    retcode: int = 0
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    comment: str = ""
    position: int = 0


@dataclass
class ServerInfo:
    name: str = ""
    company: str = ""
    site: str = ""
    access: list = field(default_factory=list)
    is_demo: bool = False
