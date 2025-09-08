import os, json, uuid, shutil
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .schemas import RenderRequest, JobStatus, RenderPayload
from .utils import fetch_payload, tmpdir
from .renderer import build_ffmpeg_cmd, run_ffmpeg
from .storage import upload_if_configured
from .parser import is_timeline_payload

PORT = int(os.getenv("PORT", "8080"))
OUTPUT_DIR = "/workspace/outputs"
BASE_URL = "https://xiff2j86qmsii8-8080.proxy.runpod.net"

os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="GPU Video Render Service", version="0.3.0")

app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

JOBS: Dict[str, JobStatus] = {}

@app.get("/healthz")
def healthz():
    return {"ok": True}

def to_payload_model_with_raw(data: Dict[str, Any]) -> RenderPayload:
    """
    Always attach the original dict as _raw_dict so the renderer can decide.
    If it's not our internal schema, renderer can still branch using the raw dict.
    """
    try:
        p = RenderPayload(**data) if not is_timeline_payload(data) else RenderPayload()
    except ValidationError:
        p = RenderPayload()
    setattr(p, "_raw_dict", data)
    return p

@app.post("/render", response_model=JobStatus)
def render(req: RenderRequest, bg: BackgroundTasks):
    if not req.payload and not req.payload_url:
        raise HTTPException(400, "Provide either 'payload' or 'payload_url'")

    if req.payload_url:
        try:
            raw_data = fetch_payload(str(req.payload_url))
        except Exception as e:
            raise HTTPException(400, f"Failed to fetch payload_url: {e}")
    else:
        raw_data = json.loads(req.payload.model_dump_json()) if req.payload else {}

    # DEBUG
    print("[render] Received payload keys:", list(raw_data.keys()))
    if is_timeline_payload(raw_data):
        print("[render] Detected TIMELINE-style payload.")
    else:
        print("[render] Detected INTERNAL-style payload (or unknown).")

    payload = to_payload_model_with_raw(raw_data)

    job_id = str(uuid.uuid4())
    file_name = req.output_filename or f"{job_id}.mp4"
    out_file = os.path.join(OUTPUT_DIR, file_name)
    JOBS[job_id] = JobStatus(id=job_id, status="queued", message="Queued")

    def worker():
        JOBS[job_id].status = "running"
        workdir = tmpdir(prefix=f"{job_id}_")
        try:
            cmd = build_ffmpeg_cmd(payload, workdir, out_file)
            print("[render] ffmpeg cmd:", " ".join(cmd))
            rc, logs = run_ffmpeg(cmd)
            if rc != 0:
                JOBS[job_id].status = "failed"
                JOBS[job_id].message = f"FFmpeg exited with {rc}"
                JOBS[job_id].logs = logs
                return
            url = upload_if_configured(out_file)
            JOBS[job_id].status = "success"
            JOBS[job_id].output_url = f"{BASE_URL}/outputs/{file_name}"
            JOBS[job_id].logs = logs
        except Exception as e:
            JOBS[job_id].status = "failed"
            JOBS[job_id].message = f"Error: {e}"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    bg.add_task(worker)
    return JOBS[job_id]

@app.get("/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job
