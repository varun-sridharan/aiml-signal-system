import React from 'react';

/** One to four topic hashtags on one line. Labels for skimming, not links. */
export function Hashtags({ tags = [] }) {
  return (
    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontFamily: 'var(--mono)', fontSize: 11.5, letterSpacing: '.02em', color: 'var(--muted)' }}>
      {tags.slice(0, 4).map(t => <span key={t}>#{String(t).replace(/^#/, '')}</span>)}
    </div>
  );
}
