import React from 'react';

/** The five-item site nav. Exactly one item is active: the one whose label equals current. */
export function Nav({ items = [], current }) {
  return (
    <nav aria-label="Site" style={{ display: 'flex', alignItems: 'center', gap: '10px 22px', flexWrap: 'wrap', fontFamily: 'var(--sans)', fontSize: 'var(--fs-small)' }}>
      {items.map(it => it.label === current
        ? <span key={it.label} aria-current="page" style={{ color: 'var(--accent)', fontWeight: 600, borderBottom: '1.5px solid var(--accent)', paddingBottom: 2 }}>{it.label}</span>
        : <a key={it.label} href={it.href} style={{ color: 'var(--soft)', textDecoration: 'none', borderBottom: 0 }}>{it.label}</a>)}
    </nav>
  );
}
