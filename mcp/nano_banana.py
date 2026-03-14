# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import mimetypes
from typing import Literal, Optional

from google.adk.models.google_llm import Gemini
from google.genai import types

from media_models import MediaAsset
from storage_utils import upload_data_to_gcs

AUTHORIZED_URI = "https://storage.mtls.cloud.google.com/"
MAX_RETRIES = 5

# Prefer the newest image model first, then fall back to older ones.
IMAGE_MODELS = [
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
    "gemini-2.0-flash-preview-image-generation",
]

print("DEBUG: nano_banana loaded from:", __file__)

def _build_content(prompt: str, source_image_gcs_uris: list[str]) -> types.Content:
    """Builds a multimodal Content object: optional source images + text prompt."""
    parts = [
        types.Part.from_uri(
            file_uri=uri,
            mime_type=mimetypes.guess_type(uri)[0] or "image/jpeg",
        )
        for uri in source_image_gcs_uris
    ]
    parts.append(types.Part.from_text(text=prompt))
    return types.Content(role="user", parts=parts)


def _generate_with_model_fallback(
    genai_client,
    content: types.Content,
    aspect_ratio: str,
):
    """Calls Gemini image models with fallback using generate_content_stream."""
    generate_content_config = types.GenerateContentConfig(
        temperature=1,
        top_p=0.95,
        max_output_tokens=32768,
        response_modalities=["TEXT", "IMAGE"],
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
        ],
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
        ),
        thinking_config=types.ThinkingConfig(thinking_budget=-1),
    )

    for model_name in IMAGE_MODELS:
        try:
            logging.info(
                "Attempting image generation with model=%s, aspect_ratio=%s",
                model_name,
                aspect_ratio,
            )
            chunks = list(genai_client.models.generate_content_stream(
                model=model_name,
                contents=content,
                config=generate_content_config,
            ))
            return chunks
        except Exception as err:
            logging.warning("Image model %s failed: %s", model_name, err)

    return None


def _response_parts(chunks):
    """Collects all content parts from a list of streamed response chunks."""
    if not chunks:
        return []
    all_parts = []
    for chunk in chunks:
        try:
            for part in chunk.candidates[0].content.parts:
                all_parts.append(part)
        except (AttributeError, IndexError):
            pass
    return all_parts


async def _asset_from_part(part) -> Optional[MediaAsset]:
    """Extracts a MediaAsset from a single response part (file_data or inline_data)."""
    file_data = getattr(part, "file_data", None)
    if file_data and file_data.file_uri:
        return MediaAsset(uri=file_data.file_uri)

    inline_data = getattr(part, "inline_data", None)
    if not inline_data or not inline_data.data:
        return None

    gcs_uri = await upload_data_to_gcs(
        "mcp-tools",
        inline_data.data,
        inline_data.mime_type,  # type: ignore
    )
    return MediaAsset(uri=gcs_uri)


async def _extract_asset_from_response(response) -> tuple[MediaAsset, str]:
    """Walks the response, collecting any text and returning the first image asset."""
    response_text = ""
    for part in _response_parts(response):
        if getattr(part, "text", None) and not getattr(part, "thought", None):
            response_text += part.text

        asset = await _asset_from_part(part)
        if asset:
            return asset, response_text

    return MediaAsset(uri=""), response_text


async def generate_image(
    prompt: str,
    source_image_gcs_uris: Optional[list[str]] = None,
    aspect_ratio: Literal["16:9", "9:16"] = "16:9",
) -> MediaAsset:
    """Generates an image using Gemini 3.1 Flash Image Preview (aka Nano Banana2).

    Returns a MediaAsset object with the GCS URI of the generated image or an error text.

    Args:
        prompt (str): Image generation prompt (may refer to the source images if provided).
        source_image_gcs_uris (Optional[list[str]], optional): Optional list of GCS URIs of
            reference images to guide generation.
        aspect_ratio (str, optional): Aspect ratio of the generated image.
            Supported values are "16:9" and "9:16". Defaults to "16:9".

    Returns:
        MediaAsset: object with the GCS URI of the generated image or an error text.
    """
    gemini_client = Gemini()
    genai_client = gemini_client.api_client
    content = _build_content(prompt, source_image_gcs_uris or [])

    asset = MediaAsset(uri="")
    for _ in range(0, MAX_RETRIES):
        response = _generate_with_model_fallback(genai_client, content, aspect_ratio)
        if response is None:
            continue

        asset, response_text = await _extract_asset_from_response(response)
        if asset.uri:
            break

        if response_text:
            logging.warning("MODEL RESPONSE (no image yet):\n%s", response_text)

    if not asset.uri:
        asset.error = "No image was generated."
    else:
        logging.info(
            "Image URL: %s",
            asset.uri.replace("gs://", AUTHORIZED_URI),
        )
    print("DEBUG: generate_image returning:", asset)
    return {
        "uri": asset.uri,
        "error": asset.error,
    }


