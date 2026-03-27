# Video Highlight Clipper

Automatically extract highlights from long videos using AI. The pipeline detects speech segments, transcribes audio, analyzes keyframes visually, and uses LLM scoring to select the most relevant clips.

## Features

- Silence detection and active segment extraction (ffmpeg)
- Speech-to-text transcription (OpenAI Whisper compatible or Volcengine ASR)
- Keyframe visual analysis via vision models
- LLM-based semantic segmentation and scoring
- Automatic highlight compilation
- Web UI for managing tasks, reviewing segments, and adjusting scores

## Requirements

- Python 3.10+
- Node.js 18+
- ffmpeg (must be on PATH)

## Quick Start

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
cp settings.example.json settings.json
# Edit settings.json with your API keys
python main.py
```

The backend starts at `http://localhost:8000`.

### 2. Frontend

```bash
cd frontend
npm install
npm run build
```

The built frontend is served automatically by the backend.

For development with hot reload:

```bash
npm run dev
```

### 3. Configure

Open `http://localhost:8000/settings` in your browser to configure:

- **ASR**: Choose between OpenAI Whisper compatible API (universal) or Volcengine
- **Text Model**: Any OpenAI-compatible provider (OpenAI, DeepSeek, Qwen, Doubao, etc.)
- **Vision Model**: Any OpenAI-compatible provider with vision support (can be a different provider than text)

## Project Structure

```
├── backend/
│   ├── main.py              # FastAPI server
│   ├── clipper_service.py    # Video processing pipeline
│   ├── requirements.txt
│   └── settings.example.json
├── frontend/
│   ├── src/
│   │   ├── pages/            # Settings, TaskList, TaskDetail, NewTask
│   │   ├── components/       # Shared UI components
│   │   └── hooks/            # API client hook
│   ├── package.json
│   └── vite.config.js
└── .gitignore
```

## License

MIT
