"""
ClipperService — Refactored video highlight extraction pipeline.

Wraps the full pipeline (silence detection -> ASR -> visual analysis -> LLM scoring -> ffmpeg cut+concat)
into a class that reports progress via callback and reads settings from a dict.
"""

import os
import json
import gzip
import uuid
import struct
import asyncio
import subprocess
import websockets
import base64
import shutil
from pathlib import Path
from typing import Callable, Optional
from openai import OpenAI


class TaskCancelled(Exception):
    """Raised when a task is cancelled by the user."""
    pass


# ── ASR protocol constants ──
ASR_RESOURCE_ID = "volc.bigasr.sauc.duration"
ASR_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"

PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001
MSG_FULL_CLIENT = 0b0001
MSG_AUDIO_ONLY = 0b0010
MSG_FULL_SERVER = 0b1001
SERIALIZATION_JSON = 0b0001
COMPRESS_GZIP = 0b0001
COMPRESS_NONE = 0b0000

# ASR audio params
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_MS = 200


def _find_ffmpeg() -> str:
    """Find ffmpeg executable on PATH."""
    return shutil.which("ffmpeg") or "ffmpeg"


def _find_ffprobe() -> str:
    """Find ffprobe executable on PATH."""
    return shutil.which("ffprobe") or "ffprobe"


FFMPEG = _find_ffmpeg()
FFPROBE = _find_ffprobe()


def get_video_duration(video_path: str) -> float:
    """Use ffprobe to get video duration in seconds."""
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", video_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    data = json.loads(probe.stdout)
    return float(data["format"]["duration"])


# ── ASR protocol helpers ──

