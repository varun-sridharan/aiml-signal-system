import React, { useRef } from 'react';
import { Pill } from '../labels/Pill';

/** An ARIA tablist. variant "primary" for NEWS/PREDICTION/HOMEWORK/ARCHIVE, "sub" for the sticky NEWS sub-tabs. */
export function Tabs({ items = [], value, onChange, variant = 'primary', label = 'Sections', trailing }) {
  const refs = useRef([]);
  const sub = variant === 'sub';
  const keys = (e, i) => {
    const n = items.length;
    const go = j => { const k = (j + n) % n; refs.current[k] && refs.current[k].focus(); onChange && onChange(items[k].id); };
    if (e.key === 'ArrowRight') { e.preventDefault(); go(i + 1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(i - 1); }
    if (e.key === 'Home') { e.preventDefault(); go(0); }
    if (e.key === 'End') { e.preventDefault(); go(n - 1); }
  };
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, borderBottom: '1px solid var(--rule-strong)', overflowX: 'auto' }}>
      <div role="tablist" aria-label={label} style={{ display: 'flex', gap: sub ? 22 : 28, flex: 1 }}>
        {items.map((t, i) => {
          const on = t.id === value;
          const colour = t.color || 'var(--accent)';
          return (
            <button key={t.id} ref={el => (refs.current[i] = el)} role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
              onClick={() => onChange && onChange(t.id)} onKeyDown={e => keys(e, i)}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 8, background: 'none', border: 0, borderBottom: '2px solid ' + (on ? colour : 'transparent'), marginBottom: -1, padding: sub ? '10px 0 9px' : '12px 0 11px', fontFamily: 'var(--mono)', fontSize: sub ? 10.5 : 11.5, letterSpacing: 'var(--track-label)', textTransform: 'uppercase', fontWeight: on ? 600 : 500, color: on ? (sub ? colour : 'var(--ink)') : 'var(--soft)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
              {sub && <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%', background: colour, flex: 'none' }} />}
              <span>{t.label}</span>
              {t.count != null && <Pill count={t.count} />}
            </button>
          );
        })}
      </div>
      {trailing}
    </div>
  );
}
