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

import os
import re

import google.auth
from google.genai import types

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import AgentTool

from subagents import (
    concat_agent,
    script_sequencer_agent,
    script_writer_agent,
    video_agent,
)

from utils.storage_utils import (
    load_character_profile,
    resolve_profile_view_urls,
    upload_data_to_gcs,
)

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)  # type: ignore
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

CHARACTER_PROFILE_PATTERN = re.compile(
    r"^\s*CHARACTER_PROFILE_ID\s*:\s*(?P<profile_id>[A-Za-z0-9._\-]+)\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _extract_character_profile_id(llm_request: LlmRequest) -> str | None:
    """Extract CHARACTER_PROFILE_ID from user text."""
    for content in llm_request.contents:
        for part in content.parts or []:  # type: ignore
            text = getattr(part, "text", None)
            if not text:
                continue
            for line in text.splitlines():
                matched = CHARACTER_PROFILE_PATTERN.match(line)
                if matched:
                    return matched.group("profile_id")
    return None


def _build_profile_guidance(profile) -> str:
    """Builds structured identity guidance for the avatar."""
    locked_traits = "\n - ".join(profile.locked_traits) if profile.locked_traits else ""
    wardrobe = "\n - ".join(profile.wardrobe_constraints) if profile.wardrobe_constraints else ""
    voice_profile = (
        "\n".join(f" - {k}: {v}" for k, v in profile.voice_profile.items())
        if profile.voice_profile
        else ""
    )
    camera_guidance = profile.camera_guidance or ""

    return (
        f"## CHARACTER PROFILE\n"
        f"id: {profile.profile_id}\n"
        f"display_name: {profile.display_name}\n"
        f"description: {profile.description}\n"
        f"\n## LOCKED TRAITS\n{locked_traits}\n"
        f"\n## WARDROBE CONSTRAINTS\n{wardrobe}\n"
        f"\n## VOICE PROFILE\n{voice_profile}\n"
        f"\n## CAMERA GUIDANCE\n{camera_guidance}\n"
        f"\n## POLICY FALLBACK MODE\n{profile.policy_fallback_mode}"
    )


def _hydrate_profile_views(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
):
    """Loads canonical views and identity guidance into callback state."""
    persona_views_urls = callback_context.state.get("persona_views", [])
    profile_id = (
        callback_context.state.get("character_profile_id")
        or _extract_character_profile_id(llm_request)
    )

    if profile_id and not persona_views_urls:
        profile = load_character_profile(profile_id)
        if profile:
            persona_views_urls = resolve_profile_view_urls(profile)
            views_map = {
                str(cv.view_index): cv.uri
                for cv in sorted(profile.canonical_views, key=lambda v: v.view_index)
            }

            callback_context.state["character_profile_id"] = profile.profile_id
            callback_context.state["character_identity_guidance"] = _build_profile_guidance(profile)
            callback_context.state["persona_views"] = persona_views_urls
            callback_context.state["character_views_map"] = views_map

    return profile_id, persona_views_urls


async def _consume_inline_images(
    callback_context: CallbackContext,
    user_content,
    persona_views_urls: list[str],
):
    """Uploads inline user images to GCS and removes them from the prompt."""
    remove_indexes = []
    upload_persona_views = len(persona_views_urls) == 0

    for index, part in enumerate(user_content.parts):  # type: ignore
        inline_data = part.inline_data
        if (
            not inline_data
            or not inline_data.data
            or not inline_data.mime_type
            or not inline_data.mime_type.startswith("image/")
        ):
            continue

        if upload_persona_views:
            image_url = await upload_data_to_gcs(
                callback_context.agent_name,
                inline_data.data,
                inline_data.mime_type,
            )
            persona_views_urls.append(image_url)

        remove_indexes.append(index)

    for index in reversed(remove_indexes):
        user_content.parts.pop(index)  # type: ignore


# ---------------------------------------------------------------------------
# before_model_callback
# ---------------------------------------------------------------------------

async def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Injects identity guidance, canonical views, and persona images."""
    profile_id, persona_views_urls = _hydrate_profile_views(
        callback_context,
        llm_request,
    )

    user_content = llm_request.contents[0]

    # Upload inline images if needed
    await _consume_inline_images(
        callback_context,
        user_content,
        persona_views_urls,
    )

    # Inject CHARACTER_PROFILE_ID
    if profile_id:
        user_content.parts.append(  # type: ignore
            types.Part.from_text(text=f"## CHARACTER PROFILE ID\n{profile_id}")
        )

    # Inject identity guidance
    identity_guidance = callback_context.state.get("character_identity_guidance")
    if identity_guidance:
        user_content.parts.append(  # type: ignore
            types.Part.from_text(text=identity_guidance)
        )

    # Inject canonical view map
    views_map = callback_context.state.get("character_views_map", {})
    if views_map:
        map_lines = "\n".join(
            f" - view {k}: {v}" for k, v in sorted(views_map.items())
        )
        user_content.parts.append(  # type: ignore
            types.Part.from_text(text=f"## CANONICAL VIEW MAP\n{map_lines}")
        )

    # Inject persona view URLs
    user_content.parts.append(  # type: ignore
        types.Part.from_text(
            text="## VIEW IMAGE URLS\n" + "\n - ".join(persona_views_urls)
        )
    )

    callback_context.state["persona_views"] = persona_views_urls


# ---------------------------------------------------------------------------
# Root Agent
# ---------------------------------------------------------------------------

root_agent = LlmAgent(
    name="root_agent",
    model="gemini-2.5-pro",
    instruction="""
    You are a video generation orchestrator for avatar-based training videos.

    **Workflow:**

    1. **Script Creation**  
       - If the user provides a full script, use it.  
       - Otherwise call `script_writer_agent` to generate one.

    2. **Script Sequencing**  
       - Call `script_sequencer_agent` to break the script into chunks and assign view indices.

    3. **Reference Images**  
       - If no canonical views exist, call `video_agent` with the `generate_image` tool  
         to create 4 canonical views:  
         1 = front, 2 = left profile, 3 = right profile, 4 = wide shot.  
       - Store these URLs for identity consistency.

    4. **Video Generation**  
       - For each chunk, call `video_agent` to generate a video segment.  
       - Immediately present each segment to the user with:  
         - the video URL  
         - the chunk number  
         - the script text

    5. **Concatenation**  
       - After all chunks are generated, call `concat_agent` to merge them.  
       - Present the final merged video URL.

    **Rules:**

    - Always pass the full character description and shot instructions to `video_agent`.
    - Resolve starting frame using this priority:  
      1. Canonical view for the chunk’s view_index  
      2. Any persona view  
      3. Generate a new starter image
    - Replace "gs://" with "https://storage.mtls.cloud.google.com/" when showing URLs to the user.
    - Keep "gs://" when calling tools.
    """.strip(),
    tools=[
        AgentTool(script_writer_agent),
        AgentTool(script_sequencer_agent),
        AgentTool(video_agent),
        AgentTool(concat_agent),
    ],
    before_model_callback=before_model_callback,
)
