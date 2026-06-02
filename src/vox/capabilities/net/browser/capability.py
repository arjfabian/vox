"""net.browser — VOX headless browser capability (Playwright Firefox)."""

import json
import time
import re

from vox.capabilities import VOXCapability

from .client import BrowserClient
from .models import BrowserConfig


class BrowserCapability(VOXCapability):

    PARAMS = {
        "BROWSER_HEADLESS": ["Run browser in headless mode", True],
        "BROWSER_TIMEOUT":  ["Page load timeout in ms", 30000],
    }

    async def boot(self) -> None:
        config = BrowserConfig(
            headless=self.BROWSER_HEADLESS,
            timeout_ms=int(self.BROWSER_TIMEOUT),
        )
        self._client = BrowserClient(config)
        await self._client.start()
        self._agent.logger.ok("Browser online.")

    async def shutdown(self) -> None:
        if hasattr(self, "_client"):
            await self._client.stop()
        if hasattr(self, "_agent"):
            self._agent.logger.info("Browser offline.")

    async def capture_page(self, url: str) -> str:
        self._agent.logger.info("Capturing page...")
        filename = f"capture_{int(time.time() * 1000)}.png"
        path = self._agent.get_safe_path("evidence", filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        page = await self._client.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=int(self.BROWSER_TIMEOUT))
            await page.screenshot(path=str(path), full_page=True)
            self._agent.logger.ok(f"Captured: {path}")
            return str(path)
        except Exception as e:
            self._agent.logger.error(f"Capture failed: {e}")
            return ""
        finally:
            await page.close()

    async def get_text(self, url: str) -> str:
        self._agent.logger.info("Extracting text...")
        page = await self._client.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=int(self.BROWSER_TIMEOUT))
            return await page.inner_text("body")
        except Exception as e:
            self._agent.logger.error(f"Text extraction failed: {e}")
            return ""
        finally:
            await page.close()

    async def _capture_page_data(self, url: str):
        self._agent.logger.info("Capturing page data...")
        page = await self._client.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(self.BROWSER_TIMEOUT))
            await page.wait_for_timeout(3000)
            filename = f"analysis_{int(time.time() * 1000)}.png"
            screenshot_path = self._agent.get_safe_path("evidence", filename)
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path), full_page=True)
            visible_text = await page.inner_text("body")
            try:
                accessibility_tree = await page.accessibility.snapshot()
            except Exception as e:
                self._agent.logger.warning(f"Accessibility snapshot failed: {e}")
                accessibility_tree = {}
            return screenshot_path, visible_text, accessibility_tree
        finally:
            await page.close()

    async def analyze_page(self, url: str, instruction: str):
        self._agent.logger.info("Analyzing page...")
        try:
            screenshot_path, visible_text, accessibility_tree = await self._capture_page_data(url)
        except Exception as e:
            self._agent.logger.error(f"Page capture failed [{url}]: {e}")
            return []

        system = (
            "You are a browser perception agent.\n\n"
            f"Task:\n{instruction}\n\nReturn ONLY valid JSON.\n"
        )
        user = (
            f"URL: {url}\n\n"
            f"VISIBLE TEXT:\n{visible_text[:15000]}\n\n"
            f"ACCESSIBILITY TREE:\n{json.dumps(accessibility_tree)[:15000]}\n"
        )

        ollama = getattr(self._agent, "cap_ollama", None)
        if not ollama:
            self._agent.logger.error("ai.ollama missing")
            return []

        response = await ollama.generate_vision(
            system=system, prompt=user, image_path=str(screenshot_path),
        )
        return self._parse_json(response)

    def _parse_json(self, text: str):
        if not text:
            return []
        text = text.strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except Exception:
            self._agent.logger.warning("Failed to parse LLM output as JSON directly")
        match = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1).strip())
                if isinstance(data, list):
                    return data
            except Exception:
                self._agent.logger.warning("Failed to parse JSON code block from LLM output")
        self._agent.logger.warning("Invalid JSON from model")
        return []
