import os
import shutil
import subprocess
from typing import List, Optional, Tuple

import requests

from .schemas import RenderPayload
from .utils import safe_filename_from_url
from .parser import is_timeline_payload, extract_timeline_clips, extract_timeline_audio

# Config
INPUT_QUEUE_SIZE = os.getenv("INPUT_QUEUE_SIZE", "512")
PROBE_SIZE = os.getenv("PROBE_SIZE")
ANALYZE_DURATION = os.getenv("ANALYZE_DURATION")
FORCE_CPU = os.getenv("FORCE_CPU", "").lower() in ("1", "true", "yes", "on")
FORCE_NVENC = os.getenv("FORCE_NVENC", "").lower() in ("1", "true", "yes", "on")

def which(cmd: str) -> Optional[str]:
    from shutil import which as _which
    return _which(cmd)

def run_cmd_capture(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

def has_nvenc_encoder(ffmpeg_bin: str) -> bool:
    try:
        out = subprocess.check_output([ffmpeg_bin, "-hide_banner", "-encoders"], stderr=subprocess.STDOUT, text=True)
        return "h264_nvenc" in out
    except Exception:
        return False

def nvenc_usable(ffmpeg_bin: str) -> bool:
    test = [ffmpeg_bin, "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=320x180:rate=10:duration=1",
            "-c:v", "h264_nvenc", "-f", "null", "-"]
    proc = run_cmd_capture(test)
    return proc.returncode == 0

def download_asset(url: str, dest_dir: str) -> str:
    if not url or not isinstance(url, str):
        raise RuntimeError("empty asset src")
    if not url.startswith("http"):
        return url
    local = os.path.join(dest_dir, safe_filename_from_url(url))
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(local, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return local

def position_to_xy(position: Optional[str], W: int, H: int) -> Tuple[str, str]:
    if not position:
        return "(W-w)/2", "(H-h)/2"
    pos = position.lower()
    table = {
        "top_left": ("40", "40"),
        "top_right": ("W-w-40", "40"),
        "bottom_left": ("40", "H-h-40"),
        "bottom_right": ("W-w-40", "H-h-40"),
        "center": ("(W-w)/2", "(H-h)/2"),
        "top_center": ("(W-w)/2", "40"),
        "bottom_center": ("(W-w)/2", "H-h-40"),
        "left_center": ("40", "(H-h)/2"),
        "right_center": ("W-w-40", "(H-h)/2"),
    }
    return table.get(pos, ("(W-w)/2", "(H-h)/2"))

def add_input(args_list: List[str], *tokens: str) -> None:
    parts = list(tokens)
    try:
        i_idx = parts.index("-i")
    except ValueError:
        raise ValueError("add_input() requires an '-i <path>' token pair.")
    inject = []
    if INPUT_QUEUE_SIZE:
        inject += ["-thread_queue_size", str(INPUT_QUEUE_SIZE)]
    if PROBE_SIZE:
        inject += ["-probesize", str(PROBE_SIZE)]
    if ANALYZE_DURATION:
        inject += ["-analyzeduration", str(ANALYZE_DURATION)]
    parts[i_idx:i_idx] = inject
    args_list += parts

def build_black_fallback(out_path: str, W: int, H: int, FPS: int) -> List[str]:
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    print("[renderer] Using BLACK FALLBACK")
    return [ffmpeg, "-y", "-hide_banner", "-f", "lavfi",
            "-i", f"color=c=black:s={W}x{H}:d=10", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", out_path]

# ---------- Timeline builder ----------
def build_from_timeline(data: dict, workdir: str, out_path: str,
                        W: int, H: int, FPS: int, prefer_nvenc: bool) -> List[str]:
    # Visuals (video/image)
    vclips = extract_timeline_clips(data)
    # Audio
    aclips = extract_timeline_audio(data)

    if not vclips and not aclips:
        print("[renderer] Timeline detected but no clips found; falling back.")
        return build_black_fallback(out_path, W, H, FPS)

    inputs: List[str] = []
    filters: List[str] = []
    input_idx = 0

    # ---- VIDEO graph ----
    base_labels: List[str] = []
    total_dur = 0.0

    for i, c in enumerate(vclips):
        path = download_asset(c["src"], workdir)
        is_image = path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        dur = max(0.01, float(c["length"]))
        start = float(c.get("start", 0.0))

        if is_image:
            add_input(inputs, "-loop", "1", "-t", f"{dur:.3f}", "-i", path)
        else:
            add_input(inputs, "-ss", "0", "-t", f"{dur:.3f}", "-i", path)

        vin = f"[{input_idx}:v]"
        chain = (
            f"{vin}"
            f"scale={W}:{H}:force_original_aspect_ratio={'decrease' if (c.get('fit','cover')=='cover') else 'increase'},"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={FPS},format=yuva420p"
        )
        if c.get("opacity") is not None:
            alpha = max(0.0, min(1.0, float(c['opacity'])))
            chain += f",colorchannelmixer=aa={alpha}"
        filters.append(chain + f"[b{i}]")
        base_labels.append(f"[b{i}]")
        input_idx += 1
        total_dur = max(total_dur, start + dur)

    vmap = None
    if base_labels:
        filters.append(f"{''.join(base_labels)}concat=n={len(base_labels)}:v=1:a=0[vout]")
        vmap = "[vout]"

    # ---- AUDIO graph ----
    audio_labels: List[str] = []
    for j, a in enumerate(aclips):
        path = download_asset(a["src"], workdir)
        dur = max(0.01, float(a["length"]))
        start = float(a.get("start", 0.0))
        start_ms = max(0, int(round(start * 1000)))
        vol = float(a["volume"]) if a.get("volume") is not None else 1.0

        # bring in the audio as a normal input (trim to length for safety)
        add_input(inputs, "-ss", "0", "-t", f"{dur:.3f}", "-i", path)
        ain = f"[{input_idx}:a]"
        # chain: resample (stable clock), volume, trim to 'dur', reset pts, then delay by 'start'
        chain = (
            f"{ain}aresample=async=1,volume={vol},atrim=0:{dur:.6f},asetpts=PTS-STARTPTS,"
            f"adelay={start_ms}|{start_ms}[a{j}]"
        )
        filters.append(chain)
        audio_labels.append(f"[a{j}]")
        input_idx += 1
        total_dur = max(total_dur, start + dur)

    amap = None
    if audio_labels:
        if len(audio_labels) == 1:
            amap = audio_labels[0]
        else:
            filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0[aout]")
            amap = "[aout]"

    # Decide codecs
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")

    use_nvenc = False
    if not FORCE_CPU and prefer_nvenc:
        if has_nvenc_encoder(ffmpeg) and (FORCE_NVENC or nvenc_usable(ffmpeg)):
            use_nvenc = True

    print("[renderer] Building from TIMELINE. NVENC:", use_nvenc)

    cmd: List[str] = [ffmpeg, "-y", "-hide_banner"]
    cmd += inputs

    # Build filter_complex & mapping
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    if vmap:
        cmd += ["-map", vmap]
    if amap:
        cmd += ["-map", amap]

    # Video codec
    vcodec = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M"]
              if use_nvenc else
              ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
    cmd += vcodec + ["-r", str(FPS), "-pix_fmt", "yuv420p"]

    # Audio codec (only if we mapped audio)
    if amap:
        cmd += ["-c:a", "aac", "-b:a", "192k"]

    # Avoid hanging if audio longer than video (or vice versa)
    cmd += ["-shortest", out_path]
    return cmd
# -------------------------------------

def build_ffmpeg_cmd(payload: RenderPayload, workdir: str, out_path: str) -> List[str]:
    W, H, FPS = payload.output.width, payload.output.height, payload.output.fps
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    prefer_nvenc = (payload.output.codec or "").lower() == "h264_nvenc" or FORCE_NVENC

    # Access raw dict if present; otherwise dump the model
    raw = getattr(payload, "_raw_dict", None)
    if raw is None:
        try:
            raw = payload.model_dump()
        except Exception:
            raw = {}

    if is_timeline_payload(raw):
        print("[renderer] Detected TIMELINE payload in build_ffmpeg_cmd()")
        return build_from_timeline(raw, workdir, out_path, W, H, FPS, prefer_nvenc)

    print("[renderer] No timeline detected; using fallback")
    return build_black_fallback(out_path, W, H, FPS)

def run_ffmpeg(cmd: List[str]) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    logs: List[str] = []
    for line in proc.stdout:
        logs.append(line.rstrip())
    proc.wait()
    return proc.returncode, "\n".join(logs[-400:])
