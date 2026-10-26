import React from 'react';
import { Pill } from '../labels/Pill';
import { Hashtags } from '../labels/Hashtags';
import { Thumbs } from '../feedback/Thumbs';

const LABEL = { fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '.14em', fontWeight: 600, whiteSpace: 'nowrap' };

/** One numbered story in This Week's Stories. A feature is larger but stays one tile in the same list. */
export function StoryTile({ n, tag = 'KNOW', headline, hashtags = [], body, why, tryIt, inNinety, tiesTo, sources = [], checked = true, fromBacklog = false, feature = false, color = 'var(--sec-stories)', children }) {
  const primary = sources.filter(s => s.primary);
  const also = sources.filter(s => !s.primary);
  const link = s => <a key={s.href} href={s.href} target="_blank" rel="noopener" style={{ color: 'var(--soft)', borderBottom: '1px solid var(--rule-strong)' }}>{s.label} ↗</a>;
  return (
    <article style={{ padding: feature ? '34px 0 32px' : '26px 0 24px', borderTop: '1px solid var(--rule)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--fs-read-number)', color: 'var(--faint)', lineHeight: 1 }}>{String(n).padStart(2, '0')}/</span>
        <Pill label={tag} />
        {feature && <span style={{ ...LABEL, fontSize: 10.5, color }}>FEATURE</span>}
        {fromBacklog && <span style={{ ...LABEL, fontSize: 10.5, color: 'var(--accent)', borderLeft: '2px solid var(--accent)', paddingLeft: 7 }}>FROM YOUR BACKLOG</span>}
      </div>
      <h3 style={{ fontFamily: 'var(--serif)', fontSize: feature ? 'var(--fs-read-feature)' : 'var(--fs-read-tile)', fontWeight: 600, lineHeight: 1.18, letterSpacing: '-.012em', margin: '14px 0 8px', color: 'var(--ink)', textWrap: 'pretty' }}>{headline}</h3>
      {hashtags.length > 0 && <Hashtags tags={hashtags} />}
      <div style={{ fontSize: 'var(--fs-read-body)', lineHeight: 'var(--lh-read)', color: 'var(--ink-2)', marginTop: 12 }}>{body}</div>
      {children}
      {why && <p style={{ margin: '14px 0 0', fontSize: 17, lineHeight: 1.55, color: 'var(--ink-2)' }}><span style={{ ...LABEL, color }}>WHY IT MATTERS / </span>{why}</p>}
      {tryIt && <div style={{ marginTop: 14, background: 'var(--inset)', borderLeft: '2px solid ' + color, padding: '12px 16px', fontSize: 16, lineHeight: 1.55, color: 'var(--ink-2)' }}><div style={{ ...LABEL, color, marginBottom: 4 }}>TRY IT</div>{tryIt}</div>}
      {inNinety && (
        <div style={{ marginTop: 14, border: '1px solid var(--rule)' }}>
          <div style={{ ...LABEL, fontSize: 10.5, color: 'var(--muted)', padding: '8px 12px', borderBottom: '1px solid var(--rule)' }}>IN 90 SECONDS</div>
          {['method', 'result', 'caveat'].map(k => inNinety[k] && (
            <div key={k} style={{ display: 'grid', gridTemplateColumns: '92px 1fr', gap: 12, padding: '8px 12px', borderBottom: k === 'caveat' ? 0 : '1px solid var(--rule2)', fontSize: 15.5, lineHeight: 1.5 }}>
              <span style={{ ...LABEL, fontSize: 10.5, color: 'var(--muted)', textTransform: 'uppercase' }}>{k}</span><span style={{ color: 'var(--ink-2)' }}>{inNinety[k]}</span>
            </div>))}
        </div>)}
      {tiesTo && <p style={{ margin: '12px 0 0', fontSize: 15.5, color: 'var(--soft)' }}><span style={{ color }}>↔ Ties to </span><a href={tiesTo.href} style={{ color: 'var(--ink)', borderBottom: '1px solid ' + color }}>{tiesTo.label}</a></p>}
      {sources.length > 0 && (
        <p style={{ margin: '14px 0 0', fontFamily: 'var(--mono)', fontSize: 11.5, letterSpacing: '.02em', color: 'var(--muted)', lineHeight: 1.7 }}>
          Primary: {primary.map(link)}{also.length > 0 && <> · Also: {also.map((s, i) => <React.Fragment key={s.href}>{i > 0 && ', '}{link(s)}</React.Fragment>)}</>}
          {checked && <span style={{ color: 'var(--ok)', marginLeft: 10 }}>✓ checked against source</span>}
        </p>)}
      <div style={{ marginTop: 16 }}><Thumbs color={color} /></div>
    </article>
  );
}
