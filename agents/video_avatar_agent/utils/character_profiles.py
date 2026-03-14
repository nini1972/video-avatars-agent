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

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CharacterView(BaseModel):
    view_index: int = Field(ge=1, le=8)
    uri: str
    label: Optional[str] = None
    shot_hint: Optional[str] = None


class CharacterProfile(BaseModel):
    profile_id: str
    display_name: str
    description: str
    voice_profile: Dict[str, str] = Field(default_factory=dict)
    locked_traits: List[str] = Field(default_factory=list)
    wardrobe_constraints: List[str] = Field(default_factory=list)
    camera_guidance: Optional[str] = None
    canonical_views: List[CharacterView] = Field(default_factory=list)
    policy_fallback_mode: Literal[
        "auto-fictionalize",
        "ask-user",
        "hard-fail",
    ] = "auto-fictionalize"
    version: int = 1

    @model_validator(mode="after")
    def validate_canonical_views(self):
        if not self.canonical_views:
            raise ValueError("canonical_views must contain at least one view")
        view_indexes = [view.view_index for view in self.canonical_views]
        if len(view_indexes) != len(set(view_indexes)):
            raise ValueError("canonical_views must have unique view_index values")
        return self
