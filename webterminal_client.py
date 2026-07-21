import asyncio
import json
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

WEB_TERMINAL_URL = "https://15.206.31.153:443/terminal"


class MT5WebClient:
    def __init__(self, login: int, password: str, server: str):
        self.login = login
        self.password = password
        self.server = server
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._connected = False
        self._ws_frames = []

    async def connect(self):
        log.info("Starting browser...")
        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--ignore-certificate-errors']
        )
        self._context = await self._browser.new_context(ignore_https_errors=True)
        self._page = await self._context.new_page()

        def on_ws(ws):
            ws.on('framesent', lambda p: self._ws_frames.append(('SENT', bytes(p))) if isinstance(p, (bytes, bytearray)) else None)
            ws.on('framereceived', lambda p: self._ws_frames.append(('RECV', bytes(p))) if isinstance(p, (bytes, bytearray)) else None)

        self._page.on('websocket', on_ws)

        log.info("Loading terminal...")
        await self._page.goto(WEB_TERMINAL_URL, timeout=30000)
        await asyncio.sleep(3)

        log.info("Logging in...")
        await self._page.evaluate(f'''() => {{
            const login = document.querySelector('input[name="login"]');
            const pwd = document.querySelector('input[name="password"]');
            if (login) {{ login.value = '{self.login}'; login.dispatchEvent(new Event('input', {{bubbles: true}})); }}
            if (pwd) {{ pwd.value = '{self.password}'; pwd.dispatchEvent(new Event('input', {{bubbles: true}})); }}
            document.querySelectorAll('button[type="submit"]')[0]?.click();
        }}''')

        await asyncio.sleep(15)
        self._connected = True
        title = await self._page.title()
        log.info(f"Connected! Title: {title}")

    async def _eval(self, js_code):
        return await self._page.evaluate(js_code)

    async def get_account_info(self):
        text = await self._eval('() => document.body.innerText')
        info = {}
        for key, pattern in [
            ('balance', r'Balance:\s*([\d.,]+)'),
            ('equity', r'Equity:\s*([\d.,]+)'),
            ('margin', r'Margin:\s*([\d.,]+)'),
            ('free_margin', r'Free margin:\s*([\d.,]+)'),
            ('level', r'Level:\s*([\d.,]+%?)'),
            ('currency', r'(USD|EUR|GBP|JPY|BTC)\s*$'),
        ]:
            import re
            m = re.search(pattern, text)
            if m:
                info[key] = m.group(1)

        info['login'] = self.login
        info['server'] = self.server
        info['title'] = await self._eval('() => document.title')
        return info

    async def get_quotes(self):
        result = await self._eval('''() => {
            const quotes = [];
            const rows = document.querySelectorAll('tr');
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 3) {
                    const sym = cells[0]?.textContent?.trim();
                    const bid = cells[1]?.textContent?.trim();
                    const ask = cells[2]?.textContent?.trim();
                    if (sym && bid && ask && /[a-zA-Z]/.test(sym) && /[\\d.]/.test(bid)) {
                        const b = parseFloat(bid.replace(/\\s/g, ''));
                        const a = parseFloat(ask.replace(/\\s/g, ''));
                        if (!isNaN(b) && !isNaN(a)) {
                            quotes.push({symbol: sym, bid: b, ask: a});
                        }
                    }
                }
            });
            return quotes;
        }''')
        return result

    async def get_positions(self):
        result = await self._eval('''() => {
            const rows = document.querySelectorAll('tr[data-id]');
            const positions = [];
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 8) {
                    positions.push({
                        symbol: cells[0]?.textContent?.trim() || '',
                        ticket: cells[1]?.textContent?.trim() || '',
                        time: cells[2]?.textContent?.trim() || '',
                        type: cells[3]?.textContent?.trim() || '',
                        volume: cells[4]?.textContent?.trim() || '',
                        open_price: cells[5]?.textContent?.trim() || '',
                        sl: cells[6]?.textContent?.trim() || '',
                        tp: cells[7]?.textContent?.trim() || '',
                        price: cells[8]?.textContent?.trim() || '',
                        swap: cells[9]?.textContent?.trim() || '',
                        profit: cells[10]?.textContent?.trim() || '',
                    });
                }
            });
            return positions;
        }''')
        return result

    async def place_order(self, symbol: str, order_type: str, volume: float,
                          price: float = None, sl: float = None, tp: float = None,
                          comment: str = ""):
        log.info(f"Placing order: {symbol} {order_type} {volume}")

        await self._eval('''() => {
            const btn = document.querySelector('button');
            const allBtns = document.querySelectorAll('button');
            for (const b of allBtns) {
                if (b.textContent.includes('Create New Order') || b.textContent.includes('New Order')) {
                    b.click();
                    return true;
                }
            }
            return false;
        }''')
        await asyncio.sleep(2)

        result = await self._eval(f'''() => {{
            const inputs = document.querySelectorAll('input, select');
            let info = [];
            inputs.forEach(i => {{
                info.push({{
                    tag: i.tagName,
                    type: i.type,
                    name: i.name,
                    placeholder: i.placeholder,
                    value: i.value,
                    visible: i.offsetParent !== null,
                    options: i.tagName === 'SELECT' ? Array.from(i.options).map(o => ({{value: o.value, text: o.text}})) : undefined
                }});
            }});
            return info;
        }}''')
        log.info(f"Order form elements: {json.dumps(result, indent=2)}")
        return result

    async def close_position(self, ticket: str):
        log.info(f"Closing position {ticket}")
        result = await self._eval(f'''() => {{
            const rows = document.querySelectorAll('tr[data-id="{ticket}"]');
            if (rows.length > 0) {{
                const closeBtn = rows[0].querySelector('button[class*="close"], .close-btn, [class*="Close"]');
                if (closeBtn) {{ closeBtn.click(); return "clicked"; }}
                rows[0].dispatchEvent(new Event('dblclick', {{bubbles: true}}));
                return "double-clicked";
            }}
            return "not found";
        }}''')
        return result

    async def get_symbol_info(self, symbol: str):
        text = await self._eval('() => document.body.innerText')
        import re
        pattern = rf'{re.escape(symbol)}\s+([\d.,]+)\s+([\d.,]+)'
        m = re.search(pattern, text)
        if m:
            return {'symbol': symbol, 'bid': float(m.group(1).replace(' ', '')),
                    'ask': float(m.group(2).replace(' ', ''))}
        return None

    async def disconnect(self):
        if self._browser:
            await self._browser.close()
        self._connected = False
        log.info("Disconnected")


async def main():
    client = MT5WebClient(
        login=463558919,
        password="Trade@123",
        server="Exness-MT5Trial17"
    )

    try:
        await client.connect()

        print("\n=== ACCOUNT INFO ===")
        info = await client.get_account_info()
        for k, v in info.items():
            print(f"  {k}: {v}")

        print("\n=== LIVE QUOTES ===")
        quotes = await client.get_quotes()
        for q in quotes:
            print(f"  {q['symbol']:12s} Bid={q['bid']:>12.2f}  Ask={q['ask']:>12.2f}")

        print("\n=== OPEN POSITIONS ===")
        positions = await client.get_positions()
        if positions:
            for pos in positions:
                print(f"  {pos}")
        else:
            print("  No open positions")

        print("\n=== BTCUSDm INFO ===")
        btc = await client.get_symbol_info("BTCUSDm")
        if btc:
            print(f"  {btc}")

    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
