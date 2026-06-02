from dataclasses import dataclass


@dataclass(slots=True)
class TelegramConfig:
    bot_token: str
    user_id: str
    long_timeout: int = 20