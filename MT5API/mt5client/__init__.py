"""MT5 Client — Pure Python async API for MetaTrader 5."""
from .client import MT5Client
from .models import Account, Symbol, Quote, Position, Order, Deal, Candle, TradeResult, ServerInfo
from .exceptions import MT5Error, AuthError, TradeError, ServerNotFoundError
from .search import find_server, find_server_ips, search, search_mq

__version__ = "1.0.0"

__all__ = [
    'MT5Client',
    'Account', 'Symbol', 'Quote', 'Position', 'Order', 'Deal', 'Candle', 'TradeResult',
    'ServerInfo',
    'MT5Error', 'AuthError', 'TradeError', 'ServerNotFoundError',
    'find_server', 'find_server_ips', 'search', 'search_mq',
]
