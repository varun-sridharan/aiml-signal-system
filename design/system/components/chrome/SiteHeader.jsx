import React from 'react';
import { Nav } from './Nav';

/** The shared site header: one wordmark lockup, then the nav. Identical on every page. */
export function SiteHeader({ wordmark = 'AI/ML Signal', wordmarkAccent = '.', href = 'Product.html', items = [], current, sticky = true }) {
  return (
    <header style={{ position: sticky ? 'sticky' : 'static', top: 0, zIndex: 20, background: 'rgba(244,241,232,.93)', backdropFilter: 'blur(8px)', borderBottom: '1px solid var(--rule)' }}>
      <div style={{ maxWidth: 'var(--w-site)', margin: '0 auto', padding: '14px 48px', display: 'flex', alignItems: 'center', gap: '10px 26px', flexWrap: 'wrap' }}>
        <a href={href} style={{ fontFamily: 'var(--serif)', fontSize: 'var(--fs-body)', fontWeight: 600, letterSpacing: '-.012em', color: 'var(--ink)', textDecoration: 'none', borderBottom: 0, whiteSpace: 'nowrap' }}>
          {wordmark}<span style={{ color: 'var(--accent)' }}>{wordmarkAccent}</span>
        </a>
        <Nav items={items} current={current} />
      </div>
    </header>
  );
}
