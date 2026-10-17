"""Validation tests ensuring image providers cannot inadvertently generate live images in tests."""

import os
import unittest

from providers.gemini_image_provider import GeminiImageProvider
from providers.image_provider import ImageProviderError
from providers.openai_flare_image_provider import OpenAIFlareImageProvider
from providers.openai_image_provider import OpenAIImageProvider


class TestImageMockValidation(unittest.TestCase):
    def test_gemini_image_provider_fails_without_credentials(self) -> None:
        old_key = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(ImageProviderError):
                GeminiImageProvider()
        finally:
            if old_key is not None:
                os.environ["GEMINI_API_KEY"] = old_key

    def test_openai_image_provider_fails_without_credentials(self) -> None:
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        old_open_key = os.environ.pop("OPEN_API_KEY", None)
        try:
            with self.assertRaises(ImageProviderError):
                OpenAIImageProvider()
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_open_key is not None:
                os.environ["OPEN_API_KEY"] = old_open_key

    def test_openai_flare_image_provider_fails_without_credentials(self) -> None:
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        old_open_key = os.environ.pop("OPEN_API_KEY", None)
        try:
            with self.assertRaises(ImageProviderError):
                OpenAIFlareImageProvider()
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_open_key is not None:
                os.environ["OPEN_API_KEY"] = old_open_key


if __name__ == "__main__":
    unittest.main()
