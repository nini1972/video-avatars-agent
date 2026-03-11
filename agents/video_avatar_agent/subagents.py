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
    StreamableHTTPConnectionParams
)
from google.genai import types

from utils.auth_provider import IdentityTokenHeaderProvider
from utils.utils import load_prompt_from_file
from utils.storage_utils import download_data_from_gcs

mcp_server_url = os.environ.get(
    "MEDIA_MCP_SERVER_URL",
    "http://localhost:8080"
).strip("/")
if not mcp_server_url.endswith("/mcp"):
    mcp_server_url += "/mcp"

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


def before_tool_callback(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict]:
    print(f"======== Calling a tool: {tool.name}. Arguments: {args}")


async def extract_media_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict
) -> Optional[Dict]:
    """The callback that ensures uploading all media assets to the Artifact Store"""
    if not tool_response:
        return
    if not isinstance(tool_response, BaseModel):
        if isinstance(tool_response, dict) and len(tool_response) == "1" and "result" in tool_response:
            response = tool_response["result"]
        else:
            response = tool_response
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError:
                return
    elif isinstance(tool_response, BaseModel):
        response = tool_response.model_dump(exclude_none=False)
    if isinstance(response, dict):
        uri = response.get("uri", "")
        if uri and uri.startswith("gs://"):
            await tool_context.save_artifact(
                filename=uuid.uuid4().hex,
                artifact=types.Part(inline_data=download_data_from_gcs(uri))
            )

async def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest
) -> LlmResponse | None:
    """The callback that ensures uploading user's images to GCS."""
    persona_views = callback_context.state.get("persona_views", None)
    if not persona_views:
        return
    user_content = llm_request.contents[-1]
    for url in persona_views:
        print(f"#### Added image: {url}")
        user_content.parts.append( # type: ignore
            types.Part.from_uri(
                file_uri=url,
                mime_type=mimetypes.guess_type(url)[0],
            )
        )

script_sequencer_agent = Agent(
    model="gemini-2.5-pro",
    name="script_sequencer_agent",
    description="""Script Sequencer Agent.
    Input:
    1. Training script.
    """,
    instruction=load_prompt_from_file("script_sequencer_agent.md"),
)

video_agent = Agent(
    model="gemini-2.5-pro",
    name="video_agent",
    description="""Video Agent.

    Input:
    -   The character description.
    -   The script chunk (as ## SCRIPT section).
    -   The starting frame image (one of the character views).
    """,
    instruction=load_prompt_from_file("video_agent.md"),
    tools=[mcp_toolset_generate_video],
    after_tool_callback=extract_media_callback,
    before_tool_callback=before_tool_callback,
)

