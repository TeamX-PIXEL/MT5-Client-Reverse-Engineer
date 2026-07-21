#!/usr/bin/env python3
"""
The web terminal might send trades via HTTP, not WebSocket.
Intercept ALL network requests when clicking Buy.
"""
import asyncio, struct, time
from playwright.async_api import async_playwright
from Crypto.Cipher import AES

STATIC_KEY = bytes.fromhex('02de02a1a65cc794684fcbea1ecb0fd74ae657e43662c11eee885d2fd64f4964')
ZERO_IV = bytes(16)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = await browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})

        # Intercept ALL requests
        http_requests = []
        async def handle_request(request):
            http_requests.append({
                'time': time.time(),
                'method': request.method,
                'url': request.url,
                'resource_type': request.resource_type,
                'post_data': request.post_data,
                'headers': dict(request.headers) if request.headers else {}
            })

        page = await context.new_page()
        page.on('request', handle_request)

        # Also intercept responses
        http_responses = []
        async def handle_response(response):
            body = None
            try:
                body = await response.text()
            except: pass
            http_responses.append({
                'time': time.time(),
                'url': response.url,
                'status': response.status,
                'body': body[:500] if body else None
            })

        page.on('response', handle_response)

        print("[*] Opening web terminal...")
        await page.goto('https://15.206.31.153:443/terminal', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)

        # Login
        await page.fill('input[name="login"]', '463558919')
        await page.fill('input[name="password"]', 'Trade@123')
        http_requests.clear()
        http_responses.clear()
        await page.click('button:has-text("Connect to account")')
        await asyncio.sleep(12)

        print(f"[+] After login: {len(http_requests)} HTTP requests")
        for req in http_requests:
            if '15.206' in req['url'] or 'exness' in req['url'].lower():
                print(f"  {req['method']} {req['url'][:100]}")
                if req['post_data']:
                    print(f"    POST data: {req['post_data'][:200]}")

        # Clear and open order dialog
        http_requests.clear()
        http_responses.clear()
        await page.locator('text=Create New Order').first.click()
        await asyncio.sleep(2)

        print(f"\n[+] After 'Create New Order': {len(http_requests)} HTTP requests")
        for req in http_requests:
            if '15.206' in req['url'] or 'trade' in req['url'].lower():
                print(f"  {req['method']} {req['url'][:100]}")
                if req['post_data']:
                    print(f"    POST data: {req['post_data'][:200]}")

        # Now click Buy by Market and watch for HTTP requests
        http_requests.clear()
        http_responses.clear()

        print("\n[*] Clicking 'Buy by Market'...")
        await page.locator('button:has-text("Buy by Market")').first.click()
        await asyncio.sleep(5)

        # Screenshot
        await page.screenshot(path='/media/teamx/New Volume/AlgoMinds/MT5_API_Test/screenshot_after_buy.png')

        print(f"\n[+] After 'Buy by Market' click: {len(http_requests)} HTTP requests")
        for req in http_requests:
            print(f"  {req['method']} {req['url'][:120]}")
            if req['post_data']:
                print(f"    POST data ({len(req['post_data'])} bytes): {req['post_data'][:500]}")

        print(f"\n[+] HTTP responses: {len(http_responses)}")
        for resp in http_responses:
            if '15.206' in resp['url'] or 'trade' in resp['url'].lower() or 'order' in resp['url'].lower():
                print(f"  {resp['status']} {resp['url'][:120]}")
                if resp['body']:
                    print(f"    Body: {resp['body'][:300]}")

        # Check if there's a confirmation dialog
        print("\n[*] Checking for confirmation dialogs...")
        dialogs = await page.evaluate("""() => {
            const result = [];
            document.querySelectorAll('[class*="modal"], [class*="dialog"], [class*="confirm"], [class*="popup"], [role="dialog"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    result.push({
                        tag: el.tagName,
                        class: el.className.substring(0, 100),
                        text: el.textContent.substring(0, 200),
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        w: Math.round(rect.width), h: Math.round(rect.height)
                    });
                }
            });
            return result;
        }""")
        print(f"  Dialogs found: {len(dialogs)}")
        for d in dialogs:
            print(f"    [{d['tag']}] class='{d['class'][:60]}' text='{d['text'][:100]}'")

        # Also check if trade might be sent via the page's internal API
        # by hooking fetch and XMLHttpRequest
        print("\n[*] Now trying with fetch/XHR hooks...")
        await page.evaluate("""() => {
            window.__fetch_calls = [];
            window.__xhr_calls = [];

            const origFetch = window.fetch;
            window.fetch = function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
                const opts = args[1] || {};
                window.__fetch_calls.push({
                    time: Date.now(),
                    url: url,
                    method: opts.method || 'GET',
                    body: opts.body ? (typeof opts.body === 'string' ? opts.body.substring(0, 500) : 'binary') : null,
                    headers: opts.headers ? JSON.parse(JSON.stringify(opts.headers)) : null
                });
                return origFetch.apply(this, args);
            };

            const origXHROpen = XMLHttpRequest.prototype.open;
            const origXHRSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__method = method;
                this.__url = url;
                return origXHROpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
                window.__xhr_calls.push({
                    time: Date.now(),
                    url: this.__url,
                    method: this.__method,
                    body: body ? (typeof body === 'string' ? body.substring(0, 500) : 'binary') : null
                });
                return origXHRSend.apply(this, arguments);
            };
        }""")

        # Close order dialog and reopen
        try:
            await page.keyboard.press('Escape')
            await asyncio.sleep(1)
        except: pass

        await page.locator('text=Create New Order').first.click()
        await asyncio.sleep(2)

        # Click Buy by Market
        await page.locator('button:has-text("Buy by Market")').first.click()
        await asyncio.sleep(5)

        # Check fetch/XHR calls
        fetch_calls = await page.evaluate("window.__fetch_calls")
        xhr_calls = await page.evaluate("window.__xhr_calls")

        print(f"\n[+] Fetch calls after Buy: {len(fetch_calls)}")
        for fc in fetch_calls:
            print(f"  {fc['method']} {fc['url'][:120]}")
            if fc['body']:
                print(f"    Body: {fc['body'][:300]}")
            if fc['headers']:
                print(f"    Headers: {fc['headers']}")

        print(f"\n[+] XHR calls after Buy: {len(xhr_calls)}")
        for xc in xhr_calls:
            print(f"  {xc['method']} {xc['url'][:120]}")
            if xc['body']:
                print(f"    Body: {xc['body'][:300]}")

        await browser.close()

asyncio.run(main())
