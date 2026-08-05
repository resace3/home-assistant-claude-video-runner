from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_discover_sensors: bool = True
    include_binary_sensors: bool = True
    entity_allowlist: list[str] = Field(default_factory=list, max_length=2_000)
    max_discovered_entities: int = Field(default=2_000, ge=1, le=5_000)
    history_hours_daily: int = Field(default=24, ge=1, le=168)
    history_days_weekly: int = Field(default=7, ge=1, le=31)
    history_batch_size: int = Field(default=20, ge=1, le=100)
    max_observations_per_entity: int = Field(default=512, ge=8, le=2048)
    max_response_bytes: int = Field(default=10_000_000, ge=64_000, le=20_000_000)
    max_highlights: int = Field(default=5, ge=3, le=5)
    include_raw_values_in_external_requests: bool = False
    anonymize_entity_names: bool = True


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="claude", pattern=r"^(claude|offline)$")
    claude_binary: str = "claude"
    # Shared mount so the SSH add-on and this add-on read and refresh one credential store.
    claude_config_dir: Path = Path("/homeassistant/.claude-video-runner")
    # Container-owned writable HOME; the CLI writes state outside CLAUDE_CONFIG_DIR too.
    claude_home_dir: Path = Path("/data/personal_video_studio/claude-home")
    model: str = "claude-opus-5"
    timeout_seconds: int = Field(default=300, ge=60, le=900)
    story_detail: str = Field(default="personal", pattern=r"^(personal|aggregate)$")
    offline_fallback: bool = True
    # Namespaces the catalog id so this add-on can publish into the same share as
    # another one. Empty restores the un-namespaced ids. Constrained here rather than
    # at render time so a bad value fails at config load, not mid-render.
    video_id_namespace: str = Field(
        default="claude", max_length=32, pattern=r"^$|^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
    )


class TTSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "edge"
    requested_voice_name: str = "Libby, British Warm"
    requested_voice_id: str = "en-GB-LibbyNeural"
    fallback_voice_name: str = ""
    allow_fallback: bool = False
    allow_external_egress: bool = False
    # Narration may be slowed deliberately, but is never sped up to force-fit a scene.
    speaking_rate: float = Field(default=1.0, ge=0.9, le=1.0)
    output_format: str = "mp3"


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(default=720, ge=240, le=1080)
    height: int = Field(default=1280, ge=426, le=1920)
    fps: int = Field(default=24, ge=12, le=30)
    preset: str = "medium"
    style: str = Field(default="engaging", pattern=r"^(engaging|calm|minimal)$")
    energy: str = Field(default="balanced", pattern=r"^(calm|balanced|bright)$")
    motion_level: str = Field(default="medium", pattern=r"^(low|medium|high)$")
    music_enabled: bool = True
    sound_effects_enabled: bool = True
    captions_style: str = Field(default="animated", pattern=r"^(animated|standard|off)$")
    theme: str = Field(default="adaptive", pattern=r"^(adaptive|indigo|teal|slate)$")
    motion_sample_fps: int = Field(default=8, ge=4, le=12)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_directory: Path = Path("/share/personal_video_studio")
    private_data_directory: Path = Path("/data/personal_video_studio")
    data: DataConfig = DataConfig()
    generation: GenerationConfig = GenerationConfig()
    tts: TTSConfig = TTSConfig()
    render: RenderConfig = RenderConfig()


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(payload)
