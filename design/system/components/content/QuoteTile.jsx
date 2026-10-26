import React from 'react';
import { Pill } from '../labels/Pill';

const initials = name => String(name || '').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('');

/** A testimonial tile for What are industry leaders talking? Quote verbatim; truncation shown as …. */
export function QuoteTile({ quote, href, name, role, org, date, kind = 'POST', why, color = 'var(--sec-leaders)' }) {
  return (
    <figure style={{ margin: 0, padding: '22px 22px 20px', background: 'var(--panel)', border: '1px solid var(--rule)', borderRadius: 'var(--radius-md)', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <span aria-hidden="true" style={{ fontFamily: 'var(--serif)', fontSize: 60, lineHeight: .7, color }}>“</span>
        <Pill label={kind} />
      </div>
      <blockquote style={{ margin: 0, fontFamily: 'var(--serif)', fontStyle: 'italic', fontSize: 'var(--fs-read-quote)', lineHeight: 1.5, color: 'var(--ink-2)' }}>{quote}</blockquote>
      {href && <a href={href} target="_blank" rel="noopener" style={{ alignSelf: 'flex-start', fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '.06em', color: 'var(--soft)', borderBottom: '1px solid var(--rule-strong)' }}>read the full post ↗</a>}
      <figcaption style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 12, borderTop: '1px solid var(--rule2)' }}>
        <span aria-hidden="true" style={{ width: 30, height: 30, borderRadius: '50%', border: '1px solid var(--rule-strong)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '.06em', color: 'var(--soft)', flex: 'none' }}>{initials(name)}</span>
        <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.35 }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>{name}</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '.04em', color: 'var(--muted)' }}>{[role, org].filter(Boolean).join(' · ')}{date ? ' · ' + date : ''}</span>
        </span>
      </figcaption>
      {why && <p style={{ margin: 0, fontSize: 15.5, lineHeight: 1.55, color: 'var(--ink-2)' }}><span style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '.14em', fontWeight: 600, color }}>WHY IT MATTERS / </span>{why}</p>}
    </figure>
  );
}
