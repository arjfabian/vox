"""net.browser — VOX headless browser capability (Playwright Firefox)."""

import time

from vox.capabilities import VOXCapability

from .client import BrowserClient
from .models import BrowserConfig


class BrowserCapability(VOXCapability):
    CAPABILITY_NAME = "net.browser"

    async def boot(self) -> None:
        config = BrowserConfig(
            headless=self.BROWSER_HEADLESS,
            timeout_ms=int(self.BROWSER_TIMEOUT),
        )
        self._client = BrowserClient(config)
        await self._client.start()
        self.ok("Browser online.")

    async def shutdown(self) -> None:
        if hasattr(self, "_client"):
            await self._client.stop()

    async def capture_page(self, url: str) -> str:
        self.log("Capturing page...")
        filename = f"capture_{int(time.time() * 1000)}.png"
        path = self.get_safe_path("evidence", filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        page = await self._client.new_page()
        try:
            await page.goto(
                url, wait_until="networkidle", timeout=int(self.BROWSER_TIMEOUT)
            )
            await page.screenshot(path=str(path), full_page=True)
            self.ok(f"Captured: {path}")
            return str(path)
        except Exception as e:  # noqa: BLE001 — screenshot returns empty
            self.error(f"Capture failed: {e}")
            return ""
        finally:
            await page.close()

    async def get_text(self, url: str) -> str:
        self.log("Extracting text...")
        page = await self._client.new_page()
        try:
            await page.goto(
                url, wait_until="networkidle", timeout=int(self.BROWSER_TIMEOUT)
            )
            return await page.inner_text("body")
        except Exception as e:  # noqa: BLE001 — text extraction returns empty
            self.error(f"Text extraction failed: {e}")
            return ""
        finally:
            await page.close()
