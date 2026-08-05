from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

TARGET_NARRATION_WPM = 150
MIN_NARRATION_WPM = 145
MAX_NARRATION_WPM = 160


def estimated_narration_seconds(text: str) -> float:
    return len(text.split()) / TARGET_NARRATION_WPM * 60


class PeriodType(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class Visual(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(
        pattern=(
            r"^(gradient|chart|icon_grid|timeline|photo_placeholder|hook|metric_grid|"
            r"progress_ring|sparkline|seven_day|comparison|recommendation|closing|data_quality)$"
        )
    )
    data_reference: str = Field(max_length=80)
    payload: dict[str, object] = Field(default_factory=dict)


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_seconds: float = Field(ge=0, le=65)
    duration_seconds: float = Field(gt=0, le=65)
    scene_type: str = Field(pattern=r"^(title|metric|timeline|reflection|recommendation|closing)$")
    heading: str = Field(min_length=1, max_length=80)
    body: str = Field(max_length=240)
    visual: Visual
    scene_id: str = Field(default="scene", pattern=r"^[a-z0-9-]{2,50}$")
    layout: str = Field(default="focus", max_length=40)
    accent: str = Field(default="indigo", max_length=24)
    transition_in: str = Field(default="fade-slide", max_length=32)
    transition_out: str = Field(default="crossfade", max_length=32)
    caption: str = Field(default="", max_length=120)
    caption_behavior: str = Field(default="phrase", max_length=32)
    audio_cue: str = Field(default="none", max_length=24)
    accessibility_description: str = Field(default="", max_length=240)


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    period_type: PeriodType
    summary: str = Field(max_length=400)
    narration: str = Field(min_length=1, max_length=1800)
    scenes: list[Scene] = Field(min_length=3, max_length=10)
    safety_notes: list[str] = Field(default_factory=list, max_length=10)
    data_categories_used: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_timeline(self) -> Storyboard:
        previous_end = 0.0
        for scene in self.scenes:
            if scene.start_seconds < previous_end - 0.05:
                raise ValueError("scenes must be ordered and non-overlapping")
            previous_end = scene.start_seconds + scene.duration_seconds
        if not 55 <= previous_end <= 65:
            raise ValueError("storyboard duration must be between 55 and 65 seconds")
        words = len(self.narration.split())
        if not MIN_NARRATION_WPM <= words <= MAX_NARRATION_WPM:
            raise ValueError(
                "narration must contain 145-160 words for natural one-minute speech at 150 WPM"
            )
        estimated_seconds = estimated_narration_seconds(self.narration)
        if not 58 <= estimated_seconds <= 64:
            raise ValueError("narration timing estimate is outside the one-minute target")
        return self


class BrowserVideo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9-]{5,80}$")
    type: PeriodType
    title: str = Field(max_length=80)
    description: str = Field(max_length=240)
    created_at: datetime
    period_start: datetime
    period_end: datetime
    duration_seconds: float = Field(ge=55, le=65)
    video_filename: str = Field(pattern=r"^[a-zA-Z0-9_.-]+\.mp4$")
    thumbnail_filename: str = Field(pattern=r"^[a-zA-Z0-9_.-]+\.(webp|png|jpg)$")
    captions_filename: str = Field(pattern=r"^[a-zA-Z0-9_.-]+\.vtt$")
    generation_status: str = Field(pattern="^complete$")
    schema_version: int = 1


class PrivateAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    fallback_reason: str = ""
    data_categories: list[str] = Field(default_factory=list)
