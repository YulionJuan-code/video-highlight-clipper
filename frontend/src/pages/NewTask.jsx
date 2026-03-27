import React, { useState, useRef, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Upload, Play, Film, X } from 'lucide-react';
import { useAPI } from '../hooks/useAPI.js';
import { useToast } from '../components/Toast.jsx';

const presets = [
  {
    label: '直播切片',
    text: '提取直播中有趣的、搞笑的、有话题性的高光片段，过滤掉无聊的闲聊和静默段落。',
  },
  {
    label: '角色提取',
    text: '提取视频中特定人物出现的片段，关注他们的对话、表情和互动场景。',
  },
  {
    label: '会议纪要',
    text: '提取会议中的重要讨论、决策点和行动项，过滤寒暄和无关闲聊。',
  },
  {
    label: '回忆片段',
    text: '提取视频中温馨、感动、有纪念意义的片段，如欢笑、拥抱、庆祝等场景。',
  },
  {
    label: '自定义',
    text: '',
  },
];

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

function formatDuration(sec) {
  if (!sec) return '--:--';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function NewTask() {
  const api = useAPI();
  const navigate = useNavigate();
  const toast = useToast();
  const fileInputRef = useRef(null);
  const videoRef = useRef(null);

  // Step 1 state
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadResult, setUploadResult] = useState(null);
  const [videoDuration, setVideoDuration] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  // Step 2 state
  const [description, setDescription] = useState('');
  const [minScore, setMinScore] = useState(5);
  const [keyframeInterval, setKeyframeInterval] = useState(2);
  const [starting, setStarting] = useState(false);
  const [activePreset, setActivePreset] = useState(null);

  const handleFile = useCallback(async (f) => {
    if (!f) return;
    setFile(f);
    setUploading(true);
    setUploadProgress(0);
    try {
      const result = await api.uploadVideo(f, (p) => setUploadProgress(p));
      setUploadResult(result);
      toast.success('上传成功！');
    } catch (err) {
      toast.error('上传失败: ' + err.message);
      setFile(null);
    } finally {
      setUploading(false);
    }
  }, [api, toast]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f && f.type.startsWith('video/')) {
      handleFile(f);
    } else {
      toast.error('请拖入视频文件');
    }
  }, [handleFile, toast]);

  const handleStart = async () => {
    if (!uploadResult) return;
    const taskId = uploadResult.task_id || uploadResult.id;
    if (!taskId) {
      toast.error('无法获取任务 ID');
      return;
    }
    if (!description.trim()) {
      toast.error('请填写筛选方向描述');
      return;
    }
    setStarting(true);
    try {
      await api.startTask(taskId, description, minScore, false, keyframeInterval);
      navigate(`/task/${taskId}`);
    } catch (err) {
      toast.error('启动失败: ' + err.message);
      setStarting(false);
    }
  };

  const videoPreviewURL = useMemo(() => file ? URL.createObjectURL(file) : null, [file]);

  return (
    <div className="page">
      <h1 className="page-title">新建任务</h1>

      {/* Step 1 */}
      <div className="card">
        <div className="step-header">
          <span className="step-number">1</span>
          <h2 className="step-title">上传视频</h2>
        </div>

        {!uploadResult ? (
          <div
            className={`upload-area ${dragOver ? 'upload-area-active' : ''} ${uploading ? 'upload-area-uploading' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => !uploading && fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*"
              style={{ display: 'none' }}
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            {uploading ? (
              <div className="upload-progress-container">
                <div className="upload-progress-circle">
                  <span className="upload-progress-text">{uploadProgress}%</span>
                </div>
                <p className="upload-hint">正在上传 {file?.name}...</p>
                <div className="progress-bar">
                  <div className="progress-bar-fill" style={{ width: `${uploadProgress}%` }} />
                </div>
              </div>
            ) : (
              <>
                <Upload size={48} className="upload-icon" />
                <p className="upload-text">拖拽视频文件到此处，或点击选择</p>
                <p className="upload-hint">支持 MP4, MOV, AVI, MKV 等常见格式</p>
              </>
            )}
          </div>
        ) : (
          <div className="upload-result">
            <div className="video-preview">
              <video
                ref={videoRef}
                src={videoPreviewURL}
                controls
                onLoadedMetadata={() => {
                  if (videoRef.current) {
                    setVideoDuration(videoRef.current.duration);
                  }
                }}
              />
            </div>
            <div className="upload-info">
              <div className="info-row">
                <Film size={16} />
                <span className="info-label">文件名</span>
                <span>{file?.name}</span>
              </div>
              <div className="info-row">
                <span className="info-label">大小</span>
                <span>{file ? formatSize(file.size) : '-'}</span>
              </div>
              <div className="info-row">
                <span className="info-label">时长</span>
                <span>{videoDuration ? formatDuration(videoDuration) : '加载中...'}</span>
              </div>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setFile(null);
                  setUploadResult(null);
                  setVideoDuration(null);
                }}
              >
                <X size={14} /> 重新选择
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Step 2 */}
      {uploadResult && (
        <div className="card" style={{ marginTop: 20 }}>
          <div className="step-header">
            <span className="step-number">2</span>
            <h2 className="step-title">配置提取参数</h2>
          </div>

          <div className="form-group">
            <label className="form-label">筛选方向</label>
            <div className="preset-chips">
              {presets.map((p, i) => (
                <button
                  key={i}
                  className={`chip ${activePreset === i ? 'chip-active' : ''}`}
                  onClick={() => {
                    setActivePreset(i);
                    setDescription(p.text);
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <textarea
              className="form-textarea"
              rows={4}
              placeholder="请描述你希望提取的内容方向，例如：提取直播中有趣的、搞笑的片段..."
              value={description}
              onChange={(e) => {
                setDescription(e.target.value);
                setActivePreset(null);
              }}
            />
          </div>

          <div className="form-group">
            <label className="form-label">
              最低保留评分
              <span className="score-value">{minScore}</span>
            </label>
            <input
              type="range"
              className="form-range"
              min={1}
              max={10}
              step={1}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
            />
            <div className="range-labels">
              <span>1 (宽松)</span>
              <span>10 (严格)</span>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">
              视觉分析精度
              <span className="score-value">每 {keyframeInterval} 秒</span>
            </label>
            <input
              type="range"
              className="form-range"
              min={1}
              max={10}
              step={1}
              value={keyframeInterval}
              onChange={(e) => setKeyframeInterval(Number(e.target.value))}
            />
            <div className="range-labels">
              <span>1秒 (高精度，慢)</span>
              <span>10秒 (低精度，快)</span>
            </div>
          </div>

          <div className="form-actions">
            <button
              className="btn btn-primary btn-lg"
              disabled={starting || !description.trim()}
              onClick={handleStart}
            >
              {starting ? (
                <>
                  <span className="btn-spinner" />
                  正在启动...
                </>
              ) : (
                <>
                  <Play size={18} />
                  开始处理
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