def _build_header(msg_type: int, flags: int = 0,
                  serial: int = SERIALIZATION_JSON,
                  compress: int = COMPRESS_NONE) -> bytes:
    b0 = (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE
    b1 = (msg_type << 4) | flags
    b2 = (serial << 4) | compress
    b3 = 0x00
    return bytes([b0, b1, b2, b3])


def _pack_request(payload: bytes, msg_type: int,
                  compress: bool = False) -> bytes:
    if compress:
        payload = gzip.compress(payload)
        hdr = _build_header(msg_type, compress=COMPRESS_GZIP)
    else:
        hdr = _build_header(msg_type)
    return hdr + struct.pack(">I", len(payload)) + payload


def _parse_response(data: bytes) -> dict:
    if len(data) < 12:
        return {}
    sequence = struct.unpack(">I", data[4:8])[0]
    payload_size = struct.unpack(">I", data[8:12])[0]
    if payload_size == 0:
        return {}
    payload = data[12:12 + payload_size]
    header_b2 = data[2]
    compress_flag = header_b2 & 0x0F
    if compress_flag == COMPRESS_GZIP:
        payload = gzip.decompress(payload)
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        result = json.loads(text)
        result["_sequence"] = sequence
        return result
    except json.JSONDecodeError:
        return {}


def _collect_asr_result(resp: dict, seg_start: float,
                        results: list, final_only: bool = False):
    result = resp.get("result", {})
    utterances = result.get("utterances", [])
    for utt in utterances:
        is_definite = utt.get("definite", False)
        if final_only and not is_definite:
            continue
        text = utt.get("text", "").strip()
        if not text:
            continue
        start_ms = utt.get("start_time", 0)
        end_ms = utt.get("end_time", 0)
        results.append({
            "text": text,
            "start": round(seg_start + start_ms / 1000, 3),
            "end": round(seg_start + end_ms / 1000, 3),
        })


class ClipperService:
    """
    Encapsulates the full video highlight extraction pipeline.

    Progress callback signature:
        callback(step: float, name: str, status: str, message: str,
                 progress: int | None = None, data: dict | None = None)
    """

    def __init__(
        self,
        task_id: str,
        video_path: str,
        output_dir: str,
        settings: dict,
        interest: str,
        min_score: int,
        progress_callback: Optional[Callable] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ):
        self.task_id = task_id
        self.video_path = str(Path(video_path).resolve())
        self.output_dir = output_dir
        self.settings = settings
        self.interest = interest
        self.min_score = min_score
        self._progress_callback = progress_callback
        self._cancel_event = cancel_event

        # Extract settings with defaults
        self.asr_provider = settings.get("asr_provider", "openai")
        self.asr_app_id = settings.get("asr_app_id", "")
        self.asr_access_key = settings.get("asr_access_key", "")
        self.asr_api_key = settings.get("asr_api_key", "")
        self.asr_base_url = settings.get("asr_base_url", "")
        self.asr_model = settings.get("asr_model", "whisper-1")
        # Text model provider
        self.text_api_key = settings.get("text_api_key", "")
        self.text_base_url = settings.get("text_base_url", "")
        self.text_model = settings.get("text_model", "")
        # Vision model provider
        self.vision_api_key = settings.get("vision_api_key", "")
        self.vision_base_url = settings.get("vision_base_url", "")
        self.vision_model = settings.get("vision_model", "")
        self.silence_db = settings.get("silence_db", -40)
        self.silence_min_dur = settings.get("silence_min_dur", 2.0)
        self.active_min_dur = settings.get("active_min_dur", 3.0)
        self.keyframe_interval = settings.get("keyframe_interval", 2)

        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def progress(self, step: float, name: str, status: str, message: str,
                 progress: int = None, data: dict = None):
        if self._progress_callback:
            self._progress_callback(
                step=step, name=name, status=status,
                message=message, progress=progress, data=data,
            )

    def check_cancel(self):
        """Raise TaskCancelled if the cancel event has been set."""
        if self._cancel_event and self._cancel_event.is_set():
            raise TaskCancelled("Task cancelled by user")

    @staticmethod
    def _load_cache(path: Path):
        """Load cached JSON file. Returns None if missing or empty."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) == 0:
                path.unlink()
                return None
            return data
        except (json.JSONDecodeError, Exception):
            path.unlink(missing_ok=True)
            return None

    # ──────────────────────────────────────────────────────
    #  Full pipeline
    # ──────────────────────────────────────────────────────

    async def run(self):
        """Run the full pipeline with cancel checks and cache support."""
        # Validate required settings before starting
        errors = []
        if self.asr_provider == "openai" and not self.asr_api_key:
            errors.append("ASR API Key 未配置")
        elif self.asr_provider == "volcengine" and (not self.asr_app_id or not self.asr_access_key):
            errors.append("ASR App ID 或 Access Key 未配置")
        if not self.text_api_key:
            errors.append("文本模型 API Key 未配置")
        if not self.text_model:
            errors.append("文本模型名称未配置")
        if not self.vision_api_key:
            errors.append("视觉模型 API Key 未配置")
        if not self.vision_model:
            errors.append("视觉模型名称未配置")
        if errors:
            msg = "请先在设置页面完成配置：" + "；".join(errors)
            raise ValueError(msg)

        output = Path(self.output_dir)
        transcript_path = output / "transcript.json"
        visual_path = output / "visual_analysis.json"
        report_path = output / "segments_report.json"

        # Step 1: Silence detection (always run, fast)
        self.check_cancel()
        self.progress(1, "silence_detection", "running", "静音检测中...")
        active_segments = await asyncio.to_thread(self.detect_active_segments)
        total_active = sum(s["end"] - s["start"] for s in active_segments)
        total_dur = await asyncio.to_thread(get_video_duration, self.video_path)
        self.progress(
            1, "silence_detection", "done",
            f"总时长 {total_dur:.0f}s | 有声段 {len(active_segments)} 个 | 有效时长 {total_active:.0f}s",
            data={"active_segments": active_segments, "total_duration": total_dur},
        )

        if not active_segments:
            raise ValueError("未检测到有声内容，视频可能是纯静音")

        # Step 2: ASR transcription (use cache if available)
        self.check_cancel()
        cached_transcript = self._load_cache(transcript_path)
        if cached_transcript is not None:
            sentences = cached_transcript
            self.progress(2, "asr", "done",
                          f"转录已有缓存，共 {len(sentences)} 句（跳过）",
                          data={"sentence_count": len(sentences)})
        else:
            self.progress(2, "asr", "running", "ASR 转录中...")
            sentences = await self.transcribe_video(active_segments)
            transcript_path.write_text(
                json.dumps(sentences, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.progress(2, "asr", "done",
                          f"转录完成，共 {len(sentences)} 句",
                          data={"sentence_count": len(sentences)})

        if not sentences:
            raise ValueError("ASR 转录结果为空，请检查 ASR 配置（API Key 是否正确、是否有多余空格）")

        # Step 2.5: Visual analysis (use cache if available)
        self.check_cancel()
        cached_visual = self._load_cache(visual_path)
        if cached_visual is not None:
            visual_results = cached_visual
            match_count = sum(1 for r in visual_results if r.get("match"))
            self.progress(2.5, "visual", "done",
                          f"视觉分析已有缓存：{len(visual_results)} 帧（跳过）",
                          data={"frame_count": len(visual_results), "match_count": match_count})
        else:
            self.progress(2.5, "visual", "running", "视觉分析中...")
            total_duration = sentences[-1]["end"] if sentences else 0
            frames = await asyncio.to_thread(self.extract_keyframes, total_duration)
            visual_results = await asyncio.to_thread(self.analyze_keyframes, frames)
            visual_path.write_text(
                json.dumps(visual_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            match_count = sum(1 for r in visual_results if r.get("match"))
            self.progress(2.5, "visual", "done",
                          f"视觉分析完成：{len(visual_results)} 帧，{match_count} 帧匹配",
                          data={"frame_count": len(visual_results), "match_count": match_count})

        # Step 3: LLM semantic segmentation + scoring (use cache if available)
        self.check_cancel()
        cached_report = self._load_cache(report_path)
        if cached_report is not None:
            segments = cached_report
            kept = [s for s in segments if s.get("keep")]
            self.progress(3, "llm", "done",
                          f"语义分块已有缓存，共 {len(segments)} 块（跳过）",
                          data={"segment_count": len(segments), "kept_count": len(kept)})
        else:
            self.progress(3, "llm", "running", "LLM 语义分块+评分中...")
            segments = await asyncio.to_thread(self.semantic_segment, sentences, visual_results)
            report_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            kept = [s for s in segments if s.get("keep")]
            self.progress(3, "llm", "done",
                          f"共 {len(segments)} 个语义块，保留 {len(kept)} 个",
                          data={"segment_count": len(segments), "kept_count": len(kept)})

        # Step 4+5: Cut and concat
        self.check_cancel()
        kept = [s for s in segments if s.get("keep")]
        self.progress(4, "cutting", "running", f"裁剪拼接中（{len(kept)} 个片段）...")
        output_video = str(output / "highlights.mp4")
        await asyncio.to_thread(self.cut_and_concat, segments, output_video)

        total_kept_sec = sum(s["end"] - s["start"] for s in kept)
        self.progress(
            5, "done", "done",
            f"处理完成！精华时长 {total_kept_sec:.0f}s（{total_kept_sec / 60:.1f} 分钟）",
            data={"highlights_duration": total_kept_sec},
        )

    # ──────────────────────────────────────────────────────
    #  Step 1: Silence detection
    # ──────────────────────────────────────────────────────

    def detect_active_segments(self) -> list:
        cmd = [
            FFMPEG, "-i", self.video_path,
            "-af", f"silencedetect=noise={self.silence_db}dB:d={self.silence_min_dur}",
            "-f", "null", "-",
        ]
        out = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stderr

        silence_starts, silence_ends = [], []
        for line in out.splitlines():
            if "silence_start" in line:
                silence_starts.append(float(line.split("silence_start:")[-1].strip()))
            elif "silence_end" in line:
                silence_ends.append(float(line.split("silence_end:")[-1].split("|")[0].strip()))

        total = get_video_duration(self.video_path)

        active = []
        cursor = 0.0
        for s, e in zip(silence_starts, silence_ends):
            if s - cursor >= self.active_min_dur:
                active.append({"start": round(cursor, 3), "end": round(s, 3)})
            cursor = e
        if total - cursor >= self.active_min_dur:
            active.append({"start": round(cursor, 3), "end": round(total, 3)})

        return active

    # ──────────────────────────────────────────────────────
    #  Step 2: ASR transcription
    # ──────────────────────────────────────────────────────

    def _extract_audio_chunk(self, start: float, end: float) -> bytes:
        duration = end - start
        cmd = [
            FFMPEG, "-y",
            "-ss", str(start), "-t", str(duration),
            "-i", self.video_path,
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "pipe:1",
        ]
        result = subprocess.run(cmd, capture_output=True)
        return result.stdout

    async def _asr_one_segment(self, pcm_data: bytes, seg_start: float) -> list:
        connect_id = str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.asr_app_id,
            "X-Api-Access-Key": self.asr_access_key,
            "X-Api-Resource-Id": ASR_RESOURCE_ID,
            "X-Api-Connect-Id": connect_id,
        }

        config_payload = json.dumps({
            "user": {"uid": "clipper"},
            "audio": {
                "format": "pcm",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "bits": SAMPLE_WIDTH * 8,
                "language": "zh-CN",
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punctuation": True,
                "enable_time_stamp": True,
            },
        }, ensure_ascii=False).encode("utf-8")

        results = []
        chunk_bytes = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * CHUNK_MS / 1000)

        async with websockets.connect(ASR_WS_URL, additional_headers=headers) as ws:
            # Send initial config packet
            await ws.send(_pack_request(config_payload, MSG_FULL_CLIENT))

            # Stream audio chunks
            offset = 0
            total = len(pcm_data)
            while offset < total:
                chunk = pcm_data[offset: offset + chunk_bytes]
                offset += chunk_bytes
                is_last = (offset >= total)

                flags = 0b0010 if is_last else 0b0000
                hdr = _build_header(MSG_AUDIO_ONLY, flags=flags)
                frame = hdr + struct.pack(">I", len(chunk)) + chunk
                await ws.send(frame)

                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=0.05)
                    resp = _parse_response(resp_raw)
                    _collect_asr_result(resp, seg_start, results, final_only=True)
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                    pass

            # Wait for final results
            try:
                while True:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                    is_final_frame = (len(resp_raw) >= 2 and (resp_raw[1] & 0x0F) == 0x03)
                    resp = _parse_response(resp_raw)
                    _collect_asr_result(resp, seg_start, results, final_only=True)
                    if is_final_frame:
                        break
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                pass

        return results

    # ── OpenAI Whisper-compatible ASR ──

    def _extract_audio_file(self, start: float, end: float, out_path: str):
        """Extract audio segment to an mp3 file for Whisper API."""
        duration = end - start
        cmd = [
            FFMPEG, "-y",
            "-ss", str(start), "-t", str(duration),
            "-i", self.video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            "-b:a", "64k", out_path,
        ]
        subprocess.run(cmd, capture_output=True)

    def _whisper_transcribe_segment(self, seg_start: float, seg_end: float) -> list:
        """Transcribe a segment using OpenAI Whisper-compatible API."""
        import tempfile
        tmp_path = os.path.join(self.output_dir, f"_asr_tmp_{seg_start:.0f}.mp3")
        try:
            self._extract_audio_file(seg_start, seg_end, tmp_path)
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                return []

            client = OpenAI(
                api_key=self.asr_api_key,
                base_url=self.asr_base_url or None,
            )
            with open(tmp_path, "rb") as f:
                resp = client.audio.transcriptions.create(
                    model=self.asr_model,
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    language="zh",
                )

            sentences = []
            if hasattr(resp, "segments") and resp.segments:
                for seg in resp.segments:
                    sentences.append({
                        "start": round(seg_start + seg.start, 3),
                        "end": round(seg_start + seg.end, 3),
                        "text": seg.text.strip(),
                    })
            elif hasattr(resp, "text") and resp.text:
                # Fallback: no segment timestamps, treat as one chunk
                sentences.append({
                    "start": round(seg_start, 3),
                    "end": round(seg_end, 3),
                    "text": resp.text.strip(),
                })
            return sentences
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    async def transcribe_video(self, active_segments: list) -> list:
        ASR_MAX_DURATION = 60

        sub_segments = []
        for seg in active_segments:
            seg_start = seg["start"]
            seg_end = seg["end"]
            duration = seg_end - seg_start
            if duration <= ASR_MAX_DURATION:
                sub_segments.append(seg)
            else:
                cursor = seg_start
                while cursor < seg_end:
                    sub_end = min(cursor + ASR_MAX_DURATION, seg_end)
                    sub_segments.append({"start": cursor, "end": sub_end})
                    cursor = sub_end

        all_sentences = []
        total_subs = len(sub_segments)
        for i, seg in enumerate(sub_segments):
            seg_start = seg["start"]
            seg_end = seg["end"]
            pct = int((i + 1) / total_subs * 100) if total_subs > 0 else 0
            self.progress(
                2, "asr", "running",
                f"转录 [{i + 1}/{total_subs}] {seg_start:.1f}s~{seg_end:.1f}s",
                progress=pct,
            )

            try:
                if self.asr_provider == "volcengine":
                    pcm = self._extract_audio_chunk(seg_start, seg_end)
                    if not pcm:
                        continue
                    sentences = await self._asr_one_segment(pcm, seg_start)
                else:
                    sentences = self._whisper_transcribe_segment(seg_start, seg_end)
            except TaskCancelled:
                raise
            except Exception as e:
                # First segment failure = likely config error, raise immediately
                if not all_sentences:
                    raise RuntimeError(f"ASR 转录失败: {e}") from e
                # Later failures: log and continue
                self.progress(
                    2, "asr", "running",
                    f"转录 [{i + 1}/{total_subs}] ASR 错误: {e}，跳过",
                    progress=pct,
                )
                continue
            all_sentences.extend(sentences)

        return all_sentences

    # ──────────────────────────────────────────────────────
    #  Step 2.5: Visual analysis
    # ──────────────────────────────────────────────────────

    def extract_keyframes(self, total_duration: float) -> list:
        frames_dir = Path(self.output_dir) / "_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            [
                FFMPEG, "-y",
                "-i", self.video_path,
                "-vf", f"fps=1/{self.keyframe_interval}",
                "-q:v", "5",
                str(frames_dir / "frame_%04d.jpg"),
            ],
            capture_output=True, encoding="utf-8", errors="replace",
        )

        frames = []
        for img_path in sorted(frames_dir.glob("frame_*.jpg")):
            idx = int(img_path.stem.split("_")[1]) - 1
            timestamp = idx * self.keyframe_interval
            if timestamp <= total_duration:
                frames.append({"time": timestamp, "path": str(img_path)})
        return frames

    def analyze_keyframes(self, frames: list) -> list:
        if not frames:
            return []

        client = OpenAI(
            api_key=self.vision_api_key,
            base_url=self.vision_base_url or None,
        )
        results = []
        BATCH_SIZE = 4
        total_batches = (len(frames) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_idx, batch_start in enumerate(range(0, len(frames), BATCH_SIZE)):
            batch = frames[batch_start:batch_start + BATCH_SIZE]
            time_labels = ", ".join(f"{f['time']:.0f}s" for f in batch)
            pct = int((batch_idx + 1) / total_batches * 100) if total_batches > 0 else 0
            self.progress(
                2.5, "visual", "running",
                f"分析帧: {time_labels}",
                progress=pct,
            )

            content = []
            content.append({
                "type": "text",
                "text": (
                    f"以下是视频中 {len(batch)} 帧关键画面，时间戳分别为 {time_labels}。\n"
                    f"筛选目标：{self.interest}\n"
                    "请逐帧简要描述：1）场景和人物外貌/服装 2）画面色调/光线风格\n"
                    "3）该帧是否符合筛选目标（match字段）。请仔细辨认人物服装颜色和外貌特征，不要混淆不同角色。\n"
                    "用JSON数组返回，每帧一个对象：{\"time\": 秒数, \"scene\": \"描述\", \"style\": \"色调描述\", \"match\": true/false}\n"
                    "只返回JSON数组，不要其他文字。"
                ),
            })
            for f in batch:
                with open(f["path"], "rb") as img_file:
                    b64 = base64.b64encode(img_file.read()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })

            try:
                resp = client.chat.completions.create(
                    model=self.vision_model,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=2048,
                    temperature=0.1,
                )
                raw = resp.choices[0].message.content.strip()
                raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                batch_results = json.loads(raw)
                results.extend(batch_results)
            except Exception as e:
                for f in batch:
                    results.append({"time": f["time"], "scene": "", "style": "", "match": False})

        return results

    def _build_visual_summary(self, visual_results: list) -> str:
        lines = []
        for r in visual_results:
            tag = " [符合目标]" if r.get("match") else ""
            scene = r.get("scene", "")
            style = r.get("style", "")
            lines.append(f"[{r['time']:.0f}s] {scene} | 风格: {style}{tag}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────
    #  Step 3: LLM semantic segmentation + scoring
    # ──────────────────────────────────────────────────────

    def _build_transcript_text(self, sentences: list) -> str:
        lines = []
        for s in sentences:
            t = f"[{s['start']:.1f}s]"
            lines.append(f"{t} {s['text']}")
        return "\n".join(lines)

    def semantic_segment(self, sentences: list, visual_results: list = None) -> list:
        if not sentences:
            return []

        transcript = self._build_transcript_text(sentences)
        total_duration = sentences[-1]["end"] if sentences else 0

        visual_section = ""
        if visual_results:
            visual_text = self._build_visual_summary(visual_results)
            visual_section = f"""

## 视觉分析（每隔{self.keyframe_interval}秒的画面描述，标注了[符合目标]的帧）
{visual_text}

请综合对白内容和画面视觉特征进行评分。
重点关注标注了[符合目标]的帧所在的时间区间，这些区间应获得更高评分。
没有[符合目标]帧的时间区间应给予低分。
"""

        prompt = f"""你是一个影视内容分析专家。下面是一段视频的转录文本和视觉分析结果。每行开头的 [Xs] 是该句在视频中的原始时间戳（秒）。

请完成两件事：

1. **语义分块**：把内容划分为若干个语义完整的片段（话题/场景转换处即为分割点）。
   - 每个片段至少 5 秒，不超过 10 分钟
   - start/end 必须来自文本中已出现的时间戳，不能自行编造

2. **评分筛选**：对每个片段打分（0-10分），评分标准：
   - 内容方向：{self.interest}
   - 10分 = 极度符合，特征明显
   - 6分 = 较为符合，值得保留
   - 3分以下 = 不符合，不保留

只返回 JSON 数组，不要有任何其他文字。格式如下：
[
  {{
    "start": 12.5,
    "end": 187.0,
    "summary": "描述这个片段的内容和视觉特征",
    "score": 8,
    "keep": true
  }},
  ...
]

## 转录文本（总时长约 {total_duration:.0f}s）
{transcript}
{visual_section}"""

        client = OpenAI(
            api_key=self.text_api_key,
            base_url=self.text_base_url or None,
        )
        resp = client.chat.completions.create(
            model=self.text_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        segments = json.loads(raw)

        for seg in segments:
            seg["keep"] = seg.get("score", 0) >= self.min_score

        return segments

    # ──────────────────────────────────────────────────────
    #  Step 4+5: Cut and concat
    # ──────────────────────────────────────────────────────

    def cut_and_concat(self, segments: list, output_path: str = None):
        if output_path is None:
            output_path = str(Path(self.output_dir) / "highlights.mp4")

        kept = [s for s in segments if s.get("keep")]
        if not kept:
            return

        out_dir = Path(output_path).parent
        tmp_clips = []

        for i, seg in enumerate(sorted(kept, key=lambda x: x["start"])):
            clip_path = out_dir / f"_clip_{i:03d}.mp4"
            duration = seg["end"] - seg["start"]
            self.progress(
                4, "cutting", "running",
                f"裁剪片段 {i + 1}/{len(kept)}: {seg['start']:.1f}s ~ {seg['end']:.1f}s",
                progress=int((i + 1) / len(kept) * 100),
            )
            subprocess.run(
                [
                    FFMPEG, "-y",
                    "-ss", str(seg["start"]),
                    "-t", str(duration),
                    "-i", self.video_path,
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k",
                    str(clip_path),
                ],
                capture_output=True, encoding="utf-8", errors="replace",
            )
            tmp_clips.append(clip_path)

        self.progress(4, "cutting", "running", "拼接中...", progress=100)
        concat_list = out_dir / "_concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in tmp_clips),
            encoding="utf-8",
        )
        subprocess.run(
            [
                FFMPEG, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                output_path,
            ],
            capture_output=True, encoding="utf-8", errors="replace",
        )

        # Cleanup temp files
        for p in tmp_clips:
            p.unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)

    # ──────────────────────────────────────────────────────
    #  Re-export: only cut+concat with updated segments
    # ──────────────────────────────────────────────────────

    async def reexport(self, segments: list):
        """Re-run only the cut+concat step with updated segment keep flags."""
        # Save updated segments
        report_path = Path(self.output_dir) / "segments_report.json"
        report_path.write_text(
            json.dumps(segments, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        kept = [s for s in segments if s.get("keep")]
        self.progress(4, "cutting", "running", f"裁剪拼接中（{len(kept)} 个片段）...")
        output_video = str(Path(self.output_dir) / "highlights.mp4")
        await asyncio.to_thread(self.cut_and_concat, segments, output_video)

        total_kept_sec = sum(s["end"] - s["start"] for s in kept)
        self.progress(
            5, "done", "done",
            f"重新导出完成！精华时长 {total_kept_sec:.0f}s（{total_kept_sec / 60:.1f} 分钟）",
            data={"highlights_duration": total_kept_sec},
        )
