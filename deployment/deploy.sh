#!/bin/bash
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "${SCRIPT_DIR}/.."

source ".env"

if [[ "${AGENT_ENGINE_ID}" == "" ]]; then
    AGENT_ENGINE_ID=$(python3 "${SCRIPT_DIR}/get_agent_engine.py" --agent-name "${AGENT_ENGINE_NAME}" --project-id "${GOOGLE_CLOUD_PROJECT}" --location "${GOOGLE_CLOUD_REGION}" | tail -1)
    echo "AGENT_ENGINE_ID=\"${AGENT_ENGINE_ID}\"" >> ".env"
fi

echo "Deploying MCP Server to Cloud Run..."
MCP_SERVICE_NAME="media-mcp"

gcloud run deploy "${MCP_SERVICE_NAME}" \
  --source mcp \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_REGION}" \
  --no-allow-unauthenticated \
  --clear-base-image \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI}" \
  --set-env-vars GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT}" \
  --set-env-vars GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION}" \
  --set-env-vars AI_ASSETS_BUCKET="${AI_ASSETS_BUCKET}"

MEDIA_MCP_SERVER_URL=$(gcloud run services describe "${MCP_SERVICE_NAME}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_REGION}" \
  --format="value(status.url)")
echo "MCP Server deployed at ${MEDIA_MCP_SERVER_URL}"

SERVICE_NAME="${AGENT_ENGINE_NAME//_/-}"
echo "Deploying the agent to Cloud Run..."
adk deploy cloud_run \
    --project="${GOOGLE_CLOUD_PROJECT}" \
    --region="${GOOGLE_CLOUD_REGION}" \
    --service_name="${SERVICE_NAME}" \
    --app_name="video_avatar_agent" \
    --artifact_service_uri="gs://${AI_ASSETS_BUCKET}" \
    --session_service_uri="agentengine://${AGENT_ENGINE_ID}" \
    --memory_service_uri="agentengine://${AGENT_ENGINE_ID}" \
    --trace_to_cloud \
    --with_ui \
    ./agents/video_avatar_agent \
    -- --allow-unauthenticated \
        --set-env-vars GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI}" \
        --set-env-vars GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT}" \
        --set-env-vars GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION}" \
        --set-env-vars AI_ASSETS_BUCKET="${AI_ASSETS_BUCKET}" \
        --set-env-vars AGENT_ENGINE_ID="${AGENT_ENGINE_ID}" \
        --set-env-vars MEDIA_MCP_SERVER_URL="${MEDIA_MCP_SERVER_URL}"
