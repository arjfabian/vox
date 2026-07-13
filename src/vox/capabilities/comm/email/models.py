from dataclasses import dataclass


@dataclass
class EmailConfig:
    host: str
    port: int
    user: str
    password: str
