"""comm.email — Asynchronous email dispatch capability.

Sends plain-text emails via SMTP using credentials from the
bound agent's local .env file (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS).
"""

from vox.capabilities.base import VOXCapability

from .client import EmailClient
from .models import EmailConfig


class EmailCapability(VOXCapability):

    CAPABILITY_NAME = "comm.email"

    PARAMS = {
        "SMTP_HOST": ["SMTP server hostname", None],
        "SMTP_PORT": ["SMTP server port", 587],
        "SMTP_USER": ["SMTP authentication username", None],
        "SMTP_PASS": ["SMTP authentication password", None],
    }

    SENSITIVE_PARAMS = {"SMTP_USER", "SMTP_PASS"}

    EXPOSED_COMMANDS = [
        {
            "name": "send_email",
            "description": "Send an automated plain-text email to a recipient.",
            "method": "send_email",
        },
    ]

    _client: EmailClient

    @classmethod
    async def health_check(cls) -> bool:
        return True

    def _build_client(self) -> EmailClient:
        config = EmailConfig(
            host=str(self.SMTP_HOST),
            port=int(self.SMTP_PORT),
            user=str(self.SMTP_USER),
            password=str(self.SMTP_PASS),
        )
        return EmailClient(config)

    async def boot(self) -> None:
        self._client = self._build_client()

    async def shutdown(self) -> None:
        pass

    async def send_email(self, to: str, subject: str, body: str) -> dict:
        self.log(f"Sending email to {to}: {subject}")
        ok = await self._client.send(to, subject, body)
        if ok:
            self.log(f"Email sent to {to}")
            return {"status": "ok", "to": to, "subject": subject}
        self.log(f"Failed to send email to {to}")
        return {"status": "error", "to": to, "subject": subject}
