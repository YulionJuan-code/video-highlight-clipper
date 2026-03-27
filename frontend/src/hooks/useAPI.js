const API_BASE = window.location.origin;

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, options);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      msg = body.detail || body.message || msg;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res;
}

async function json(path, options = {}) {
  const res = await request(path, options);
  return res.json();
}

export function useAPI() {
  const getSettings = () => json('/api/settings');

  const saveSettings = (settings) =>
    json('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });

  const uploadVideo = (file, onProgress) => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API_BASE}/api/upload`);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            resolve(xhr.responseText);
          }
        } else {
          let msg = `HTTP ${xhr.status}`;
          try {
            const body = JSON.parse(xhr.responseText);
            msg = body.detail || body.message || msg;
          } catch { /* ignore */ }
          reject(new Error(msg));
        }
      };

      xhr.onerror = () => reject(new Error('Network error'));

      const formData = new FormData();
      formData.append('file', file);
      xhr.send(formData);
    });
  };

  const startTask = (taskId, interestDescription, minScore, resume = false, keyframeInterval = 0) =>
    json(`/api/tasks/${taskId}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        interest_description: interestDescription,
        min_score: minScore,
        keyframe_interval: keyframeInterval,
        resume,
      }),
    });

  const deleteTask = (taskId) =>
    json(`/api/tasks/${taskId}`, { method: 'DELETE' });

  const cancelTask = (taskId) =>
    json(`/api/tasks/${taskId}/cancel`, { method: 'POST' });

  const getTaskStatus = (taskId) => json(`/api/tasks/${taskId}/status`);

  const getSegments = (taskId) => json(`/api/tasks/${taskId}/segments`);

  const getTranscript = (taskId) => json(`/api/tasks/${taskId}/transcript`);

  const updateSegments = (taskId, segments) =>
    json(`/api/tasks/${taskId}/segments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(segments),
    });

  const downloadHighlights = (taskId) => {
    window.open(`${API_BASE}/api/tasks/${taskId}/download`, '_blank');
  };

  const listTasks = () => json('/api/tasks');

  const getProgressURL = (taskId) => `${API_BASE}/api/tasks/${taskId}/progress`;

  const getFrameURL = (taskId, name) => `${API_BASE}/api/tasks/${taskId}/frames/${name}`;

  const getDownloadURL = (taskId) => `${API_BASE}/api/tasks/${taskId}/download`;

  return {
    getSettings,
    saveSettings,
    uploadVideo,
    startTask,
    deleteTask,
    cancelTask,
    getTaskStatus,
    getSegments,
    getTranscript,
    updateSegments,
    downloadHighlights,
    listTasks,
    getProgressURL,
    getFrameURL,
    getDownloadURL,
  };
}
