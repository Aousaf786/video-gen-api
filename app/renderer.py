import os
import shutil
import subprocess
from typing import List, Optional, Tuple

import requests

from .schemas import RenderPayload
from .utils import safe_filename_from_url, resolve_asset_src
from .parser import (
    is_timeline_payload,
    extract_timeline_clips,
    extract_timeline_audio,
    extract_timeline_subtitles,
)

# --------- Config via ENV ----------
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


def download_http(url: str, dest: str) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return dest


def download_asset(url: str, dest_dir: str) -> str:
    # resolve asset:// and local paths
    resolved = resolve_asset_src(url)
    if resolved and os.path.exists(resolved):
        return resolved
    if not resolved.startswith("http"):
        return resolved
    local = os.path.join(dest_dir, safe_filename_from_url(resolved))
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local
    return download_http(resolved, local)


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
    """Insert probing/queue options just before -i."""
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


# ---------- Effects helpers ----------
def apply_effects(chain: str, effects, W: int, H: int, FPS: int, dur: float, index: int) -> tuple[str, dict]:
    """
    Supports:
      - {"type":"zoom_in"} / {"type":"zoom_out"}
      - {"type":"fade","in":0.5,"out":0.5}
      - {"type":"slide_in","direction":"up|down|left|right","duration":1.0}
      - {"type":"slide_out","direction":"up|down|left|right","duration":1.0}

    Returns:
      chain: modified filter chain string
      slide: dict with slide overlay expressions (to be used in overlay filter)
    """
    effs = effects or []
    slide_cfg = {}

    # ---- Zoom (Ken Burns style)
    for e in effs:
        t = (e.get("type") or "").lower()
        if t in ("zoom_in", "zoom_out"):
            dframes = max(1, int(round(FPS * dur)))
            step = 0.0008 if t == "zoom_in" else -0.0008
            zexpr = f"if(lte(on,1),1.0,clip(1.0+{step}*(on-1),0.9,1.2))"
            chain += f",zoompan=z='{zexpr}':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d={dframes}:s={W}x{H}:fps={FPS}"
            break

    # ---- Fade in/out
    for e in effs:
        if (e.get("type") or "").lower() == "fade":
            fin = float(e.get("in", 0.5))
            fout = float(e.get("out", 0.5))
            chain += f",fade=t=in:st=0:d={fin:.3f},fade=t=out:st={max(0.0, dur-fout):.3f}:d={fout:.3f}"

    # ---- Slide (prepare overlay expressions)
    for e in effs:
        etype = (e.get("type") or "").lower()
        if etype in ("slide_in", "slide_out"):
            direction = (e.get("direction") or "up").lower()
            dur_slide = float(e.get("duration", 1.0))

            if etype == "slide_in":
                if direction == "up":
                    slide_cfg = {"x": "(W-w)/2", "y": f"H-(t/{dur_slide})*H"}
                elif direction == "down":
                    slide_cfg = {"x": "(W-w)/2", "y": f"-(H-(t/{dur_slide})*H)"}
                elif direction == "left":
                    slide_cfg = {"x": f"W-(t/{dur_slide})*W", "y": "(H-h)/2"}
                elif direction == "right":
                    slide_cfg = {"x": f"-(W-(t/{dur_slide})*W)", "y": "(H-h)/2"}

            elif etype == "slide_out":
                st = max(0.0, dur - dur_slide)
                if direction == "up":
                    slide_cfg = {"x": "(W-w)/2", "y": f"-(t-{st})/{dur_slide}*H"}
                elif direction == "down":
                    slide_cfg = {"x": "(W-w)/2", "y": f"(t-{st})/{dur_slide}*H"}
                elif direction == "left":
                    slide_cfg = {"x": f"-(t-{st})/{dur_slide}*W", "y": "(H-h)/2"}
                elif direction == "right":
                    slide_cfg = {"x": f"(t-{st})/{dur_slide}*W", "y": "(H-h)/2"}

    return chain, slide_cfg

