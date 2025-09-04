from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, HttpUrl, ConfigDict

# =========================
# Internal (legacy) schema
# =========================
class OutputSpec(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    codec: str = "h264_nvenc"
    bitrate: Optional[str] = None  # e.g., "6M"

class Clip(BaseModel):
    src: str
    start: float = 0
    duration: float
    effects: Optional[List[Dict[str, Any]]] = None
    transition_in: Optional[Dict[str, Any]] = None
    transition_out: Optional[Dict[str, Any]] = None
    position: Optional[str] = None
    opacity: Optional[float] = None
    fit: Optional[str] = None
    position: str

class Track(BaseModel):
    # "video" | "image" | "overlay" | "subtitle" | "audio"
    type: str
    clips: Optional[List[Clip]] = None
    # Optional shortcut for a single-source audio track
    src: Optional[str] = None

class AudioSpec(BaseModel):
    voiceover: Optional[str] = None
    music: Optional[str] = None
    ducking_db: Optional[float] = None

class RenderPayload(BaseModel):
    """
    Our internal payload format (kept for backward compatibility).
    """
    output: OutputSpec = Field(default_factory=OutputSpec)
    tracks: List[Track] = Field(default_factory=list)
    audio: Optional[AudioSpec] = None

# ===================================
# Timeline (Shotstack-like) schema
# ===================================
# These classes mirror your payload fields and allow unknown keys.
# Keys seen in your JSON: asset.{type,src,volume}, clip.{start,length,fit,opacity}
class TLAsset(BaseModel):
    model_config = ConfigDict(extra="allow")  # accept extra keys
    type: Optional[str] = None               # "video" | "image" | "audio" | ...
    src: Optional[str] = None                # URL or local path
    volume: Optional[float] = None           # 0..1 if present

class TLClip(BaseModel):
    model_config = ConfigDict(extra="allow")
    asset: TLAsset
    start: float = 0.0                       # timeline start (seconds)
    length: float = 0.0                      # duration (seconds)
    fit: Optional[str] = None                # "cover" | "contain" | ...
    opacity: Optional[float] = None          # 0..1
    position: Optional[str] = None           # e.g. "top_right"
    # Optional / permissive:
    effects: Optional[List[Dict[str, Any]]] = None
    transition: Optional[Dict[str, Any]] = None

class TLTrack(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Optional[str] = None               # some editors include a track type
    clips: List[TLClip] = Field(default_factory=list)

class Timeline(BaseModel):
    model_config = ConfigDict(extra="allow")
    tracks: List[TLTrack] = Field(default_factory=list)

class CanvasSpec(BaseModel):
    """
    Optional block for canvas/output (supported if you add later).
    """
    model_config = ConfigDict(extra="allow")
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    format: Optional[str] = None
    codec: Optional[str] = None

class TimelineRoot(BaseModel):
    """
    Top-level structure for Shotstack-like payloads.
    Your file uses {"timeline": {"tracks": [...]}}.
    """
    model_config = ConfigDict(extra="allow")
    timeline: Timeline
    output: Optional[Dict[str, Any]] = None
    canvas: Optional[CanvasSpec] = None

# =========================
# Request / Response
# =========================
class RenderRequest(BaseModel):
    """
    Accept:
      - Internal RenderPayload (our original schema), OR
      - TimelineRoot (Shotstack-like), OR
      - a raw dict (for maximum compatibility)
    """
    payload: Optional[Union[RenderPayload, TimelineRoot, Dict[str, Any]]] = None
    payload_url: Optional[HttpUrl] = None
    output_filename: Optional[str] = "output.mp4"

class JobStatus(BaseModel):
    id: str
    status: str  # queued | running | success | failed
    message: Optional[str] = None
    output_url: Optional[str] = None
    logs: Optional[str] = None
