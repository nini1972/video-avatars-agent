#!/usr/bin/env python
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

"""Bootstrap a character profile by generating canonical view images and saving to GCS.

Usage:
  python deployment/bootstrap_character_views.py
  python deployment/bootstrap_character_views.py --profile-file assets/characters/example_profile.json
  python deployment/bootstrap_character_views.py --dry-run
"""

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import sys
from pathlib import Path

import google.auth
from dotenv import load_dotenv
from google.adk.models.google_llm import Gemini
from google.cloud.storage import Blob
from google.genai import types

_REPO_ROOT = Path(__file__).parent.parent
# Add the utils dir directly so we bypass video_avatar_agent/__init__.py
# (which eagerly imports root_agent and all ADK dependencies).
sys.path.insert(0, str(_REPO_ROOT / "agents" / "video_avatar_agent"))

load_dotenv(_REPO_ROOT / ".env")

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id)  # type: ignore
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

from utils.character_profiles import (  # type: ignore[import]  # noqa: E402
    CharacterProfile,
    CharacterView,
)

IMAGE_MODELS = [
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
    "gemini-2.0-flash-preview-image-generation",
]

_PLACEHOLDER_PREFIX = "gs://REPLACE_WITH_BUCKET"

_VIEW_ANGLE_DESCRIPTIONS = {
    1: "front-facing medium close-up portrait, looking directly at camera",
    2: "left profile view, side portrait, facing left",
    3: "right profile view, side portrait, facing right",
    4: "wide medium shot, upper body, slightly angled toward camera",
}


def _build_view_prompt(
    profile: CharacterProfile, view_index: int, shot_hint: str = ""
) -> str:
    traits = ", ".join(profile.locked_traits + profile.wardrobe_constraints)
    angle = _VIEW_ANGLE_DESCRIPTIONS.get(view_index, "front-facing portrait")
    hint = f", {shot_hint}" if shot_hint else ""
    return (
        f"{profile.display_name}, {profile.description}, {traits}, "
        f"{angle}{hint}, professional studio background, "
        f"cinematic lighting, photorealistic, high quality"
    )


def _extract_image_from_chunks(chunks) -> tuple[bytes, str] | None:
    """Returns (data, mime_type) from the first inline image found in stream chunks."""
    for chunk in chunks:
        try:
            for part in chunk.candidates[0].content.parts:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    return inline.data, inline.mime_type or "image/png"
        except (AttributeError, IndexError):
            pass
    return None


def _generate_image(
    genai_client, prompt: str, aspect_ratio: str = "9:16"
) -> tuple[bytes, str]:
    """Call Gemini image generation with model fallback. Returns (data, mime_type)."""
    content = types.Content(
        parts=[types.Part.from_text(text=prompt)],
        role="user",
    )
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
            image_size="1K",
            output_mime_type="image/png",
        ),
        thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
    )
    for model_name in IMAGE_MODELS:
        try:
            chunks = list(genai_client.models.generate_content_stream(
                model=model_name,
                contents=[content],
                config=generate_content_config,
            ))
            result = _extract_image_from_chunks(chunks)
            if result:
                return result
        except Exception as err:  # pylint: disable=broad-except
            logging.warning("Image model %s failed: %s", model_name, err)
    raise RuntimeError("All image models failed to generate an image.")


def _upload_image(image_data: bytes, mime_type: str, profile_id: str) -> str:
    from utils.storage_utils import (  # type: ignore[import]  # pylint: disable=import-outside-toplevel
        ai_bucket,
        ai_bucket_name,
        storage_client,
    )
    ext = mimetypes.guess_extension(mime_type) or ".jpg"
    blob_name = f"assets/{profile_id}/{hashlib.sha256(image_data).hexdigest()}{ext}"
    blob = Blob(bucket=ai_bucket, name=blob_name)
    blob.upload_from_string(image_data, content_type=mime_type, client=storage_client)
    return f"gs://{ai_bucket_name}/{blob_name}"


def _process_views(
    profile: CharacterProfile,
    genai_client,
    dry_run: bool,
) -> list[CharacterView]:
    updated: list[CharacterView] = []
    for view in sorted(profile.canonical_views, key=lambda v: v.view_index):
        prompt = _build_view_prompt(profile, view.view_index, view.shot_hint or "")
        print(f"\n[view {view.view_index}] {view.label or ''}")
        print(f"  Prompt: {prompt[:140]}...")

        if dry_run or not view.uri.startswith(_PLACEHOLDER_PREFIX):
            if not dry_run:
                print(f"  Already has URI — skipping: {view.uri}")
            updated.append(view)
            continue

        print("  Generating image ...")
        image_data, mime_type = _generate_image(genai_client, prompt)
        uri = _upload_image(image_data, mime_type, profile.profile_id)
        print(f"  Uploaded: {uri}")
        updated.append(
            CharacterView(
                view_index=view.view_index,
                uri=uri,
                label=view.label,
                shot_hint=view.shot_hint,
            )
        )
    return updated


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description=(
            "Generate canonical view images for a character profile and save it to GCS."
        ),
    )
    parser.add_argument(
        "--profile-file",
        default="assets/characters/example_profile.json",
        help="Path to CharacterProfile JSON (default: assets/characters/example_profile.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts without calling the image API or uploading anything.",
    )
    args = parser.parse_args()

    profile_path = Path(args.profile_file)
    if not profile_path.exists():
        logging.error("Profile file not found: %s", profile_path)
        return 1

    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = CharacterProfile.model_validate(raw)

    print(f"Profile  : {profile.profile_id}")
    print(f"Character: {profile.display_name}")

    genai_client = None if args.dry_run else Gemini().api_client

    updated_views = _process_views(profile, genai_client, args.dry_run)

    if args.dry_run:
        print("\n[dry-run] No images generated. Re-run without --dry-run to create them.")
        return 0

    updated_profile = CharacterProfile.model_validate(
        {**profile.model_dump(), "canonical_views": [v.model_dump() for v in updated_views]}
    )
    from utils.storage_utils import save_character_profile  # type: ignore[import]  # pylint: disable=import-outside-toplevel
    profile_uri = save_character_profile(updated_profile)

    print("\nProfile saved to GCS:")
    print(f"  PROFILE_ID={updated_profile.profile_id}")
    print(f"  PROFILE_URI={profile_uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
