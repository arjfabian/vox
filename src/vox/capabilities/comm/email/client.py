from email.message import EmailMessage

import aiosmtplib

from .models import EmailConfig


class EmailClient:
    def __init__(self, config: EmailConfig) -> None:
        self._config = config

    async def send(self, to: str, subject: str, body: str) -> bool:
        msg = EmailMessage()
        msg["From"] = self._config.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.user,
                password=self._config.password,
                use_tls=self._config.port == 465,
                start_tls=self._config.port == 587,
            )
            return True
        except Exception:  # noqa: BLE001 — SMTP send failure returns False
            return False
