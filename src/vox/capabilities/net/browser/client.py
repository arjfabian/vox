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
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    async def new_page(self):
        if not self.context:
            raise RuntimeError("Browser not started")
        return await self.context.new_page()
