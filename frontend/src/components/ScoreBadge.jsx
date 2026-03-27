import React from 'react';

function getScoreColor(score) {
  if (score <= 2) return '#EF4444';
  if (score <= 4) return '#EA580C';
  if (score <= 6) return '#F59E0B';
  if (score <= 8) return '#10B981';
  return '#059669';
}

export default function ScoreBadge({ score, size = 36 }) {
  const color = getScoreColor(score);
  const fontSize = size * 0.4;

  return (
    <div
      className="score-badge"
      style={{
        width: size,
        height: size,
        minWidth: size,
        borderRadius: '50%',
        border: `3px solid ${color}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 700,
        fontSize: `${fontSize}px`,
        color: color,
        backgroundColor: `${color}14`,
      }}
    >
      {score}
    </div>
  );
}
