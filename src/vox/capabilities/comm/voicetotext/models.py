from dataclasses import dataclass


@dataclass(slots=True)
class WhisperConfig:
    model: str
    device: str
    compute_type: str


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    language: str
    confidence: float
