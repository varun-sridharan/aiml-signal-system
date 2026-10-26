// Weekly Digest edition view, composed from the system's primitives.
// Sample content only. The live template is Weekly Digest Template.dc.html.
const NS = window[Object.keys(window).find(k => window[k] && typeof window[k] === 'object' && window[k].SiteHeader)] || {};
const { SiteHeader, Tabs, StoryTile, QuoteTile, Pill } = NS;

const NAV = [{label:'Product',href:'#'},{label:'Weekly Digest',href:'#'},{label:'System Design',href:'#'},{label:'Roadmap & Backlog',href:'#'},{label:'Plan & Progress',href:'#'}];
const KICK = {fontFamily:'var(--mono)',fontSize:10.5,letterSpacing:'var(--track-kicker)',textTransform:'uppercase',fontWeight:600};

function Section({ id, color, ink, kicker, title, hl, count, preview, open, onToggle, children }) {
  return (
    <section id={id} style={{borderTop:'1px solid var(--rule)',padding:'22px 0',borderLeft: open ? '3px solid ' + color : '3px solid transparent', paddingLeft: 16, marginLeft: -19}}>
      <button aria-expanded={open} onClick={onToggle} style={{all:'unset',cursor:'pointer',display:'block',width:'100%'}}>
        <div style={{...KICK,color}}>{open ? '▾' : '▸'} {kicker}{count != null ? ' · ' + count : ''}</div>
        <h2 style={{fontFamily:'var(--serif)',fontSize:open ? 'var(--fs-read-headline)' : 26,fontWeight:600,lineHeight:'var(--lh-head)',letterSpacing:'-.018em',margin:'8px 0 0',color:'var(--ink)'}}>{title} <span style={{color:ink}}>{hl}</span></h2>
        {!open && <p style={{margin:'6px 0 0',fontSize:16,color:'var(--muted)'}}>{preview}</p>}
      </button>
      {open && <div style={{marginTop:18}}>{children}</div>}
    </section>
  );
}

function Edition() {
  const [tab, setTab] = React.useState('news');
  const [sub, setSub] = React.useState('stories');
  const [open, setOpen] = React.useState({thread:false,learning:false,stories:true,leaders:false});
  const flip = k => setOpen(o => ({...o,[k]:!o[k]}));
  return (
    <div>
      <SiteHeader current="Weekly Digest" items={NAV} />
      <main style={{maxWidth:'var(--w-page)',margin:'0 auto',background:'var(--paper)',padding:'26px 40px 64px'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',gap:16,flexWrap:'wrap'}}>
          <div style={{display:'flex',gap:12,alignItems:'baseline'}}>
            <span style={{fontFamily:'var(--serif)',fontSize:20,fontWeight:600}}>Weekly Digest</span>
            <Pill label="SAMPLE" tone="EXCERPT" />
          </div>
          <span style={{fontFamily:'var(--mono)',fontSize:11,letterSpacing:'var(--track-label)',color:'var(--muted)'}}>EDITION 09 · OCT-17-2026 TO OCT-23-2026</span>
        </div>
        <div style={{marginTop:18}}>
          <Tabs value={tab} onChange={setTab} items={[{id:'news',label:'News'},{id:'prediction',label:'Prediction',count:'3/3'},{id:'homework',label:'Homework',count:1},{id:'archive',label:'Archive'}]} />
        </div>
        <div style={{position:'sticky',top:58,background:'var(--paper)',zIndex:5,marginTop:8}}>
          <Tabs variant="sub" value={sub} onChange={id => { setSub(id); setOpen(o => ({...o,[id]:true})); }} label="News sections"
            items={[{id:'thread',label:'The week in one thread',color:'var(--sec-thread)'},{id:'learning',label:'Learning for the week',color:'var(--sec-learning)'},{id:'stories',label:"This week's stories",count:9,color:'var(--sec-stories)'},{id:'leaders',label:'Industry leaders',count:5,color:'var(--sec-leaders)'}]} />
        </div>
        <div style={{maxWidth:'var(--w-column)',marginTop:12}}>
          <Section id="thread" color="var(--sec-thread)" ink="var(--sec-thread-ink)" kicker="The week in one thread" title="Capability got cheaper." hl="Judgment didn't." preview="One editor's note before any single story." open={open.thread} onToggle={() => flip('thread')}>
            <p style={{fontSize:'var(--fs-read-body)',lineHeight:'var(--lh-read)',color:'var(--ink-2)',margin:0}}>Sample thread paragraph naming how the week shifted, citing two stories as evidence, and ending on the harder question.</p>
          </Section>
          <Section id="learning" color="var(--sec-learning)" ink="var(--sec-learning-ink)" kicker="Learning for the week" title="Inference fell 80% in a quarter. The cost of checking an answer" hl="did not move." preview="The essay: what the stories mean together." open={open.learning} onToggle={() => flip('learning')}>
            <p style={{fontSize:'var(--fs-read-body)',lineHeight:'var(--lh-read)',color:'var(--ink-2)',margin:0}}>Sample essay opening.</p>
          </Section>
          <Section id="stories" color="var(--sec-stories)" ink="var(--sec-stories-ink)" kicker="This week's stories" count={9} title="What happened," hl="in order of consequence" preview="Nine numbered tiles, the first a feature." open={open.stories} onToggle={() => flip('stories')}>
            <StoryTile n={1} feature tag="DO THIS" headline="Agent tools now ship with a permission manifest, and the defaults are wide open" hashtags={['Agents','Permissions','ModelContextProtocol']}
              body="Sample feature body. It builds from the news, to how it works, to the twist the headline number hides."
              why="Faster setup in exchange for a wider blast radius." tryIt="Sample worked example in two sentences."
              sources={[{label:'GitHub Blog',href:'#',primary:true},{label:'Hugging Face Papers',href:'#'}]} />
            <StoryTile n={2} tag="RADAR" fromBacklog headline="Open weights closed half the gap on long-context recall" hashtags={['OpenWeights','Evaluation']}
              body="Sample short body of three sentences with specifics."
              inNinety={{method:'Sample method.',result:'Sample result.',caveat:'Sample caveat.'}}
              sources={[{label:'The Decoder',href:'#',primary:true}]} />
          </Section>
          <Section id="leaders" color="var(--sec-leaders)" ink="var(--sec-leaders-ink)" kicker="What are industry leaders talking?" count={5} title="Five voices on" hl="the same week" preview="Named reactions, quoted verbatim." open={open.leaders} onToggle={() => flip('leaders')}>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
              <QuoteTile quote="Sample verbatim quote about who checks the model's work…" href="#" name="Sample Person" role="CTO" org="Example Co" date="Oct 21" kind="POST" why="Sample trade-off line." />
              <QuoteTile quote="A longer sample excerpt that carries the substance of the argument…" href="#" name="Another Sample" role="Researcher" org="Example Lab" date="Oct 22" kind="EXCERPT" why="Sample trade-off line." />
            </div>
          </Section>
        </div>
      </main>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById('root')).render(<Edition />);
