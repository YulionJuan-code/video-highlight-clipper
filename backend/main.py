"""
FastAPI backend for video highlight extraction web app.

Endpoints:
  - Settings API (GET/POST /api/settings)
  - Upload, task management, SSE progress, download
"""

import os
import json
import uuid
import shutil
import asyncio
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clipper_service import ClipperService, TaskCancelled, get_video_duration, FFPROBE

# ── Paths ──
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
SETTINGS_PATH = Path(__file__).parent / "settings.json"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ── App ──
app = FastAPI(title="Video Highlight Clipper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount uploads directory as static files
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# ── In-memory task store ──
tasks: dict = {}
# SSE subscribers: task_id -> list[asyncio.Queue]
sse_queues: dict[str, list[asyncio.Queue]] = {}
# Cancel flags: task_id -> asyncio.Event (set = cancelled)
cancel_flags: dict[str, asyncio.Event] = {}


# ── Models ──
class SettingsModel(BaseModel):
    # ASR provider: "volcengine" or "openai"
    asr_provider: str = "openai"
    # Volcengine ASR (only for volcengine provider)
    asr_app_id: str = ""
    asr_access_key: str = ""
    # OpenAI-compatible ASR (universal)
    asr_api_key: str = ""
    asr_base_url: str = ""
    asr_model: str = "whisper-1"
    # Text model provider
    text_api_key: str = ""
    text_base_url: str = ""
    text_model: str = ""
    # Vision model provider
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    # Processing defaults
    silence_db: int = -40
    silence_min_dur: float = 2.0
    active_min_dur: float = 3.0
    keyframe_interval: int = 2
    default_min_score: int = 6


class StartTaskBody(BaseModel):
    interest_description: str = ""
    min_score: int = 6
    keyframe_interval: int = 0  # 0 = use default from settings
    resume: bool = False


class UpdateSegmentsBody(BaseModel):
    segments: list


# ── Settings helpers ──
def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        # Migrate: add asr_provider if missing
        if "asr_provider" not in data:
            # If they had volcengine credentials, default to volcengine
            if data.get("asr_app_id"):
                data["asr_provider"] = "volcengine"
            else:
                data["asr_provider"] = "openai"
            data.setdefault("asr_api_key", "")
            data.setdefault("asr_base_url", "")
            data.setdefault("asr_model", "whisper-1")
            save_settings(data)
        # Migrate old ark_* format to new separate text/vision format
        if "ark_api_key" in data and "text_api_key" not in data:
            old_key = data.pop("ark_api_key", "")
            old_url = data.pop("ark_base_url", "")
            old_model = data.pop("ark_model", "")
            old_vision = data.pop("ark_vision_model", "")
            data["text_api_key"] = old_key
            data["text_base_url"] = old_url
            data["text_model"] = old_model
            data["vision_api_key"] = old_key
            data["vision_base_url"] = old_url
            data["vision_model"] = old_vision
            save_settings(data)
        return data
    # Return defaults
    return SettingsModel().model_dump()


def save_settings(data: dict):
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Task helpers ──
def _make_progress_callback(task_id: str):
    """Create a progress callback that updates task state and pushes to SSE."""
    def callback(step: float, name: str, status: str, message: str,
                 progress: int = None, data: dict = None):
        event = {
            "step": step,
            "name": name,
            "status": status,
            "message": message,
        }
        if progress is not None:
            event["progress"] = progress
        if data is not None:
            event["data"] = data

        # Update task steps list
        if task_id in tasks:
            task = tasks[task_id]
            # Replace or append step
            existing = None
            for i, s in enumerate(task["steps"]):
                if s["step"] == step and s["name"] == name:
                    existing = i
                    break
            if existing is not None:
                task["steps"][existing] = event
            else:
                task["steps"].append(event)

            # Update overall status
            if status == "done" and name == "done":
                task["status"] = "completed"
            elif status == "error":
                task["status"] = "error"

        # Push to SSE subscribers
        if task_id in sse_queues:
            for q in sse_queues[task_id]:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    return callback


async def _run_task(task_id: str, interest: str, min_score: int,
                    resume: bool = False, settings_override: dict = None):
    """Background coroutine to run the clipper pipeline."""
    task = tasks.get(task_id)
    if not task:
        return

    task["status"] = "processing"
    settings = load_settings()
    if settings_override:
        settings.update(settings_override)
    callback = _make_progress_callback(task_id)

    cancel_event = asyncio.Event()
    cancel_flags[task_id] = cancel_event

    # If not resuming, clear intermediate files
    if not resume:
        output_dir = Path(task["output_dir"])
        for f in ("transcript.json", "visual_analysis.json",
                   "segments_report.json", "highlights.mp4",
                   "_concat.txt"):
            (output_dir / f).unlink(missing_ok=True)
        # Clear clip files and frames
        for p in output_dir.glob("_clip_*.mp4"):
            p.unlink(missing_ok=True)
        frames_dir = output_dir / "_frames"
        if frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)

    service = ClipperService(
        task_id=task_id,
        video_path=task["video_path"],
        output_dir=task["output_dir"],
        settings=settings,
        interest=interest,
        min_score=min_score,
        progress_callback=callback,
        cancel_event=cancel_event,
    )

    try:
        await service.run()
        # Load final segments if they exist
        report_path = Path(task["output_dir"]) / "segments_report.json"
        if report_path.exists():
            task["segments"] = json.loads(report_path.read_text(encoding="utf-8"))
    except TaskCancelled:
        task["status"] = "cancelled"
        callback(step=0, name="cancelled", status="done",
                 message="任务已取消")
    except Exception as e:
        task["status"] = "error"
        task["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        callback(step=0, name="error", status="error", message=str(e))
    finally:
        cancel_flags.pop(task_id, None)


async def _run_reexport(task_id: str, segments: list):
    """Background coroutine to re-run cut+concat only."""
    task = tasks.get(task_id)
    if not task:
        return

    task["status"] = "processing"
    settings = load_settings()
    callback = _make_progress_callback(task_id)

    service = ClipperService(
        task_id=task_id,
        video_path=task["video_path"],
        output_dir=task["output_dir"],
        settings=settings,
        interest="",
        min_score=0,
        progress_callback=callback,
    )

    try:
        await service.reexport(segments)
        task["segments"] = segments
    except Exception as e:
        task["status"] = "error"
        task["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        callback(step=0, name="error", status="error", message=str(e))


def _restore_tasks():
    """Scan uploads directory to restore previous tasks on startup."""
    if not UPLOADS_DIR.exists():
        return
    for task_dir in UPLOADS_DIR.iterdir():
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        # Find the video file
        video_files = [
            f for f in task_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".ts", ".webm")
        ]
        if not video_files:
            continue

        video_file = video_files[0]
        output_dir = str(task_dir)

        # Determine status from existing files
        status = "uploaded"
        segments = None
        error = None

        report_path = task_dir / "segments_report.json"
        highlights_path = task_dir / "highlights.mp4"

        if highlights_path.exists():
            status = "completed"
        elif report_path.exists():
            status = "uploaded"  # has partial results, not fully completed

        if report_path.exists():
            try:
                segments = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                segments = None

        # Get file info
        try:
            file_size = video_file.stat().st_size
        except Exception:
            file_size = 0

        try:
            duration = get_video_duration(str(video_file))
        except Exception:
            duration = 0

        created_at = datetime.fromtimestamp(video_file.stat().st_mtime).isoformat()

        tasks[task_id] = {
            "task_id": task_id,
            "filename": video_file.name,
            "video_path": str(video_file),
            "output_dir": output_dir,
            "status": status,
            "steps": [],
            "segments": segments,
            "error": error,
            "created_at": created_at,
            "duration": duration,
            "file_size": file_size,
        }


# ══════════════════════════════════════════════════════════
#  API Endpoints
# ══════════════════════════════════════════════════════════

# ── Settings ──

@app.get("/api/settings")
async def get_settings():
    return load_settings()


@app.post("/api/settings")
async def post_settings(settings: SettingsModel):
    data = settings.model_dump()
    # Trim whitespace from all string values (prevent paste artifacts)
    for k, v in data.items():
        if isinstance(v, str):
            data[k] = v.strip()
    save_settings(data)
    return {"ok": True}


# ── Upload ──

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())
    task_dir = UPLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # Save file
    file_path = task_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)
    file_size = len(content)

    # Get duration via ffprobe
    try:
        duration = get_video_duration(str(file_path))
    except Exception as e:
        duration = 0

    task = {
        "task_id": task_id,
        "filename": file.filename,
        "video_path": str(file_path),
        "output_dir": str(task_dir),
        "status": "uploaded",
        "steps": [],
        "segments": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
        "duration": duration,
        "file_size": file_size,
    }
    tasks[task_id] = task

    return {
        "task_id": task_id,
        "filename": file.filename,
        "duration": duration,
        "file_size": file_size,
    }


