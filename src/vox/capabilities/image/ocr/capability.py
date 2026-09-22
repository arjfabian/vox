"""image.ocr — generic image-to-text (OCR) capability.

Transcribes text from raster image bytes and returns it as plain text. The
capability is deliberately generic: it knows nothing about the origin or
purpose of the image, about messages, channels, roles, or personas. Its only
input is ``source`` (image bytes); its only output is the extracted text
(``str``).

The OCR engine is provided by the generic ``ai.llm`` capability's vision
inference, reached exclusively via the capability host ``get_capability`` —
the same provider pattern used by ``ai.parsing``. ``image.ocr`` owns the OCR
semantics (verbatim transcription instruction, input validation, result
normalization); the provider owns the model/backend and receives the raw
image bytes — there is no intermediate file. Swapping the engine (e.g. a local
OCR library) is confined to this module and never changes the public
``extract(source)`` contract.
"""

from __future__ import annotations

from typing import Any

from vox.capabilities.base import VOXCapability

# Transcription instructions are owned here, inside the capability surface:
# consumers and the provider never see or override OCR semantics.
OCR_SYSTEM_PROMPT = (
    "You are an OCR engine. Your only task is to transcribe text exactly as "
    "it appears in the provided image. Never summarize, infer, explain, or "
    "answer questions about the image content."
)

OCR_PROMPT = (
    "Transcribe all visible text verbatim, preserving line breaks and reading "
    "order. Output only the extracted text. If the image contains no readable "
    "text, output an empty string."
)

# Recognized raster image signatures (magic bytes). Anything else is rejected
# clearly instead of being forwarded to the provider, so an unreadable input
# can never surface as fabricated text.
_IMAGE_MAGIC = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",  # GIF
    b"GIF89a",  # GIF
)


def _is_raster_image(source: bytes) -> bool:
    """Return True when ``source`` begins with a common raster image signature."""
    if source.startswith(_IMAGE_MAGIC):
        return True
    if source[:4] == b"RIFF" and source[8:12] == b"WEBP":
        return True
    if source[:2] == b"BM":  # BMP
        return True
    return source[:2] in (b"II", b"MM") and source[2:4] in (b"*\x00", b"\x00*")


class OCRUnavailableError(RuntimeError):
    """Raised when no OCR engine (e.g. the ai.llm vision provider) is mounted."""


class OCRCapability(VOXCapability):
    CAPABILITY_NAME = "image.ocr"

    _llm: Any | None = None

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        self._llm = self.get_capability("ai.llm")

    async def shutdown(self) -> None:
        self._llm = None

    # -- public operation -----------------------------------------------------

    async def extract(
        self,
        source: bytes,
        *,
        adapter: str,
        model: str | None = None,
    ) -> str:
        """OCR the given image bytes and return the extracted text.

        Args:
            source: Raw image bytes (any common raster format).
            adapter: The ai.llm adapter to use for vision inference
                (execution-time selection — ``image.ocr`` never picks a provider
                implicitly and knows nothing about the vision backend).
            model: Optional per-operation ai.llm model override.

        Returns:
            The extracted text, or ``""`` when the input is empty or the image
            contains no readable text.

        Raises:
            ValueError: ``source`` is not recognizable raster image data.
            OCRUnavailableError: No OCR engine is mounted.
        """
        if not source:
            return ""

        if not _is_raster_image(source):
            self.warning("Refusing unsupported image data")
            raise ValueError("unsupported image data: expected raster image bytes")

        if self._llm is None:
            self.warning("ai.llm vision provider unavailable; OCR failed")
            raise OCRUnavailableError("image.ocr provider ai.llm is not mounted")

        raw = await self._llm.generate_vision(
            system=OCR_SYSTEM_PROMPT,
            prompt=OCR_PROMPT,
            image_bytes=source,
            adapter=adapter,
            model=model,
        )

        text = (raw or "").strip()
        self.log(f"Extracted {len(text)} characters of text")
        return text
