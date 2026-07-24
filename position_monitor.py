"""
Live Position Monitor — cmd10 callbacks + cmd4 profit poll + cmd3 balance poll
Place trades on MT5 terminal and watch them appear instantly with live profit.
"""
import asyncio
import sys
import time

sys.path.insert(0, '/media/teamx/New Volume/AlgoMinds/MT5_API_Test/OwnMt5API/MT5API')
from mt5client import MT5Client

LOGIN = 270146102
PASSWORD = 'Trade@123'
SERVER = 'Exness-MT5Trial17'

seen_tickets = set()

def on_position(event_type, pos):
    global seen_tickets
    ts = time.strftime('%H:%M:%S')
    type_str = "BUY" if pos.type == 0 else "SELL"

    marker = ""
    if event_type == 'open':
        marker = " [OPENED]"
        seen_tickets.add(pos.ticket)
    elif event_type == 'close':
        marker = " [CLOSED]"
        seen_tickets.discard(pos.ticket)
    elif event_type == 'volume':
        marker = " [VOLUME CHANGED]"
    else:
        marker = " [UPDATED]"

    magic_str = f" magic={pos.magic}" if pos.magic else ""
    comment_str = f" {pos.comment}" if pos.comment else ""
    sl_str = f" SL={pos.sl:.5f}" if pos.sl else ""
    tp_str = f" TP={pos.tp:.5f}" if pos.tp else ""

    print(f"[{ts}] #{pos.ticket} {pos.symbol} {type_str} {pos.volume} lots "
          f"@ {pos.price:.5f}{sl_str}{tp_str} "
          f"profit={pos.profit:.2f}{magic_str}{comment_str}{marker}")

def on_account(acct):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] ACCOUNT: balance={acct.balance:.2f} equity={acct.equity:.2f} "
          f"profit={acct.profit:.2f} margin={acct.margin:.2f}")

async def main():
    print("=" * 70)
    print("LIVE POSITION MONITOR (cmd10 + cmd4 profit + cmd3 balance)")
    print(f"Account: {LOGIN} ({SERVER})")
    print("Place/close trades on MT5 terminal — updates appear instantly")
    print("Press Ctrl+C to stop")
    print("=" * 70)
    print()

    async with MT5Client(login=LOGIN, password=PASSWORD, server=SERVER) as client:
        client.on_position(on_position)
        client.on_account(on_account)

        positions = await client.get_positions()
        print(f"[{time.strftime('%H:%M:%S')}] Initial: {len(positions)} positions")
        for p in positions:
            seen_tickets.add(p.ticket)
            type_str = "BUY" if p.type == 0 else "SELL"
            print(f"  #{p.ticket} {p.symbol} {type_str} {p.volume} lots "
                  f"@ {p.price:.5f} profit={p.profit:.2f}")

        if client._account:
            print(f"  Balance: {client._account.balance:.2f}")
        print()
        print(">>> Waiting for live updates... <<<")
        print()

        while True:
            await asyncio.sleep(1)

asyncio.run(main())
