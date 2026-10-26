import React from 'react';

const TONES = {
  'DO THIS':     { color: 'var(--accent)', border: 'var(--accent)', background: 'var(--accent-soft)' },
  'KNOW':        { color: 'var(--soft)', border: 'var(--rule-strong)', background: 'transparent' },
  'RADAR':       { color: 'var(--active)', border: 'var(--active)', background: 'transparent' },
  'OPEN':        { color: 'var(--active)', border: 'var(--active)', background: 'var(--active-soft)' },
  'IN PROGRESS': { color: 'var(--active)', border: 'var(--active)', background: 'var(--active-soft)' },
  'HIT':         { color: 'var(--ok)', border: 'var(--ok)', background: 'var(--ok-soft)' },
  'DONE':        { color: 'var(--ok)', border: '#C4D6CB', background: '#EAF1EC' },
  'MISS':        { color: 'var(--miss)', border: 'var(--miss)', background: 'var(--miss-soft)' },
  'TO DO':       { color: 'var(--muted)', border: 'var(--rule)', background: 'transparent' },
  'POST':        { color: 'var(--soft)', border: 'var(--rule-strong)', background: 'transparent' },
  'EXCERPT':     { color: 'var(--soft)', border: 'var(--rule-strong)', background: 'var(--inset)' }
};

/** A small mono label. Tone follows the label; pass count for the outlined tab-count variant. */
export function Pill({ label, count, tone }) {
  if (count != null) {
    return <span style={{ fontFamily: 'var(--mono)', fontSize: 10.5, letterSpacing: 'var(--track-tag)', border: '1px solid var(--rule-strong)', borderRadius: 'var(--radius-pill)', padding: '1px 8px', color: 'var(--soft)' }}>{count}</span>;
  }
  const t = TONES[tone || label] || TONES['KNOW'];
  return (
    <span style={{ display: 'inline-block', fontFamily: 'var(--mono)', fontSize: 10.5, fontWeight: 600, letterSpacing: 'var(--track-label)', textTransform: 'uppercase', color: t.color, border: '1px solid ' + t.border, background: t.background, borderRadius: 'var(--radius-sm)', padding: '2px 8px', whiteSpace: 'nowrap' }}>{label}</span>
  );
}
