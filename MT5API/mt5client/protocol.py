"""Wire format, command builder, response parser, and serialization schemas."""
import struct
import random
from datetime import datetime, timezone


# --- Wire format ---

def pack_wire(cmd_id: int, encrypted: bytes) -> bytes:
    """[length:4LE][version:4LE=1][encrypted]"""
    return struct.pack('<II', len(encrypted), 1) + encrypted


def build_command(cmd_id: int, payload: bytes = b'') -> bytes:
    """[random:2][cmd_id:2LE][payload]"""
    cmd = bytearray(4 + len(payload))
    cmd[0] = random.randint(0, 255)
    cmd[1] = random.randint(0, 255)
    struct.pack_into('<H', cmd, 2, cmd_id)
    if payload:
        cmd[4:4 + len(payload)] = payload
    return bytes(cmd)


def parse_response(data: bytes) -> dict | None:
    """[tag:2][cmd_id:2LE][res_code:1][res_body:N]"""
    if not data or len(data) < 5:
        return None
    return {
        'tag': struct.unpack('<H', data[0:2])[0],
        'cmd_id': struct.unpack('<H', data[2:4])[0],
        'res_code': data[4],
        'res_body': data[5:],
    }


# --- Binary serialization parser (Ac.parse from JS) ---

TYPE_SIZES = {1: 1, 2: 2, 3: 4, 4: 1, 5: 2, 6: 4, 7: 4, 8: 8, 17: 8, 18: 8}


def series_size(schema: list) -> int:
    size = 0
    for field in schema:
        pt = field['propType']
        if pt in TYPE_SIZES:
            size += TYPE_SIZES[pt]
        elif pt in (11, 12):
            size += field.get('propLength', 0)
    return size


def parse_series(data: bytes, schema: list, offset: int = 0) -> tuple:
    vals = []
    for field in schema:
        pt = field['propType']
        pl = field.get('propLength', 0)
        if offset >= len(data):
            vals.append(None)
            continue
        if pt in TYPE_SIZES:
            sz = TYPE_SIZES[pt]
            if offset + sz > len(data):
                vals.append(None)
                offset = len(data)
                continue
            if pt == 1:
                vals.append(struct.unpack_from('<b', data, offset)[0])
            elif pt == 2:
                vals.append(struct.unpack_from('<h', data, offset)[0])
            elif pt == 3:
                vals.append(struct.unpack_from('<i', data, offset)[0])
            elif pt == 4:
                vals.append(data[offset])
            elif pt == 5:
                vals.append(struct.unpack_from('<H', data, offset)[0])
            elif pt == 6:
                vals.append(struct.unpack_from('<I', data, offset)[0])
            elif pt == 7:
                vals.append(struct.unpack_from('<f', data, offset)[0])
            elif pt == 8:
                vals.append(struct.unpack_from('<d', data, offset)[0])
            elif pt == 17:
                vals.append(struct.unpack_from('<q', data, offset)[0])
            elif pt == 18:
                vals.append(struct.unpack_from('<Q', data, offset)[0])
            else:
                vals.append(None)
            offset += sz
        elif pt == 11:
            if offset + pl > len(data):
                vals.append(None)
                offset = len(data)
            else:
                raw = data[offset:offset + pl]
                try:
                    s = raw.decode('utf-16-le')
                    nul = s.find('\x00')
                    if nul >= 0:
                        s = s[:nul]
                    vals.append(s)
                except Exception:
                    vals.append(raw.hex())
                offset += pl
        elif pt == 12:
            if offset + pl > len(data):
                vals.append(None)
                offset = len(data)
            else:
                vals.append(data[offset:offset + pl])
                offset += pl
        else:
            vals.append(None)
    return vals, offset


# --- Response parsing helpers ---

def ts_to_dt(ts: int) -> datetime:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OSError, ValueError):
        return datetime.now(timezone.utc)


# --- Schemas ---

