import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Check, Loader, Clock, Download, RefreshCw,
  ChevronDown, ChevronRight, AlertCircle, Play, Film,
  Trash2, Square, RotateCcw, PlayCircle,
} from 'lucide-react';
import { useAPI } from '../hooks/useAPI.js';
import { useToast } from '../components/Toast.jsx';
import ScoreBadge from '../components/ScoreBadge.jsx';
import Timeline from '../components/Timeline.jsx';

const STEPS = [
  { key: 'silence_detection', label: '静音检测' },
  { key: 'asr', label: 'ASR 转录' },
  { key: 'visual', label: '视觉分析' },
  { key: 'llm', label: 'LLM 语义分块' },
  { key: 'cutting', label: '裁剪拼接' },
];

function formatTime(seconds) {
  if (seconds == null) return '--:--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatElapsed(ms) {
  if (ms == null) return '';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}秒`;
  return `${Math.floor(sec / 60)}分${sec % 60}秒`;
}

export default function TaskDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const api = useAPI();
  const toast = useToast();
  const videoRef = useRef(null);

  const [status, setStatus] = useState(null);
  const [segments, setSegments] = useState([]);
  const [transcript, setTranscript] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [steps, setSteps] = useState([]);
  const [completed, setCompleted] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [exporting, setExporting] = useState(false);

  // SSE connection
  const sseRef = useRef(null);
  const reconnectTimer = useRef(null);
  const completedRef = useRef(false);

  const connectSSE = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
    }
    const url = api.getProgressURL(id);
    const es = new EventSource(url);
    sseRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Update step progress — match by backend "name" field
        if (data.name && data.name !== 'done' && data.name !== 'error' && data.name !== 'cancelled') {
          setSteps((prev) => {
            const idx = prev.findIndex((s) => s.key === data.name);
            if (idx >= 0) {
              const updated = [...prev];
              updated[idx] = { ...updated[idx], status: data.status, message: data.message, progress: data.progress };
              return updated;
            }
            return [...prev, { key: data.name, status: data.status, message: data.message, progress: data.progress }];
          });
        }

        // Pipeline completed
        if (data.name === 'done' && data.status === 'done') {
          completedRef.current = true;
          setCompleted(true);
          setStatus((prev) => prev ? { ...prev, status: 'completed' } : prev);
          es.close();
          loadResults();
        }
        // Pipeline cancelled
        if (data.name === 'cancelled') {
          completedRef.current = true;
          setStatus((prev) => prev ? { ...prev, status: 'cancelled' } : prev);
          es.close();
        }
        // Pipeline error
        if (data.name === 'error' || data.status === 'error') {
          completedRef.current = true;
          setError(data.message || '处理出错');
          setStatus((prev) => prev ? { ...prev, status: 'error' } : prev);
          es.close();
        }
      } catch { /* ignore parse errors */ }
    };

    es.onerror = () => {
      es.close();
      // Auto-reconnect after 3s if still active
      if (!completedRef.current) {
        reconnectTimer.current = setTimeout(() => {
          connectSSE();
        }, 3000);
      }
    };
  }, [id, api]);

  const loadResults = useCallback(async () => {
    try {
      const [segs, trans] = await Promise.all([
        api.getSegments(id).catch(() => []),
        api.getTranscript(id).catch(() => []),
      ]);
      setSegments(Array.isArray(segs) ? segs : segs.segments || []);
      setTranscript(Array.isArray(trans) ? trans : trans.transcript || []);
    } catch { /* ignore */ }
  }, [api, id]);

  useEffect(() => {
    let cancelled = false;
    api.getTaskStatus(id)
      .then((data) => {
        if (cancelled) return;
        setStatus(data);
        setLoading(false);
        // Populate step progress from saved steps
        if (data.steps && data.steps.length > 0) {
          setSteps(data.steps.filter((s) => s.name !== 'done' && s.name !== 'error' && s.name !== 'cancelled')
            .map((s) => ({ key: s.name, status: s.status, message: s.message, progress: s.progress })));
        }
        if (data.status === 'completed') {
          setCompleted(true);
          loadResults();
        } else if (data.status === 'processing' || data.status === 'pending') {
          connectSSE();
        } else if (data.status === 'cancelled') {
          // Show cancelled state — user can resume or restart
        } else if (data.status === 'error') {
          const errStep = (data.steps || []).find((s) => s.status === 'error');
          setError(errStep?.message || data.error?.split('\n')[0] || '任务出错');
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      if (sseRef.current) sseRef.current.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [id]);

  const seekTo = useCallback((time) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      videoRef.current.play().catch(() => {});
    }
  }, []);

  const toggleSegment = (idx) => {
    setSegments((prev) => {
      const updated = [...prev];
      updated[idx] = { ...updated[idx], keep: !updated[idx].keep };
      return updated;
    });
  };

  const handleReExport = async () => {
    setExporting(true);
    try {
      await api.updateSegments(id, segments);
      toast.success('重新导出已启动');
      setCompleted(false);
      connectSSE();
    } catch (err) {
      toast.error('导出失败: ' + err.message);
    } finally {
      setExporting(false);
    }
  };

  const handleDownload = () => {
    api.downloadHighlights(id);
  };

  const handleCancel = async () => {
    try {
      await api.cancelTask(id);
      toast.success('正在取消...');
    } catch (err) {
      toast.error('取消失败: ' + err.message);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm('确定要删除此任务？视频文件和所有处理结果将被永久删除。')) return;
    try {
      await api.deleteTask(id);
      toast.success('任务已删除');
      navigate('/');
    } catch (err) {
      toast.error('删除失败: ' + err.message);
    }
  };

  const handleRestart = async () => {
    try {
      await api.startTask(id, status?.interest || '', status?.min_score || 6, false);
      toast.success('重新处理已启动');
      setCompleted(false);
      setError(null);
      setSteps([]);
      setSegments([]);
      connectSSE();
    } catch (err) {
      toast.error('启动失败: ' + err.message);
    }
  };

  const handleResume = async () => {
    try {
      await api.startTask(id, status?.interest || '', status?.min_score || 6, true);
      toast.success('继续处理已启动');
      setCompleted(false);
      setError(null);
      setSteps([]);
      connectSSE();
    } catch (err) {
      toast.error('启动失败: ' + err.message);
    }
  };

  if (loading) {
    return (
      <div className="page-center">
        <Loader size={32} className="spin" />
        <p>加载中...</p>
      </div>
    );
  }

  // Don't return early on error — show the page with action buttons

  const totalDuration = status?.original_duration || status?.duration || 0;
  const videoSrc = status?.task_id && status?.filename
    ? `/uploads/${status.task_id}/${encodeURIComponent(status.filename)}`
    : null;

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">
          <Film size={24} />
          {status?.filename || '任务详情'}
        </h1>
        <div className="page-header-actions">
          {status?.status === 'processing' && (
            <button className="btn btn-secondary" onClick={handleCancel}>
              <Square size={16} />
              取消
            </button>
          )}
          {(status?.status === 'cancelled' || status?.status === 'error') && (
            <>
              <button className="btn btn-secondary" onClick={handleResume}>
                <PlayCircle size={16} />
                继续处理
              </button>
              <button className="btn btn-primary" onClick={handleRestart}>
                <RotateCcw size={16} />
                重新处理
              </button>
            </>
          )}
          <button className="btn-icon btn-icon-danger" title="删除任务" onClick={handleDelete}>
            <Trash2 size={18} />
          </button>
        </div>
      </div>

      {/* Section A: Progress */}
      {!completed && (
        <div className="card">
          <h2 className="section-title">处理进度</h2>
          <div className="progress-steps">
            {STEPS.map((stepDef, i) => {
              const stepData = steps.find((s) => s.key === stepDef.key) || {};
              const stepStatus = stepData.status || 'waiting';
              return (
                <div key={stepDef.key} className={`progress-step progress-step-${stepStatus}`}>
                  <div className="step-indicator">
                    <div className={`step-icon step-icon-${stepStatus}`}>
                      {(stepStatus === 'done' || stepStatus === 'completed') && <Check size={16} />}
                      {(stepStatus === 'running' || stepStatus === 'processing') && <Loader size={16} className="spin" />}
                      {(stepStatus === 'waiting' || stepStatus === 'pending') && <span className="step-dot" />}
                      {stepStatus === 'error' && <AlertCircle size={16} />}
                    </div>
                    {i < STEPS.length - 1 && <div className={`step-line step-line-${stepStatus}`} />}
                  </div>
                  <div className="step-content">
                    <div className="step-name">{stepDef.label}</div>
                    {stepData.message && <div className="step-message">{stepData.message}</div>}
                    {stepData.elapsed != null && (
                      <div className="step-elapsed">{formatElapsed(stepData.elapsed)}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Status banners */}
      {completed && (
        <div className="completion-banner">
          <Check size={20} />
          <span>处理完成！</span>
        </div>
      )}

      {status?.status === 'cancelled' && !completed && (
        <div className="completion-banner" style={{ background: '#FEF3C7', color: '#92400E' }}>
          <Square size={20} />
          <span>任务已取消，可以继续处理或重新开始</span>
        </div>
      )}

      {error && !completed && status?.status !== 'cancelled' && (
        <div className="completion-banner" style={{ background: '#FEE2E2', color: '#991B1B' }}>
          <AlertCircle size={20} />
          <span>处理出错：{error}</span>
        </div>
      )}

      {/* Section B: Results */}
      {completed && (
        <div className="results-layout">
          <div className="results-left">
            <div className="card">
              <h2 className="section-title">视频预览</h2>
              {videoSrc ? (
                <video ref={videoRef} src={videoSrc} controls className="video-player" />
              ) : (
                <div className="video-placeholder">
                  <Play size={48} className="text-muted" />
                  <p>无法加载视频预览</p>
                </div>
              )}
              <div className="timeline-wrapper">
                <Timeline segments={segments} totalDuration={totalDuration} onSeek={seekTo} />
              </div>
            </div>
          </div>
          <div className="results-right">
            <div className="card">
              <div className="segment-list-header">
                <h2 className="section-title">片段列表</h2>
                <span className="segment-count">
                  {segments.filter((s) => s.keep !== false).length}/{segments.length} 保留
                </span>
              </div>
              <div className="segment-list">
                {segments.map((seg, i) => (
                  <div
                    key={i}
                    className={`segment-card ${seg.keep === false ? 'segment-card-discarded' : ''}`}
                  >
                    <div className="segment-main" onClick={() => seekTo(seg.start)}>
                      <ScoreBadge score={seg.score ?? 0} size={36} />
                      <div className="segment-info">
                        <div className="segment-time">
                          {formatTime(seg.start)} - {formatTime(seg.end)}
                        </div>
                        <div className="segment-summary">{seg.summary || seg.text || ''}</div>
                      </div>
                    </div>
                    <label className="toggle-switch">
                      <input
                        type="checkbox"
                        checked={seg.keep !== false}
                        onChange={() => toggleSegment(i)}
                      />
                      <span className="toggle-slider" />
                    </label>
                  </div>
                ))}
              </div>
              <div className="segment-actions">
                <button
                  className="btn btn-secondary"
                  disabled={exporting}
                  onClick={handleReExport}
                >
                  <RefreshCw size={16} className={exporting ? 'spin' : ''} />
                  重新导出
                </button>
                <button className="btn btn-primary" onClick={handleDownload}>
                  <Download size={16} />
                  下载精华视频
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Section C: Transcript */}
      {completed && transcript.length > 0 && (
        <div className="card" style={{ marginTop: 20 }}>
          <button
            className="transcript-toggle"
            onClick={() => setShowTranscript(!showTranscript)}
          >
            {showTranscript ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
            <h2 className="section-title" style={{ margin: 0 }}>完整转录</h2>
          </button>
          {showTranscript && (
            <div className="transcript-list">
              {transcript.map((item, i) => {
                const inKept = segments.some(
                  (s) => s.keep !== false && item.start >= s.start && item.start < s.end
                );
                return (
                  <div
                    key={i}
                    className={`transcript-item ${inKept ? 'transcript-item-kept' : ''}`}
                    onClick={() => seekTo(item.start)}
                  >
                    <span className="transcript-time">{formatTime(item.start)}</span>
                    <span className="transcript-text">{item.text}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
