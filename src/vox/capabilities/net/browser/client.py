import asyncio
import os
import signal

from playwright.async_api import async_playwright


class BrowserClient:
    def __init__(self, config):
        self.config = config
        self._pw = None
        self.browser = None
        self.context = None

    async def start(self):
        self._pw = await async_playwright().start()
        self.browser = await self._pw.firefox.launch(headless=self.config.headless)
        self.context = await self.browser.new_context(
            ignore_https_errors=True,
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
            locale=self.config.locale,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0 Safari/537.36"
            ),
        )

    async def stop(self):
        # 1. Track the underlying process PID safely
        browser_pid = None
        try:
            if (
                hasattr(self, "browser")
                and self.browser
                and hasattr(self.browser, "_process")
            ):
                browser_pid = self.browser._process.pid
        except Exception:  # noqa: BLE001, S110 — best-effort PID extraction
            pass

        # 2. Fast-abort teardown with tight timeouts
        if hasattr(self, "context") and self.context:
            try:
                await asyncio.wait_for(self.context.close(), timeout=0.5)
            except BaseException:  # noqa: BLE001, S110 — teardown must not raise
                pass

        if hasattr(self, "browser") and self.browser:
            try:
                await asyncio.wait_for(self.browser.close(), timeout=0.5)
            except BaseException:  # noqa: BLE001, S110 — teardown must not raise
                pass

        if hasattr(self, "_pw") and self._pw:
            try:
                await asyncio.wait_for(self._pw.stop(), timeout=0.5)
            except BaseException:  # noqa: BLE001, S110 — teardown must not raise
                pass

        # 3. CRITICAL OS FALLBACK: Force kill if the process is still alive
        if browser_pid:
            try:
                os.kill(browser_pid, signal.SIGKILL)
                print(f"[browser] Hard-killed/verified browser PID {browser_pid}")
            except ProcessLookupError:
                pass
            except Exception as e:  # noqa: BLE001 — OS kill failure is non-fatal
                print(f"[browser] OS fallback kill failed: {e}")

    async def new_page(self):
        if not self.context:
            raise RuntimeError("Browser not started")
        return await self.context.new_page()
