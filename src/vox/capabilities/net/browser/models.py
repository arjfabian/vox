from dataclasses import dataclass


@dataclass
class BrowserConfig:
    headless: bool = True
    timeout_ms: int = 30000
    viewport_width: int = 1280
    viewport_height: int = 900
    locale: str = "pt-BR"