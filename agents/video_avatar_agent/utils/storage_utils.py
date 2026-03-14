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

import hashlib
import mimetypes
import os
from typing import List, Optional

from google.api_core import exceptions
import google.auth
from google.genai import types
from google.cloud.storage import Bucket, Client, Blob
from pydantic import ValidationError

from dotenv import load_dotenv
from utils.character_profiles import CharacterProfile

load_dotenv()

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id) # type: ignore
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
storage_client = Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
ai_bucket_name = os.environ.get(
    "AI_ASSETS_BUCKET",
    "image_ai_storage"
)
ai_bucket = storage_client.get_bucket(ai_bucket_name)
CHARACTER_PROFILE_PREFIX = "character-profiles"


async def upload_data_to_gcs(agent_id: str, data: bytes, mime_type: str) -> str:
    file_name = hashlib.sha256(data).hexdigest()
    ext = mimetypes.guess_extension(mime_type) or ""
    file_name = f"{file_name}{ext}"
    blob_name = f"assets/{agent_id}/{file_name}"
    blob = Blob(bucket=ai_bucket, name=blob_name)
    blob.upload_from_string(data, content_type=mime_type, client=storage_client)
    gcs_url = f"gs://{ai_bucket_name}/{blob_name}"
    return gcs_url

def download_data_from_gcs(url: str) -> types.Blob:
    blob = Blob.from_string(url, client=storage_client)
    blob_data = blob.download_as_bytes(client=storage_client)
    file_name = url.split("/")[-1]
    mime_type = (
        mimetypes.guess_type(file_name)[0]
        or blob.content_type
        or "application/octet-stream"
    )
    if ";" in mime_type:
        mime_type = mime_type.split(";")[0]
    return types.Blob(
        display_name=file_name,
        data=blob_data,
        mime_type=mime_type.strip()
    )


def _profile_blob_name(profile_id: str) -> str:
    return f"{CHARACTER_PROFILE_PREFIX}/{profile_id}/profile.json"


def save_character_profile(profile: CharacterProfile) -> str:
    blob_name = _profile_blob_name(profile.profile_id)
    blob = Blob(bucket=ai_bucket, name=blob_name)
    blob.upload_from_string(
        profile.model_dump_json(indent=2),
        content_type="application/json",
        client=storage_client,
    )
    return f"gs://{ai_bucket_name}/{blob_name}"


def load_character_profile(profile_id: str) -> Optional[CharacterProfile]:
    blob_name = _profile_blob_name(profile_id)
    blob = Blob(bucket=ai_bucket, name=blob_name)
    try:
        data = blob.download_as_text(client=storage_client)
    except exceptions.NotFound:
        return None
    try:
        return CharacterProfile.model_validate_json(data)
    except ValidationError as err:
        raise ValueError(f"Invalid character profile JSON for '{profile_id}': {err}")


def list_character_profiles() -> List[str]:
    prefix = f"{CHARACTER_PROFILE_PREFIX}/"
    profile_ids = set()
    for blob in storage_client.list_blobs(ai_bucket_name, prefix=prefix):
        parts = blob.name.split("/")
        if len(parts) >= 3 and parts[0] == CHARACTER_PROFILE_PREFIX:
            profile_ids.add(parts[1])
    return sorted(profile_ids)


def resolve_profile_view_urls(profile: CharacterProfile) -> List[str]:
    return [
        view.uri
        for view in sorted(profile.canonical_views, key=lambda item: item.view_index)
    ]