def _escape_sub_path(p: str) -> str:
    return p.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


def _maybe_convert_vtt_to_srt(src_path: str, workdir: str) -> str:
    # Burn-in prefers SRT/ASS; convert VTT if needed.
    base = os.path.splitext(os.path.basename(src_path))[0]
    out_srt = os.path.join(workdir, f"{base}.srt")
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        return src_path
    proc = run_cmd_capture([ffmpeg, "-y", "-hide_banner", "-i", src_path, out_srt])
    if proc.returncode == 0 and os.path.exists(out_srt):
        return out_srt
    return src_path


# ---------- Timeline builder ----------
def build_from_timeline(data: dict, workdir: str, out_path: str,
                        W: int, H: int, FPS: int, prefer_nvenc: bool) -> List[str]:
    # Parse timeline
    vclips = extract_timeline_clips(data)      # video + image
    aclips = extract_timeline_audio(data)      # audio
    subs   = extract_timeline_subtitles(data)  # subtitles

    if not vclips and not aclips and not subs:
        print("[renderer] Timeline detected but no clips found; falling back.")
        return build_black_fallback(out_path, W, H, FPS)

    # Compute total duration (longest end among clips/audio)
    total_dur = 0.0
    for c in vclips:
        total_dur = max(total_dur, float(c.get("start", 0.0)) + float(c.get("length") or 0.0))
    for a in aclips:
        total_dur = max(total_dur, float(a.get("start", 0.0)) + float(a.get("length") or 0.0))
    if total_dur <= 0:
        total_dur = 10.0

    inputs: List[str] = []
    filters: List[str] = []
    input_idx = 0

    base_labels: List[str] = []      # timeline segments to concat
    overlays: List[tuple] = []       # (label, x, y, start, dur)

    # Build base segments and/or overlay sources
    for i, c in enumerate(vclips):
        path = download_asset(c["src"], workdir)
        is_image = path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        dur = max(0.01, float(c.get("length") or 0.01))
        start = float(c.get("start", 0.0))
        fit_mode = (c.get("fit") or "cover").lower()
        force_ar = "decrease" if fit_mode == "cover" else "increase"

        if is_image:
            # Image as input: loop a single frame stream for 'dur'
            add_input(inputs, "-loop", "1", "-t", f"{dur:.3f}", "-i", path)
            vin = f"[{input_idx}:v]"
            chain = (
                f"{vin}"
                f"scale={W}:{H}:force_original_aspect_ratio={force_ar},"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={FPS},format=yuva420p"
            )
            chain = apply_effects(chain, c.get("effects"), W, H, FPS, dur, i)

            if c.get("position"):
                # OVERLAY image (do NOT push to base concat)
                # Limit duration and reset PTS so the looped image doesn't run forever
                chain += f",trim=duration={dur},setpts=PTS-STARTPTS[ovl{i}]"
                filters.append(chain)

                # Use the position from payload
                x, y = position_to_xy(c.get("position"), W, H)

                # Append overlay instruction (with start/end for enable=between)
                overlays.append((f"[ovl{i}]", x, y, start, dur))
            else:
                # STANDALONE image segment -> give it a timeline offset
                chain += f",trim=duration={dur},setpts=PTS+{start}/TB[b{i}]"
                filters.append(chain)
                base_labels.append(f"[b{i}]")
        else:
            # Video clip; trim/offset into the timeline
            if (c.get("length") or 0) > 0:
                add_input(inputs, "-ss", "0", "-t", f"{dur:.3f}", "-i", path)
            else:
                add_input(inputs, "-i", path)
            vin = f"[{input_idx}:v]"
            chain = (
                f"{vin}"
                f"trim=duration={dur},setpts=PTS+{start}/TB,"
                f"scale={W}:{H}:force_original_aspect_ratio={force_ar},"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={FPS},format=yuva420p"
            )
            if c.get("opacity") is not None:
                alpha = max(0.0, min(1.0, float(c["opacity"])))
                chain += f",colorchannelmixer=aa={alpha}"
            chain = apply_effects(chain, c.get("effects"), W, H, FPS, dur, i)
            chain += f"[b{i}]"
            filters.append(chain)
            base_labels.append(f"[b{i}]")

        input_idx += 1

    # Compose base video: concat segments if any
    vmap = None
    if base_labels:
        if len(base_labels) == 1:
            vmap = base_labels[0]
        else:
            filters.append(f"{''.join(base_labels)}concat=n={len(base_labels)}:v=1:a=0[vbase]")
            vmap = "[vbase]"
    elif overlays:
        # Only overlays but no base -> use black canvas as background
        filters.append(f"color=c=black:s={W}x{H}:d={total_dur},fps={FPS}[vbase]")
        vmap = "[vbase]"

    # Apply overlays over vmap
    if overlays and vmap:
        last = vmap
        for j, (ovl, x, y, start, dur) in enumerate(overlays):
            filters.append(
                f"{last}{ovl}overlay=x={x}:y={y}:enable='between(t,{start:.3f},{dur:.3f})'[tmp{j}]"
            )
            last = f"[tmp{j}]"
        vmap = last

    # ---- AUDIO graph ----
    audio_labels: List[str] = []
    for j, a in enumerate(aclips):
        path = download_asset(a["src"], workdir)
        dur = max(0.01, float(a.get("length") or 0.01))
        start = float(a.get("start", 0.0))
        start_ms = max(0, int(round(start * 1000)))
        vol = float(a["volume"]) if a.get("volume") is not None else 1.0

        add_input(inputs, "-ss", "0", "-t", f"{dur:.3f}", "-i", path)
        ain = f"[{input_idx}:a]"
        chain = (
            f"{ain}"
            f"aresample=async=1,volume={vol},atrim=0:{dur:.6f},asetpts=PTS-STARTPTS,"
            f"adelay={start_ms}|{start_ms}[a{j}]"
        )
        filters.append(chain)
        audio_labels.append(f"[a{j}]")
        input_idx += 1

    amap = None
    if audio_labels:
        if len(audio_labels) == 1:
            amap = audio_labels[0]
        else:
            filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0[aout]")
            amap = "[aout]"

    # ---- SUBTITLES (burn-in) ----
    if vmap:
        subs_list = subs or []
        if subs_list:
            s = subs_list[0]
            s_local = download_asset(s["src"], workdir)
            low = s_local.lower()
            if low.endswith(".vtt"):
                s_local = _maybe_convert_vtt_to_srt(s_local, workdir)
            low2 = s_local.lower()
            if low2.endswith((".srt", ".ass", ".ssa")):
                esc = _escape_sub_path(s_local)
                filters.append(f"{vmap}subtitles='{esc}'[vsub]")
                vmap = "[vsub]"

    # ---- Build final ffmpeg command ----
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

    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    if vmap:
        cmd += ["-map", vmap]
    if amap:
        cmd += ["-map", amap]

    # video codec
    vcodec = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23",
               "-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M"]
              if use_nvenc else
              ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
    cmd += vcodec + ["-r", str(FPS), "-pix_fmt", "yuv420p"]

    # audio codec
    if amap:
        cmd += ["-c:a", "aac", "-b:a", "192k"]

    # ensure we stop with the shortest stream
    cmd += ["-shortest", out_path]
    return cmd


def build_ffmpeg_cmd(payload: RenderPayload, workdir: str, out_path: str) -> List[str]:
    W, H, FPS = payload.output.width, payload.output.height, payload.output.fps
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    prefer_nvenc = (payload.output.codec or "").lower() == "h264_nvenc" or FORCE_NVENC

    # keep the raw dict if present (timeline pass-through)
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