FL_SCHEMA = [
    {'propType': 4},
    {'propType': 3},
    {'propType': 3},
    {'propType': 8},
    {'propType': 8},
    {'propType': 11, 'propLength': 64},
    {'propType': 6},
    {'propType': 6},
    {'propType': 11, 'propLength': 256},
    {'propType': 5},
    {'propType': 11, 'propLength': 128},
    {'propType': 11, 'propLength': 256},
    {'propType': 3},
    {'propType': 1},
    {'propType': 6},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 6},
    {'propType': 8},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 6},
    {'propType': 6},
]
FL_SIZE = series_size(FL_SCHEMA)

MH_SCHEMA = [
    {'propType': 11, 'propLength': 64},
    {'propType': 11, 'propLength': 128},
    {'propType': 6},
    {'propType': 6},
    {'propType': 11, 'propLength': 256},
    {'propType': 6},
    {'propType': 11, 'propLength': 64},
    {'propType': 5},
]
MH_SIZE = series_size(MH_SCHEMA)

QUOTE_SCHEMA = [
    {'propType': 6},
    {'propType': 3},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 17},
    {'propType': 6},
    {'propType': 5},
]
QUOTE_SIZE = series_size(QUOTE_SCHEMA)

POS_SCHEMA = [
    {'propType': 17},
    {'propType': 17},
    {'propType': 6},
    {'propType': 6},
    {'propType': 11, 'propLength': 64},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 18},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 17},
    {'propType': 17},
    {'propType': 11, 'propLength': 64},
    {'propType': 8},
    {'propType': 6},
    {'propType': 6},
    {'propType': 6},
    {'propType': 11, 'propLength': 64},
    {'propType': 3},
    {'propType': 3},
]
POS_SIZE = series_size(POS_SCHEMA)

DEAL_SCHEMA = [
    {'propType': 17},
    {'propType': 11, 'propLength': 64},
    {'propType': 17},
    {'propType': 6},
    {'propType': 6},
    {'propType': 11, 'propLength': 64},
    {'propType': 6},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 18},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 17},
    {'propType': 17},
    {'propType': 11, 'propLength': 64},
    {'propType': 8},
    {'propType': 6},
    {'propType': 6},
    {'propType': 6},
    {'propType': 3},
    {'propType': 3},
    {'propType': 8},
]
DEAL_SIZE = series_size(DEAL_SCHEMA)

OP_SCHEMA = [
    {'propType': 6},
    {'propType': 6},
    {'propType': 11, 'propLength': 64},
    {'propType': 18},
    {'propType': 6},
    {'propType': 18},
    {'propType': 6},
    {'propType': 6},
    {'propType': 6},
    {'propType': 6},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 8},
    {'propType': 6},
    {'propType': 8},
    {'propType': 8},
    {'propType': 11, 'propLength': 64},
    {'propType': 18},
    {'propType': 18},
    {'propType': 6},
]
OP_SIZE = series_size(OP_SCHEMA)


# --- Op builder ---

def pack_op(
    symbol: str,
    action_id: int,
    trade_action: int,
    volume: int,
    digits: int,
    trade_type: int,
    price: float,
    sl: float = 0.0,
    tp: float = 0.0,
    trade_order: int = 0,
    type_filling: int = 0,
    type_time: int = 0,
    type_flags: int = 2,
    comment: str = '',
    trade_position: int = 0,
    price_trigger: float = 0.0,
    price_deviation: int = 0,
    magic: int = 0,
) -> bytes:
    op = bytearray(OP_SIZE)
    struct.pack_into('<I', op, 0, action_id)
    struct.pack_into('<I', op, 4, trade_action)
    sym_bytes = symbol.encode('utf-16-le')
    op[8:8 + len(sym_bytes)] = sym_bytes
    struct.pack_into('<Q', op, 72, volume)
    struct.pack_into('<I', op, 80, digits)
    struct.pack_into('<Q', op, 84, trade_order)
    struct.pack_into('<I', op, 92, trade_type)
    struct.pack_into('<I', op, 96, type_filling)
    struct.pack_into('<I', op, 100, type_time)
    struct.pack_into('<I', op, 104, type_flags)
    struct.pack_into('<I', op, 108, 0)
    struct.pack_into('<d', op, 112, price)
    struct.pack_into('<d', op, 120, price_trigger)
    struct.pack_into('<d', op, 128, sl)
    struct.pack_into('<d', op, 136, tp)
    struct.pack_into('<I', op, 144, price_deviation)
    struct.pack_into('<d', op, 148, 0)
    struct.pack_into('<d', op, 156, 0)
    comment_bytes = comment.encode('utf-16-le')[:64]
    op[164:164 + len(comment_bytes)] = comment_bytes
    struct.pack_into('<Q', op, 228, trade_position)
    struct.pack_into('<Q', op, 236, 0)
    struct.pack_into('<I', op, 244, 0)
    return bytes(op)


