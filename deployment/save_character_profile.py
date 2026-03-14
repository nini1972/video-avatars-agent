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

import argparse
import json
from pathlib import Path

from agents.video_avatar_agent.utils.character_profiles import CharacterProfile
from agents.video_avatar_agent.utils.storage_utils import save_character_profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save a character profile JSON into the GCS character library."
    )
    parser.add_argument(
        "--profile-file",
        required=True,
        help="Path to a CharacterProfile JSON file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile_path = Path(args.profile_file)
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile file not found: {profile_path}")

    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = CharacterProfile.model_validate(profile_data)
    profile_uri = save_character_profile(profile)

    print(f"PROFILE_ID={profile.profile_id}")
    print(f"PROFILE_URI={profile_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
