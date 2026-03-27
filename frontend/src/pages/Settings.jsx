import React, { useEffect, useState } from 'react';
import { Eye, EyeOff, Loader } from 'lucide-react';
import { useAPI } from '../hooks/useAPI.js';
import { useToast } from '../components/Toast.jsx';

export default function Settings() {
  const api = useAPI();
  const toast = useToast();
  const [settings, setSettings] = useState({
    asr_provider: 'openai',
    asr_app_id: '',
    asr_access_key: '',
    asr_api_key: '',
    asr_base_url: '',
    asr_model: 'whisper-1',
    text_api_key: '',
    text_base_url: '',
    text_model: '',
    vision_api_key: '',
    vision_base_url: '',
    vision_model: '',
    silence_db: -40,
    silence_min_dur: 2.0,
    active_min_dur: 3.0,
    keyframe_interval: 2,
    default_min_score: 5,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showKeys, setShowKeys] = useState({});

  useEffect(() => {
    api.getSettings()
      .then((data) => {
        setSettings((prev) => ({ ...prev, ...data }));
        setLoading(false);
      })
      .catch((err) => {
        toast.error('加载设置失败: ' + err.message);
        setLoading(false);
      });
  }, []);

  const update = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const toggleShow = (key) => {
    setShowKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.saveSettings(settings);
      toast.success('设置已保存');
    } catch (err) {
      toast.error('保存失败: ' + err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="page-center">
        <Loader size={32} className="spin" />
        <p>加载设置...</p>
      </div>
    );
  }

  return (
    <div className="page">
      <h1 className="page-title">设置</h1>

      {/* Group 1: ASR Config */}
      <div className="card settings-group">
        <h2 className="settings-group-title">语音识别 (ASR) 配置</h2>
        <p className="settings-group-desc">选择语音识别服务商</p>

        <div className="form-group">
          <label className="form-label">ASR 服务商</label>
          <select
            className="form-select"
            value={settings.asr_provider}
            onChange={(e) => update('asr_provider', e.target.value)}
          >
            <option value="openai">OpenAI 兼容接口 (通用)</option>
            <option value="volcengine">火山引擎</option>
          </select>
        </div>

        {settings.asr_provider === 'volcengine' ? (
          <>
            <div className="form-group">
              <label className="form-label">ASR App ID</label>
              <input
                className="form-input"
                value={settings.asr_app_id}
                onChange={(e) => update('asr_app_id', e.target.value)}
                placeholder="请输入 ASR App ID"
              />
            </div>
            <div className="form-group">
              <label className="form-label">ASR Access Key</label>
              <div className="input-with-toggle">
                <input
                  className="form-input"
                  type={showKeys.asr_access_key ? 'text' : 'password'}
                  value={settings.asr_access_key}
                  onChange={(e) => update('asr_access_key', e.target.value)}
                  placeholder="请输入 ASR Access Key"
                />
                <button className="input-toggle-btn" onClick={() => toggleShow('asr_access_key')}>
                  {showKeys.asr_access_key ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="form-group">
              <label className="form-label">API Key</label>
              <div className="input-with-toggle">
                <input
                  className="form-input"
                  type={showKeys.asr_api_key ? 'text' : 'password'}
                  value={settings.asr_api_key}
                  onChange={(e) => update('asr_api_key', e.target.value)}
                  placeholder="请输入 API Key"
                />
                <button className="input-toggle-btn" onClick={() => toggleShow('asr_api_key')}>
                  {showKeys.asr_api_key ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Base URL</label>
              <input
                className="form-input"
                value={settings.asr_base_url}
                onChange={(e) => update('asr_base_url', e.target.value)}
                placeholder="例如: https://api.openai.com/v1 (留空则使用 OpenAI 默认)"
              />
            </div>
            <div className="form-group">
              <label className="form-label">模型名称</label>
              <input
                className="form-input"
                list="asr-models"
                value={settings.asr_model}
                onChange={(e) => update('asr_model', e.target.value)}
                placeholder="选择或输入模型名称"
              />
              <datalist id="asr-models">
                <option value="whisper-1" />
                <option value="whisper-large-v3" />
                <option value="whisper-large-v3-turbo" />
              </datalist>
            </div>
          </>
        )}
      </div>

      {/* Group 2: Text Model */}
      <div className="card settings-group">
        <h2 className="settings-group-title">文本模型配置</h2>
        <p className="settings-group-desc">用于语义分析和评分，支持 OpenAI 兼容接口（豆包、OpenAI、DeepSeek、通义千问等）</p>

        <div className="form-group">
          <label className="form-label">API Key</label>
          <div className="input-with-toggle">
            <input
              className="form-input"
              type={showKeys.text_api_key ? 'text' : 'password'}
              value={settings.text_api_key}
              onChange={(e) => update('text_api_key', e.target.value)}
              placeholder="请输入 API Key"
            />
            <button className="input-toggle-btn" onClick={() => toggleShow('text_api_key')}>
              {showKeys.text_api_key ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">Base URL</label>
          <input
            className="form-input"
            value={settings.text_base_url}
            onChange={(e) => update('text_base_url', e.target.value)}
            placeholder="例如: https://api.openai.com/v1 (留空则使用 OpenAI 默认)"
          />
        </div>

        <div className="form-group">
          <label className="form-label">模型名称</label>
          <input
            className="form-input"
            list="text-models"
            value={settings.text_model}
            onChange={(e) => update('text_model', e.target.value)}
            placeholder="选择或输入模型名称"
          />
          <datalist id="text-models">
            <option value="gpt-4o" />
            <option value="gpt-4o-mini" />
            <option value="deepseek-chat" />
            <option value="deepseek-reasoner" />
            <option value="qwen-max" />
            <option value="qwen-plus" />
            <option value="doubao-seed-2-0-pro-260215" />
            <option value="doubao-pro-32k" />
            <option value="glm-4-plus" />
          </datalist>
        </div>
      </div>

      {/* Group 3: Vision Model */}
      <div className="card settings-group">
        <h2 className="settings-group-title">视觉模型配置</h2>
        <p className="settings-group-desc">用于关键帧画面分析，支持 OpenAI 兼容接口，可与文本模型使用不同厂商</p>

        <div className="form-group">
          <label className="form-label">API Key</label>
          <div className="input-with-toggle">
            <input
              className="form-input"
              type={showKeys.vision_api_key ? 'text' : 'password'}
              value={settings.vision_api_key}
              onChange={(e) => update('vision_api_key', e.target.value)}
              placeholder="请输入 API Key"
            />
            <button className="input-toggle-btn" onClick={() => toggleShow('vision_api_key')}>
              {showKeys.vision_api_key ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">Base URL</label>
          <input
            className="form-input"
            value={settings.vision_base_url}
            onChange={(e) => update('vision_base_url', e.target.value)}
            placeholder="例如: https://api.openai.com/v1 (留空则使用 OpenAI 默认)"
          />
        </div>

        <div className="form-group">
          <label className="form-label">模型名称</label>
          <input
            className="form-input"
            list="vision-models"
            value={settings.vision_model}
            onChange={(e) => update('vision_model', e.target.value)}
            placeholder="选择或输入模型名称"
          />
          <datalist id="vision-models">
            <option value="gpt-4o" />
            <option value="gpt-4o-mini" />
            <option value="qwen-vl-max" />
            <option value="qwen-vl-plus" />
            <option value="doubao-seed-1-6-vision-250815" />
            <option value="doubao-vision-pro-32k" />
            <option value="glm-4v-plus" />
          </datalist>
        </div>
      </div>

      {/* Group 3: Defaults */}
      <div className="card settings-group">
        <h2 className="settings-group-title">默认参数</h2>

        <div className="form-group">
          <label className="form-label">
            静音阈值
            <span className="form-value">{settings.silence_db} dB</span>
          </label>
          <input
            type="range"
            className="form-range"
            min={-60}
            max={-20}
            step={1}
            value={settings.silence_db}
            onChange={(e) => update('silence_db', Number(e.target.value))}
          />
          <div className="range-labels">
            <span>-60 dB</span>
            <span>-20 dB</span>
          </div>
        </div>

        <div className="form-row">
          <div className="form-group">
            <label className="form-label">最短静音时长 (秒)</label>
            <input
              className="form-input"
              type="number"
              min={0.1}
              max={10}
              step={0.1}
              value={settings.silence_min_dur}
              onChange={(e) => update('silence_min_dur', Number(e.target.value))}
            />
          </div>
          <div className="form-group">
            <label className="form-label">最短有声段 (秒)</label>
            <input
              className="form-input"
              type="number"
              min={0.1}
              max={30}
              step={0.1}
              value={settings.active_min_dur}
              onChange={(e) => update('active_min_dur', Number(e.target.value))}
            />
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">关键帧间隔 (秒)</label>
          <select
            className="form-select"
            value={settings.keyframe_interval}
            onChange={(e) => update('keyframe_interval', Number(e.target.value))}
          >
            <option value={1}>1 秒</option>
            <option value={2}>2 秒</option>
            <option value={3}>3 秒</option>
            <option value={5}>5 秒</option>
          </select>
        </div>

        <div className="form-group">
          <label className="form-label">
            默认评分阈值
            <span className="form-value">{settings.default_min_score}</span>
          </label>
          <input
            type="range"
            className="form-range"
            min={1}
            max={10}
            step={1}
            value={settings.default_min_score}
            onChange={(e) => update('default_min_score', Number(e.target.value))}
          />
          <div className="range-labels">
            <span>1 (宽松)</span>
            <span>10 (严格)</span>
          </div>
        </div>
      </div>

      <div className="form-actions" style={{ marginTop: 20 }}>
        <button
          className="btn btn-primary btn-lg"
          disabled={saving}
          onClick={handleSave}
        >
          {saving ? (
            <>
              <span className="btn-spinner" />
              保存中...
            </>
          ) : (
            '保存设置'
          )}
        </button>
      </div>
    </div>
  );
}
