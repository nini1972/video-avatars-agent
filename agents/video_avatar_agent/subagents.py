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

import json
import mimetypes
import os
import re
from typing import Any, Dict, Optional
import uuid

from pydantic import BaseModel

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import BaseTool, ToolContext
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.genai import types

from utils.auth_provider import IdentityTokenHeaderProvider
from utils.utils import load_prompt_from_file
from utils.storage_utils import download_data_from_gcs

mcp_server_url = os.environ.get(
    "MEDIA_MCP_SERVER_URL",
    "http://localhost:8080",
).strip("/")
if not mcp_server_url.endswith("/mcp"):
    mcp_server_url += "/mcp"

_VIEW_INDEX_RE = re.compile(
    r"view[_ #-]*(?:index[:\s]+)?(\d+)",
    re.IGNORECASE,
)

mcp_toolset_generate_image = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=mcp_server_url,
    ),
    tool_filter=["generate_image"],
    header_provider=IdentityTokenHeaderProvider(mcp_server_url),
)
mcp_toolset_generate_video = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=mcp_server_url,
    ),
    tool_filter=["generate_video"],
    header_provider=IdentityTokenHeaderProvider(mcp_server_url),
)

mcp_toolset_concatenate_videos = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=mcp_server_url,
    ),
    tool_filter=["concatenate_videos"],
    header_provider=IdentityTokenHeaderProvider(mcp_server_url),
)


def before_tool_callback(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict]:
    print(f"======== Calling a tool: {tool.name}. Arguments: {args}")


def _parse_tool_response(tool_response) -> Optional[Dict]:
    """Normalise a tool response to a plain dict, or return None if unusable."""
    if not tool_response:
        return None

    if isinstance(tool_response, BaseModel):
        return tool_response.model_dump(exclude_none=False)

    if not isinstance(tool_response, dict):
        return None

    # Common MCP patterns: {"result": {...}}, {"data": {...}}, or direct dict
    if len(tool_response) == 1:
        raw = tool_response.get("result") or tool_response.get("data") or next(
            iter(tool_response.values())
        )
    else:
        raw = tool_response

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None

    return raw if isinstance(raw, dict) else None


async def extract_media_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict,
) -> Optional[Dict]:
    """Callback that uploads all media assets to the Artifact Store."""
    response = _parse_tool_response(tool_response)
    if response is None:
        return

    uri = response.get("uri", "")
    if uri and uri.startswith("gs://"):
        # Store the media as an artifact for downstream tools/agents.
        await tool_context.save_artifact(
            filename=uuid.uuid4().hex,
            artifact=types.Part(inline_data=download_data_from_gcs(uri)),
        )


def _extract_view_index_from_request(llm_request: LlmRequest) -> Optional[int]:
    """Return the first view index found in any text part of the request."""
    for content in llm_request.contents:
        for part in content.parts or []:  # type: ignore
            text = getattr(part, "text", None)
            if not text:
                continue
            match = _VIEW_INDEX_RE.search(text)
            if match:
                return int(match.group(1))
    return None


async def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Callback that injects character identity and canonical views into the request."""
    persona_views = callback_context.state.get("persona_views", None)
    character_profile_id = callback_context.state.get("character_profile_id", None)
    character_identity_guidance = callback_context.state.get(
        "character_identity_guidance",
        None,
    )

    user_content = llm_request.contents[-1]

    if character_profile_id:
        user_content.parts.append(  # type: ignore
            types.Part.from_text(
                text=f"## CHARACTER PROFILE ID\n{character_profile_id}"
            )
        )

    if character_identity_guidance:
        # Identity guidance: keep character appearance, voice, and style consistent.
        user_content.parts.append(  # type: ignore
            types.Part.from_text(text=character_identity_guidance)
        )

    if not persona_views:
        return

    views_map = callback_context.state.get("character_views_map", {})
    view_index = _extract_view_index_from_request(llm_request)
    canonical_url = views_map.get(str(view_index)) if view_index and views_map else None

    if canonical_url:
        print(f"#### Adding canonical view {view_index}: {canonical_url}")
        user_content.parts.append(  # type: ignore
            types.Part.from_text(
                text=f"## STARTING FRAME\nview {view_index}: {canonical_url}"
            )
        )
        mime_type = mimetypes.guess_type(canonical_url)[0] or "image/png"
        user_content.parts.append(  # type: ignore
            types.Part.from_uri(
                file_uri=canonical_url,
                mime_type=mime_type,
            )
        )
    else:
        for url in persona_views:
            print(f"#### Added image: {url}")
            mime_type = mimetypes.guess_type(url)[0] or "image/png"
            user_content.parts.append(  # type: ignore
                types.Part.from_uri(
                    file_uri=url,
                    mime_type=mime_type,
                )
            )


script_writer_agent = Agent(
    model="gemini-2.5-pro",
    name="script_writer_agent",
    description="""Script Writer Agent.
    Writes a 60-90 second training video script from a topic and character description.
    Input:
    1. Topic: the subject to cover.
    2. Character description: who is presenting.
    Output: plain spoken-word script text ready for a text-to-speech pipeline.
    """,
    instruction=load_prompt_from_file("script_writer_agent.md"),
)

script_sequencer_agent = Agent(
    model="gemini-2.5-pro",
    name="script_sequencer_agent",
    description="""Script Sequencer Agent.
    Input:
    1. Training script.
    Output:
    - Chunked script segments with view indices and shot guidance.
    """,
    instruction=load_prompt_from_file("script_sequencer_agent.md"),
)

video_agent = Agent(
    model="gemini-2.5-flash",
    name="video_agent",
    description="""Video Agent.

    Input:
    - The character description.
    - The script chunk (as ## SCRIPT section).
    - The starting frame image (one of the character views).
    - The view index for this chunk.
    """,
    instruction=load_prompt_from_file("video_agent.md"),
    tools=[mcp_toolset_generate_image, mcp_toolset_generate_video],
    after_tool_callback=extract_media_callback,
    before_tool_callback=before_tool_callback,
    before_model_callback=before_model_callback,
)

concat_agent = Agent(
    model="gemini-2.5-flash",
    name="concat_agent",
    description="""Video Concatenation Agent.
    Merges an ordered list of GCS video chunk URIs into a single final video.
    Input: list of gs:// URIs in chunk order.
    Output: GCS URI of the merged video.
    """,
    instruction="""You are a video concatenation assistant.
    When given a list of GCS video URIs, call the `concatenate_videos` tool
    passing all URIs in the exact order provided.
    Return the URI of the merged video exactly as returned by the tool.
    If the tool returns an error, report it verbatim.""".strip(),
    tools=[mcp_toolset_concatenate_videos],
    after_tool_callback=extract_media_callback,
    before_tool_callback=before_tool_callback,
)