# ── Task operations ──

@app.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str, body: StartTaskBody):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    task = tasks[task_id]
    if task["status"] == "processing":
        raise HTTPException(400, "Task is already processing")

    # Save params for restart/resume
    task["interest"] = body.interest_description
    task["min_score"] = body.min_score
    if body.keyframe_interval > 0:
        task["keyframe_interval"] = body.keyframe_interval

    # Reset task state
    task["status"] = "processing"
    task["steps"] = []
    task["segments"] = None
    task["error"] = None

    # Initialize SSE queue list
    if task_id not in sse_queues:
        sse_queues[task_id] = []

    # Start background processing — override keyframe_interval if specified
    settings_override = {}
    if body.keyframe_interval > 0:
        settings_override["keyframe_interval"] = body.keyframe_interval

    asyncio.create_task(
        _run_task(task_id, body.interest_description, body.min_score,
                  resume=body.resume, settings_override=settings_override)
    )

    return {"ok": True, "task_id": task_id}


@app.get("/api/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    task = tasks[task_id]
    return {
        "task_id": task["task_id"],
        "filename": task["filename"],
        "status": task["status"],
        "steps": task["steps"],
        "error": task.get("error"),
        "created_at": task["created_at"],
        "duration": task.get("duration", 0),
        "file_size": task.get("file_size", 0),
        "has_segments": task["segments"] is not None,
        "has_highlights": (Path(task["output_dir"]) / "highlights.mp4").exists(),
        "interest": task.get("interest", ""),
        "min_score": task.get("min_score", 6),
    }


@app.get("/api/tasks/{task_id}/segments")
async def get_task_segments(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    report_path = Path(tasks[task_id]["output_dir"]) / "segments_report.json"
    if report_path.exists():
        return json.loads(report_path.read_text(encoding="utf-8"))

    if tasks[task_id]["segments"]:
        return tasks[task_id]["segments"]

    raise HTTPException(404, "Segments not yet available")


@app.get("/api/tasks/{task_id}/transcript")
async def get_task_transcript(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    transcript_path = Path(tasks[task_id]["output_dir"]) / "transcript.json"
    if transcript_path.exists():
        return json.loads(transcript_path.read_text(encoding="utf-8"))

    raise HTTPException(404, "Transcript not yet available")


@app.get("/api/tasks/{task_id}/visual")
async def get_task_visual(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    visual_path = Path(tasks[task_id]["output_dir"]) / "visual_analysis.json"
    if visual_path.exists():
        return json.loads(visual_path.read_text(encoding="utf-8"))

    raise HTTPException(404, "Visual analysis not yet available")


@app.post("/api/tasks/{task_id}/segments")
async def update_task_segments(task_id: str, body: UpdateSegmentsBody):
    """Accept modified segments (user can toggle keep on/off), re-run cut+concat."""
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    task = tasks[task_id]
    if task["status"] == "processing":
        raise HTTPException(400, "Task is currently processing")

    # Reset for re-export
    task["status"] = "processing"
    task["error"] = None
    # Keep existing steps but clear cutting steps
    task["steps"] = [s for s in task["steps"] if s.get("step", 0) < 4]

    if task_id not in sse_queues:
        sse_queues[task_id] = []

    asyncio.create_task(_run_reexport(task_id, body.segments))

    return {"ok": True, "task_id": task_id}


@app.get("/api/tasks/{task_id}/download")
async def download_highlights(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    highlights_path = Path(tasks[task_id]["output_dir"]) / "highlights.mp4"
    if not highlights_path.exists():
        raise HTTPException(404, "Highlights video not yet available")

    return FileResponse(
        str(highlights_path),
        media_type="video/mp4",
        filename=f"highlights_{tasks[task_id]['filename']}",
    )


@app.get("/api/tasks/{task_id}/frames/{frame_name}")
async def get_frame(task_id: str, frame_name: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    frame_path = Path(tasks[task_id]["output_dir"]) / "_frames" / frame_name
    if not frame_path.exists():
        raise HTTPException(404, "Frame not found")

    return FileResponse(str(frame_path), media_type="image/jpeg")


@app.get("/api/tasks")
async def list_tasks():
    result = []
    for task_id, task in tasks.items():
        result.append({
            "task_id": task["task_id"],
            "filename": task["filename"],
            "status": task["status"],
            "created_at": task["created_at"],
            "duration": task.get("duration", 0),
            "file_size": task.get("file_size", 0),
            "has_highlights": (Path(task["output_dir"]) / "highlights.mp4").exists(),
        })
    # Sort by created_at descending
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return result


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    # Cancel if processing
    if task_id in cancel_flags:
        cancel_flags[task_id].set()

    # Remove from memory
    task = tasks.pop(task_id)
    sse_queues.pop(task_id, None)
    cancel_flags.pop(task_id, None)

    # Delete files from disk
    task_dir = Path(task["output_dir"])
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)

    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    if tasks[task_id]["status"] != "processing":
        raise HTTPException(400, "Task is not processing")
    if task_id in cancel_flags:
        cancel_flags[task_id].set()
    return {"ok": True}


# ── SSE Progress Stream ──

@app.get("/api/tasks/{task_id}/progress")
async def task_progress_sse(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    if task_id not in sse_queues:
        sse_queues[task_id] = []
    sse_queues[task_id].append(queue)

    async def event_generator():
        try:
            # First, send all existing steps as catch-up
            task = tasks.get(task_id)
            if task:
                for step_event in task["steps"]:
                    yield f"data: {json.dumps(step_event, ensure_ascii=False)}\n\n"

            # Then stream live events
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                    # If done or error, end the stream
                    if event.get("name") in ("done", "error") and event.get("status") in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        finally:
            # Cleanup subscriber
            if task_id in sse_queues and queue in sse_queues[task_id]:
                sse_queues[task_id].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Serve frontend static files ──
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    # Serve static assets (js, css, etc.)
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend_assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve frontend SPA — any non-API route returns index.html"""
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found")
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))


# ── Startup ──

@app.on_event("startup")
async def startup():
    _restore_tasks()
    print(f"Restored {len(tasks)} tasks from {UPLOADS_DIR}")
    print(f"Settings file: {SETTINGS_PATH}")
    if FRONTEND_DIST.exists():
        print(f"Serving frontend from {FRONTEND_DIST}")
    else:
        print(f"WARNING: Frontend dist not found at {FRONTEND_DIST}. Run 'npm run build' in frontend/")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
