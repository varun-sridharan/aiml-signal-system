import React, { useState } from 'react';

const UP = 'M3.5 10.5h3.5v10H3.5z M7 10.5 10.6 3.2a1.9 1.9 0 0 1 3.5 1.3l-.8 4.6h5.4a2 2 0 0 1 2 2.4l-1.3 7.2a2 2 0 0 1-2 1.8H7z';

/** Useful / Not useful. Outline by default, filled in the section colour when chosen. A reason box opens only after thumbs down. */
export function Thumbs({ color = 'var(--accent)', value = null, onChange, reasonPlaceholder = 'What made it not useful?' }) {
  const [v, setV] = useState(value);
  const pick = next => { const n = v === next ? null : next; setV(n); onChange && onChange(n); };
  const btn = (kind, label) => {
    const on = v === kind;
    return (
      <button type="button" aria-label={label} aria-pressed={on} onClick={() => pick(kind)}
        style={{ width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', border: '1px solid ' + (on ? color : 'var(--rule-strong)'), borderRadius: 'var(--radius-lg)', cursor: 'pointer', padding: 0 }}>
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill={on ? color : 'none'} stroke={on ? color : 'var(--soft)'} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" style={kind === 'down' ? { transform: 'scaleY(-1)' } : null}>
          <path d={UP} />
        </svg>
      </button>
    );
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8 }}>{btn('up', 'Useful')}{btn('down', 'Not useful')}</div>
      {v === 'down' && (
        <input aria-label="Why it was not useful" placeholder={reasonPlaceholder}
          style={{ font: 'inherit', fontSize: 15, padding: '8px 10px', border: '1px solid var(--rule-strong)', borderRadius: 'var(--radius-md)', background: 'var(--panel)', color: 'var(--ink)', maxWidth: 460 }} />
      )}
    </div>
  );
}
