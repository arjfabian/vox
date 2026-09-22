"""Focused tests for the image.ocr capability.

Covers the YAML contract, the generic ``extract(source: bytes) -> str``
contract, end-to-end OCR against a mocked ``ai.llm`` vision provider reached
through the host, empty/invalid input behaviour, provider failure behaviour,
capability metadata/registration, and the decoupling guards that keep the
capability generic.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.capabilities.base import VOXBoundCapability
from vox.capabilities.image.ocr import OCRCapability, OCRUnavailableError

ROOT = Path(__file__).resolve().parent.parent
OCR_DIR = ROOT / "src" / "vox" / "capabilities" / "image" / "ocr"
CAPABILITIES = ROOT / "src" / "vox" / "capabilities"

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00" + b"\x00" * 16
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00" + b"\x00" * 16
GIF_BYTES = b"GIF87a\x00" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


async def _bound_capability(llm=None):
    """Build a mounted OCRCapability bound proxy with a stubbed host."""
    OCRCapability.load_contract(OCR_DIR / "capability.py")
    cap = OCRCapability()
    cap.id = "image.ocr"
    cap.logger = MagicMock()

    host = MagicMock()
    host.get_capability.return_value = llm
    host.logger = MagicMock()
    bound = VOXBoundCapability(cap, host, {})
    await bound.boot()
    return bound


class TestOcrContract(unittest.TestCase):
    def test_class_name_matches_yaml_name(self):
        contract = OCRCapability.load_contract(OCR_DIR / "capability.py")
        self.assertIsNotNone(contract)
        self.assertEqual(contract.name, "image.ocr")
        self.assertEqual(OCRCapability.CAPABILITY_NAME, "image.ocr")

    def test_registration_id_derives_from_directory(self):
        # The registry derives cap_id from the directory path under
        # src/vox/capabilities, so on-disk layout must equal the YAML name.
        cap_id = ".".join(OCR_DIR.relative_to(CAPABILITIES).parts)
        self.assertEqual(cap_id, "image.ocr")

    def test_module_imports_like_registry_discovery(self):
        module = importlib.import_module("vox.capabilities.image.ocr")
        self.assertIs(module.OCRCapability, OCRCapability)

    def test_provides_ocr(self):
        contract = OCRCapability.load_contract(OCR_DIR / "capability.py")
        self.assertIn("ocr", contract.provides)

    def test_no_config_params_or_secrets(self):
        """OCR config comes from the ai.llm provider, not its own params."""
        contract = OCRCapability.load_contract(OCR_DIR / "capability.py")
        self.assertEqual(contract.params, {})
        self.assertEqual(contract.secrets, {})

    def test_public_api_is_small_and_capability_oriented(self):
        methods = {
            name
            for name, member in inspect.getmembers(OCRCapability)
            if not name.startswith("_") and callable(member)
        }
        self.assertIn("extract", methods)
        self.assertNotIn("generate", methods)
        self.assertNotIn("generate_vision", methods)
        self.assertNotIn("chat", methods)


class TestExtractContract(unittest.TestCase):
    def test_extract_signature_uses_generic_source_and_explicit_adapter(self):
        params = inspect.signature(OCRCapability.extract).parameters
        self.assertEqual(list(params), ["self", "source", "adapter", "model"])

    def test_returns_extracted_text_from_vision_provider(self):
        async def scenario():
            llm = MagicMock()
            llm.generate_vision = AsyncMock(
                return_value="  Caffe Vita Espresso\n"
                "-- 1 Coffee .... 4.00\n-- Total ... 4.00\n"
            )
            bound = await _bound_capability(llm)
            text = await bound.extract(source=JPEG_BYTES, adapter="ollama")
            self.assertEqual(
                text,
                "Caffe Vita Espresso\n-- 1 Coffee .... 4.00\n-- Total ... 4.00",
            )
            # Provider is reached through the host, never imported.
            bound._host.get_capability.assert_called_once_with("ai.llm")
            llm.generate_vision.assert_awaited_once()
            call = llm.generate_vision.await_args.kwargs
            self.assertEqual(call.get("image_bytes"), JPEG_BYTES)
            self.assertNotIn("image_path", call)
            self.assertIn("system", call)
            self.assertIn("prompt", call)
            self.assertEqual(call.get("adapter"), "ollama")
            # Raw source bytes go straight to the provider: no temp file.
            bound._host.get_safe_path.assert_not_called()
        asyncio.run(scenario())

    def test_blank_or_missing_provider_output_becomes_empty_string(self):
        async def scenario():
            llm = MagicMock()
            llm.generate_vision = AsyncMock(return_value="   \n")
            bound = await _bound_capability(llm)
            self.assertEqual(await bound.extract(source=JPEG_BYTES, adapter="ollama"), "")
        asyncio.run(scenario())


class TestEmptyInput(unittest.TestCase):
    def test_empty_bytes_return_empty_without_provider(self):
        async def scenario():
            llm = MagicMock()
            llm.generate_vision = AsyncMock()
            bound = await _bound_capability(llm)
            self.assertEqual(await bound.extract(source=b"", adapter="ollama"), "")
            llm.generate_vision.assert_not_called()
        asyncio.run(scenario())


class TestInvalidData(unittest.TestCase):
    def test_pdf_bytes_raise_without_provider(self):
        async def scenario():
            llm = MagicMock()
            llm.generate_vision = AsyncMock()
            bound = await _bound_capability(llm)
            with self.assertRaises(ValueError):
                await bound.extract(source=PDF_BYTES, adapter="ollama")
            llm.generate_vision.assert_not_called()
        asyncio.run(scenario())

    def test_non_image_bytes_raise_clearly(self):
        async def scenario():
            llm = MagicMock()
            bound = await _bound_capability(llm)
            with self.assertRaises(ValueError) as ctx:
                await bound.extract(source=b"hello world", adapter="ollama")
            self.assertIn("raster", str(ctx.exception))
            llm.generate_vision.assert_not_called()
        asyncio.run(scenario())

    def test_recognized_raster_formats_reach_provider(self):
        async def scenario():
            for image_bytes in (
                JPEG_BYTES,
                PNG_BYTES,
                GIF_BYTES,
                WEBP_BYTES,
            ):
                llm = MagicMock()
                llm.generate_vision = AsyncMock(return_value="text")
                bound = await _bound_capability(llm)
                self.assertEqual(await bound.extract(source=image_bytes, adapter="ollama"), "text")
                llm.generate_vision.assert_awaited_once()
        asyncio.run(scenario())


class TestProviderAvailability(unittest.TestCase):
    def test_no_provider_raises_ocr_unavailable(self):
        async def scenario():
            bound = await _bound_capability(None)
            with self.assertRaises(OCRUnavailableError):
                await bound.extract(source=JPEG_BYTES, adapter="ollama")
        asyncio.run(scenario())

    def test_ocr_unavailable_is_a_runtime_error(self):
        self.assertTrue(issubclass(OCRUnavailableError, RuntimeError))

    def test_provider_failure_propagates(self):
        async def scenario():
            llm = MagicMock()
            llm.generate_vision = AsyncMock(
                side_effect=RuntimeError("vision model not loaded")
            )
            bound = await _bound_capability(llm)
            with self.assertRaises(RuntimeError):
                await bound.extract(source=JPEG_BYTES, adapter="ollama")
            # The failure is surfaced, never converted into invented text, and
            # the provider receives the raw bytes (no temporary artifact).
            self.assertEqual(
                llm.generate_vision.await_args.kwargs.get("image_bytes"),
                JPEG_BYTES,
            )
            bound._host.get_safe_path.assert_not_called()
        asyncio.run(scenario())


class TestDecoupling(unittest.TestCase):
    def test_no_receipt_telegram_coupling(self):
        source = (OCR_DIR / "capability.py").read_text().lower()
        for token in ("receipt", "account", "expense", "document",
                      "chatrole", "telegram"):
            self.assertNotIn(token, source,
                             f"image.ocr must not reference {token}")
        # The capability must never import workload/orchestration/role modules.
        for silo in ("vox.workloads", "vox.orchestration", "vox.roles"):
            self.assertNotIn(silo, source)

    def test_no_direct_ai_llm_import(self):
        source = (OCR_DIR / "capability.py").read_text()
        self.assertNotIn("import vox.capabilities.ai.llm", source)
        self.assertNotIn("vox.capabilities.ai.llm", source)

    def test_no_concrete_llm_adapter_import(self):
        """image.ocr consumes the ai.llm port and never a concrete adapter."""
        source = (OCR_DIR / "capability.py").read_text().lower()
        for token in ("ollama", "gemini", "adapters", "gemini-2.5-flash"):
            self.assertNotIn(
                token, source, f"image.ocr must not reference {token}"
            )


if __name__ == "__main__":
    unittest.main()