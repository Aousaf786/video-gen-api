from typing import Any, Dict, List, Optional, TypedDict

class TLClip(TypedDict, total=False):
    src: str
    start: float
    length: float
    fit: Optional[str]
    opacity: Optional[float]
    volume: Optional[float]
    type: str  # "video" | "image" | "audio"

def _has_tracks_like(node: Any) -> bool:
    return isinstance(node, dict) and isinstance(node.get("tracks"), list) and any(
        isinstance(t, dict) and isinstance(t.get("clips"), list) for t in node["tracks"]
    )

def is_timeline_payload(data: Dict[str, Any]) -> bool:
    return (isinstance(data, dict) and
            (("timeline" in data and _has_tracks_like(data["timeline"])) or _has_tracks_like(data)))

def _iter_tracks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "timeline" in data and isinstance(data["timeline"], dict):
        return data["timeline"].get("tracks", []) or []
    return data.get("tracks", []) or []

def extract_timeline_clips(data: Dict[str, Any]) -> List[TLClip]:
    """
    Visual clips only (video/image) — used for the video concat.
    """
    clips: List[TLClip] = []
    for tr in _iter_tracks(data):
        for c in tr.get("clips", []) or []:
            asset = c.get("asset") or {}
            t = (asset.get("type") or "").lower()
            if t not in ("video", "image"):
                continue
            length = float(c.get("length", 0.0))
            if length <= 0:
                continue
            clips.append(TLClip(
                src=str(asset.get("src", "")),
                start=float(c.get("start", 0.0)),
                length=length,
                fit=c.get("fit"),
                opacity=float(c["opacity"]) if c.get("opacity") is not None else None,
                volume=float(asset["volume"]) if asset.get("volume") is not None else None,
                type=t,
            ))
    clips.sort(key=lambda x: x["start"])
    return clips

def extract_timeline_audio(data: Dict[str, Any]) -> List[TLClip]:
    """
    Audio clips only (asset.type == 'audio') — used for audio mixing.
    """
    clips: List[TLClip] = []
    for tr in _iter_tracks(data):
        for c in tr.get("clips", []) or []:
            asset = c.get("asset") or {}
            t = (asset.get("type") or "").lower()
            if t != "audio":
                continue
            length = float(c.get("length", 0.0))
            if length <= 0:
                continue
            clips.append(TLClip(
                src=str(asset.get("src", "")),
                start=float(c.get("start", 0.0)),
                length=length,
                volume=float(asset["volume"]) if asset.get("volume") is not None else None,
                type=t,
            ))
    clips.sort(key=lambda x: x["start"])
    return clips
