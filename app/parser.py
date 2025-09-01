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
    """
    True if node looks like {"tracks":[{"clips":[...]}]}.
    """
    if not isinstance(node, dict):
        return False
    tracks = node.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return False
    # At least one item with a 'clips' list
    for t in tracks:
        if isinstance(t, dict) and isinstance(t.get("clips"), list):
            return True
    return False


def is_timeline_payload(data: Dict[str, Any]) -> bool:
    """
    Accept both:
      1) {"timeline": {"tracks":[...]}}
      2) {"tracks":[...]}
    """
    if not isinstance(data, dict):
        return False
    if "timeline" in data and _has_tracks_like(data["timeline"]):
        return True
    if _has_tracks_like(data):
        return True
    return False


def _iter_tracks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "timeline" in data and isinstance(data["timeline"], dict):
        t = data["timeline"].get("tracks", [])
        return t if isinstance(t, list) else []
    return data.get("tracks", []) if isinstance(data.get("tracks"), list) else []


def extract_timeline_clips(data: Dict[str, Any]) -> List[TLClip]:
    """
    Normalize your timeline schema into a flat list of visual clips (video/image).
    We ignore pure audio tracks here (they can be added later as separate inputs).
    """
    tracks = _iter_tracks(data)
    clips: List[TLClip] = []
    for tr in tracks:
        for c in (tr.get("clips") or []):
            if not isinstance(c, dict):
                continue
            asset = c.get("asset") or {}
            if not isinstance(asset, dict):
                continue
            t = (asset.get("type") or "").lower()
            if t not in ("video", "image"):
                continue
            start = float(c.get("start", 0.0))
            length = float(c.get("length", 0.0))
            if length <= 0:
                continue
            clips.append(
                TLClip(
                    src=str(asset.get("src", "")),
                    start=start,
                    length=length,
                    fit=c.get("fit"),  # cover/contain/etc.
                    opacity=float(c["opacity"]) if c.get("opacity") is not None else None,
                    volume=float(asset["volume"]) if asset.get("volume") is not None else None,
                    type=t,
                )
            )
    clips.sort(key=lambda x: x["start"])
    return clips
