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
import os
import tempfile
from typing import List

from google.cloud.storage import Blob

from media_models import MediaAsset
from storage_utils import ai_bucket_name, ai_bucket, storage_client


def _upload_merged_video(data: bytes) -> str:
    file_hash = hashlib.sha256(data).hexdigest()
    blob_name = f"assets/merged/{file_hash}.mp4"
    upload_blob = Blob(bucket=ai_bucket, name=blob_name)
    upload_blob.upload_from_string(data, content_type="video/mp4", client=storage_client)
    return f"gs://{ai_bucket_name}/{blob_name}"


def _load_clips(tmpdir: str, uris: List[str]) -> list:
    from moviepy import VideoFileClip  # type: ignore[import]
    clips = []
    for i, uri in enumerate(uris):
        blob = Blob.from_string(uri, client=storage_client)
        ext = os.path.splitext(uri.split("?")[0])[-1] or ".mp4"
        local_path = os.path.join(tmpdir, f"chunk_{i:03d}{ext}")
        blob.download_to_filename(local_path, client=storage_client)
        clips.append(VideoFileClip(local_path))
    return clips


async def concatenate_videos(
    video_gcs_uris: List[str],
) -> MediaAsset:
    """Joins ordered video chunks from GCS into a single merged video file.

    Args:
        video_gcs_uris: Ordered list of GCS URIs (gs://...) of video chunks.

    Returns:
        MediaAsset with the GCS URI of the merged video, or an error message.
    """
    from moviepy import concatenate_videoclips  # type: ignore[import]

    result = MediaAsset(uri="")
    if not video_gcs_uris:
        result.error = "No video URIs provided."
        return result

    clips: list = []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            clips = _load_clips(tmpdir, video_gcs_uris)
            final = concatenate_videoclips(clips)
            out_path = os.path.join(tmpdir, "merged.mp4")
            final.write_videofile(out_path, codec="libx264", audio_codec="aac", logger=None)
            final.close()
            with open(out_path, "rb") as f:
                data = f.read()
            result.uri = _upload_merged_video(data)
    except Exception as e:
        result.error = f"Concatenation failed: {e}"
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
    return result
