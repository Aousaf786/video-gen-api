from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, HttpUrl

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

class Track(BaseModel):
    type: str  # "video" | "image" | "overlay" | "subtitle" | "audio"
    clips: Optional[List[Clip]] = None
    src: Optional[str] = None  # for audio track shortcut

class AudioSpec(BaseModel):
    voiceover: Optional[str] = None
    music: Optional[str] = None
    ducking_db: Optional[float] = None

class RenderPayload(BaseModel):
    output: OutputSpec = Field(default_factory=OutputSpec)
    tracks: List[Track] = Field(default_factory=list)
    audio: Optional[AudioSpec] = None

class RenderRequest(BaseModel):
    payload: Optional[RenderPayload] = None
    payload_url: Optional[HttpUrl] = None
    output_filename: Optional[str] = "output.mp4"

class JobStatus(BaseModel):
    id: str
    status: str  # queued|running|success|failed
    message: Optional[str] = None
    output_url: Optional[str] = None
    logs: Optional[str] = None