# --- Timeframe conversion ---

TIMEFRAMES = {
    'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30,
    'H1': 16385, 'H4': 16388,
    'D1': 16408, 'W1': 32769, 'MN1': 49153,
}


def estimate_seconds_per_candle(tf_val: int) -> int:
    if tf_val <= 30:
        return tf_val * 60
    elif tf_val < 16385:
        return tf_val * 60
    elif tf_val < 16408:
        return 3600
    elif tf_val < 32769:
        return 86400
    elif tf_val < 49153:
        return 604800
    else:
        return 2592000


CANDLE_SIZE = 48


def parse_candles(body: bytes) -> list:
    candles = []
    num = len(body) // CANDLE_SIZE
    off = 0
    for _ in range(num):
        ts = struct.unpack_from('<i', body, off)[0]
        o = struct.unpack_from('<d', body, off + 4)[0]
        h = struct.unpack_from('<d', body, off + 12)[0]
        l = struct.unpack_from('<d', body, off + 20)[0]
        c = struct.unpack_from('<d', body, off + 28)[0]
        vol = struct.unpack_from('<q', body, off + 36)[0]
        spread = struct.unpack_from('<i', body, off + 44)[0]
        candles.append({
            'time': ts_to_dt(ts),
            'timestamp': ts,
            'open': o, 'high': h, 'low': l, 'close': c,
            'tick_volume': vol, 'spread': spread,
        })
        off += CANDLE_SIZE
    return candles


# --- Constants ---

TRADE_MARKET = 3
TRADE_INSTANT = 2
TRADE_REQUEST = 1
TRADE_EXCHANGE = 4
TRADE_PENDING = 5
TRADE_MODIFY = 6
TRADE_MODIFY_ORDER = 7
TRADE_CANCEL = 8

TYPE_BUY = 0
TYPE_SELL = 1

FILL_FOK = 0
FILL_IOC = 1
FILL_RETURN = 2

TIME_GTC = 0
TIME_DAY = 1
TIME_SPECIFIED = 2

TRADE_DISABLED = 0
TRADE_LONGONLY = 1
TRADE_SHORTONLY = 2
TRADE_CLOSEONLY = 3
TRADE_FULL = 4

LOT_MULTIPLIER = 100_000_000


def lots_to_volume(lots: float) -> int:
    return int(lots * LOT_MULTIPLIER)


def volume_to_lots(volume: int) -> float:
    return volume / LOT_MULTIPLIER


# --- Command names (debug) ---

CMD_NAMES = {
    0: 'AUTH', 2: 'LOGOUT', 3: 'ACCOUNT', 4: 'POSITIONS', 5: 'DEALS',
    6: 'SYMBOLS_FULL', 7: 'SUBSCRIBE', 8: 'QUOTES', 9: 'CATEGORIES',
    11: 'RATES', 12: 'TRADE', 14: 'ACCT_UPDATE', 15: 'SYSTEM',
    17: 'SYMBOL_SPEC', 19: 'TRADE_EVENT', 20: 'SPREADS',
    22: 'POS_UPDATE', 28: 'LOGIN', 34: 'SYMBOLS_GZ', 42: 'NOTIFY', 51: 'HEARTBEAT',
}
