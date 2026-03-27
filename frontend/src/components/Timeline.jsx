import React from 'react';

export default function Timeline({ segments, totalDuration, onSeek }) {
  if (!segments || !totalDuration) return null;

  return (
    <div className="timeline-bar" onClick={(e) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = (e.clientX - rect.left) / rect.width;
      const time = ratio * totalDuration;
      if (onSeek) onSeek(time);
    }}>
      {segments.map((seg, i) => {
        const left = (seg.start / totalDuration) * 100;
        const width = ((seg.end - seg.start) / totalDuration) * 100;
        const kept = seg.keep !== false;
        return (
          <div
            key={i}
            className={`timeline-segment ${kept ? 'timeline-segment-keep' : 'timeline-segment-discard'}`}
            style={{
              left: `${left}%`,
              width: `${Math.max(width, 0.3)}%`,
            }}
            title={`${formatTime(seg.start)} - ${formatTime(seg.end)} | 评分: ${seg.score ?? '-'}`}
            onClick={(e) => {
              e.stopPropagation();
              if (onSeek) onSeek(seg.start);
            }}
          />
        );
      })}
    </div>
  );
}

function formatTime(seconds) {
  if (seconds == null) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}
