import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Film, Plus, Clock, AlertCircle, Loader, CheckCircle, Trash2 } from 'lucide-react';
import { useAPI } from '../hooks/useAPI.js';
import { useToast } from '../components/Toast.jsx';

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function formatDuration(sec) {
  if (sec == null) return '--:--';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function statusLabel(status) {
  switch (status) {
    case 'completed': return '已完成';
    case 'processing': return '处理中';
    case 'cancelled': return '已取消';
    case 'error': return '出错';
    case 'pending': return '等待中';
    default: return status || '未知';
  }
}

function StatusIcon({ status }) {
  switch (status) {
    case 'completed': return <CheckCircle size={16} />;
    case 'processing': return <Loader size={16} className="spin" />;
    case 'error': return <AlertCircle size={16} />;
    default: return <Clock size={16} />;
  }
}

export default function TaskList() {
  const api = useAPI();
  const navigate = useNavigate();
  const toast = useToast();
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const handleDelete = async (e, taskId) => {
    e.stopPropagation();
    if (!window.confirm('确定要删除此任务？视频文件和所有处理结果将被永久删除。')) return;
    try {
      await api.deleteTask(taskId);
      setTasks((prev) => prev.filter((t) => (t.task_id || t.id) !== taskId));
      toast.success('任务已删除');
    } catch (err) {
      toast.error('删除失败: ' + err.message);
    }
  };

  useEffect(() => {
    let cancelled = false;
    api.listTasks()
      .then((data) => {
        if (!cancelled) {
          setTasks(Array.isArray(data) ? data : data.tasks || []);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="page-center">
        <Loader size={32} className="spin" />
        <p>正在加载...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-center">
        <AlertCircle size={32} className="text-error" />
        <p>加载失败: {error}</p>
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div className="page-center">
        <Film size={64} className="text-muted" />
        <h2 className="empty-title">还没有任何任务</h2>
        <p className="text-muted">开始创建第一个吧！</p>
        <button className="btn btn-primary" style={{ marginTop: 16 }} onClick={() => navigate('/new')}>
          <Plus size={18} />
          新建任务
        </button>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">项目列表</h1>
        <button className="btn btn-primary" onClick={() => navigate('/new')}>
          <Plus size={18} />
          新建任务
        </button>
      </div>
      <div className="task-grid">
        {tasks.map((task) => (
          <div
            key={task.task_id || task.id}
            className="card task-card"
            onClick={() => navigate(`/task/${task.task_id || task.id}`)}
          >
            <div className="task-card-header">
              <Film size={20} className="text-primary" />
              <div className="task-card-actions">
                <span className={`status-badge status-${task.status}`}>
                  <StatusIcon status={task.status} />
                  {statusLabel(task.status)}
                </span>
                <button
                  className="btn-icon btn-icon-danger"
                  title="删除任务"
                  onClick={(e) => handleDelete(e, task.task_id || task.id)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            <h3 className="task-card-title">{task.filename || '未命名视频'}</h3>
            <div className="task-card-meta">
              <div className="meta-row">
                <Clock size={14} />
                <span>{formatDate(task.created_at)}</span>
              </div>
              {task.original_duration != null && (
                <div className="meta-row">
                  <span className="meta-label">时长</span>
                  <span>
                    {formatDuration(task.original_duration)}
                    {task.highlight_duration != null && (
                      <> → {formatDuration(task.highlight_duration)}</>
                    )}
                  </span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
