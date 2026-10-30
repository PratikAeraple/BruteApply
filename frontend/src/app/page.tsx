"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Job = { id:string; company:string; title:string; city:string; province:string; work_mode:string; category:string; score:number; confidence:number; matched:string[]; missing:string[]; blockers:string[]; action:string; source:string; posted:string; url:string };
type Ticket = { id:string; category:string; priority:string; status:string; summary:string; required_action:string };
type Overview = { mode:string; counts:Record<string,number>; workers:Record<string,string>; integrations:Record<string,string>; backup:{status:string;restore_test:string}; components?:Record<string,string> };
type ApplicationRow = { id?:string; company:string; title:string; stage:string; date:string; score:number };
type NavItem = { id:string; label:string; icon:string; count?:string };

const nav:NavItem[] = [
  {id:"overview",label:"Overview",icon:"◫"},{id:"jobs",label:"Job explorer",icon:"⌕",count:"18"},
  {id:"pipeline",label:"Applications",icon:"▤"},{id:"tickets",label:"Tickets & decisions",icon:"◇",count:"2"},
  {id:"profile",label:"My profile",icon:"○"},{id:"resumes",label:"Resume manager",icon:"▱"},
  {id:"integrations",label:"Integrations",icon:"⤴"},{id:"analytics",label:"Analytics",icon:"⌁"},
  {id:"agents",label:"Agent control",icon:"◉"},{id:"audit",label:"Audit log",icon:"≋"},
  {id:"health",label:"System health",icon:"♡"},
];

const demoJobs:Job[] = [
  {id:"demo-job-01",company:"Northwind Community Services",title:"IT Support Technician",city:"Toronto",province:"Ontario",work_mode:"Hybrid",category:"L1 / Help Desk",score:88,confidence:.79,matched:["Support","Linux","Networking"],missing:["Active Directory"],blockers:[],action:"REVIEW",source:"Company Website",posted:"Oct 27",url:"https://example.invalid/jobs/it-support-01"},
  {id:"demo-job-02",company:"Maple Systems Cooperative",title:"Junior Systems Administrator",city:"Winnipeg",province:"Manitoba",work_mode:"On-site",category:"Systems / Infrastructure",score:81,confidence:.75,matched:["Linux","Docker","Monitoring"],missing:["Active Directory"],blockers:[],action:"REVIEW",source:"Greenhouse",posted:"Oct 26",url:"https://example.invalid/jobs/junior-sysadmin-02"},
  {id:"demo-job-03",company:"Prairie Technical Services",title:"Endpoint Support Analyst",city:"Calgary",province:"Alberta",work_mode:"Field / Multi-site",category:"L2 / Desktop / Technical",score:76,confidence:.66,matched:["Support","Networking"],missing:["Active Directory","Cloud"],blockers:[],action:"REVIEW",source:"Lever",posted:"Oct 24",url:"https://example.invalid/jobs/endpoint-03"},
  {id:"demo-job-04",company:"Cedar Grove Health",title:"Service Desk Analyst",city:"Ottawa",province:"Ontario",work_mode:"Remote",category:"L1 / Help Desk",score:69,confidence:.64,matched:["Support","Networking"],missing:["Active Directory","Cloud","Automation"],blockers:[],action:"SKIP",source:"Company Website",posted:"Oct 23",url:"https://example.invalid/jobs/service-desk-04"},
];

const demoTickets:Ticket[] = [
  {id:"BA-DEMO-1042",category:"SENSITIVE_QUESTION",priority:"HIGH",status:"OPEN",summary:"Employer asks for a sensitive declaration",required_action:"Review the question and provide an approved answer or skip."},
  {id:"BA-DEMO-1041",category:"MANUAL_SUBMISSION_REQUIRED",priority:"NORMAL",status:"OPEN",summary:"Application provider requires an interactive account step",required_action:"Open the employer site and complete it manually if desired."},
];

const demoApplications = [
  {company:"Harbour Technology Group",title:"Technical Support Specialist",stage:"Prepared",date:"Oct 28",score:84},
  {company:"Prairie Systems Co-op",title:"Junior Infrastructure Analyst",stage:"Shortlisted",date:"Oct 27",score:79},
  {company:"Northstar Services",title:"Desktop Support Technician",stage:"Qualified",date:"Oct 25",score:77},
];

const defaultOverview:Overview = {mode:"DEMO",counts:{discovered:18,qualified:7,shortlisted:3,prepared:2,submitted:0,unverified:0,tickets:2,interviews:0,offers:0},workers:{discovery:"PAUSED",matching:"READY",documents:"READY",submission:"DISABLED",sheet_sync:"NOT CONNECTED",email:"NOT CONNECTED"},integrations:{google_sheets:"NOT CONFIGURED",email:"NOT CONFIGURED",ai:"FAKE PROVIDER"},backup:{status:"DEFERRED / NOT IMPLEMENTED",restore_test:"NOT TESTED"},components:{database:"SYNTHETIC",queue:"SYNTHETIC",worker:"NOT RUNNING"}};

function Badge({children,tone="neutral"}:{children:React.ReactNode;tone?:string}) { return <span className={`badge badge-${tone}`}>{children}</span>; }
function Mark({children}:{children:React.ReactNode}) { return <span className="mark" aria-hidden="true">{children}</span>; }
function Score({value}:{value:number}) { return <div className="score-wrap"><span className="score-num">{value}</span><span className="score-label">MATCH</span><div className="score-track"><i style={{width:`${value}%`}} /></div></div>; }

export default function Home() {
  const [active,setActive] = useState("overview");
  const [mobileNav,setMobileNav] = useState(false);
  const [dark,setDark] = useState(true);
  const [search,setSearch] = useState("");
  const [province,setProvince] = useState("All provinces");
  const [jobs,setJobs] = useState<Job[]>(demoJobs);
  const [tickets,setTickets] = useState<Ticket[]>(demoTickets);
  const [applications,setApplications] = useState<ApplicationRow[]>(demoApplications);
  const [overview,setOverview] = useState<Overview>(defaultOverview);
  const [shortlisted,setShortlisted] = useState<Set<string>>(new Set(["demo-job-01","demo-job-02"]));
  const [resolved,setResolved] = useState<Set<string>>(new Set());
  const [workerStates,setWorkerStates] = useState<Record<string,string>>(defaultOverview.workers);
  const [toast,setToast] = useState("");

  useEffect(()=>{
    Promise.all([
      fetch("/api/v1/overview",{cache:"no-store"}),fetch("/api/v1/jobs",{cache:"no-store"}),fetch("/api/v1/tickets",{cache:"no-store"}),fetch("/api/v1/applications",{cache:"no-store"}),
    ]).then(async ([o,j,t,a])=>{
      if(!o.ok||!j.ok||!t.ok||!a.ok) return;
      const [ov,jv,tv,av]=await Promise.all([o.json(),j.json(),t.json(),a.json()]);
      if(ov.mode==="PRODUCTION") {
        setOverview(ov);setJobs(jv.items||[]);setTickets(tv.items||[]);
        setApplications((av.items||[]).map((item:Record<string,unknown>)=>({
          company:String(item.company||"Unknown company"),title:String(item.title||"Unknown role"),stage:String(item.state||"DISCOVERED"),
          date:item.updated_at?new Intl.DateTimeFormat("en-CA",{timeZone:"America/Toronto",month:"short",day:"2-digit"}).format(new Date(String(item.updated_at))):"—",
          score:Number(item.score||0),id:String(item.id),
        })));
        setWorkerStates(ov.workers||{});
      }
    }).catch(()=>{});
  },[]);

  useEffect(()=>{document.documentElement.dataset.theme=dark?"dark":"light";try{localStorage.setItem("bruteapply-theme",dark?"dark":"light")}catch{}},[dark]);
  useEffect(()=>{if(!toast)return;const id=setTimeout(()=>setToast(""),2800);return()=>clearTimeout(id)},[toast]);

  const filteredJobs=useMemo(()=>jobs.filter(j=>{
    const q=search.toLowerCase();
    return (!q||`${j.company} ${j.title} ${j.city} ${j.category}`.toLowerCase().includes(q)) && (province==="All provinces"||j.province===province);
  }),[jobs,search,province]);
  const openTickets=tickets.filter(t=>!resolved.has(t.id));

  const notify=useCallback((message:string)=>setToast(message),[]);
  function select(id:string){setActive(id);setMobileNav(false)}
  async function toggleShortlist(id:string){
    if(overview.mode==="PRODUCTION"){
      const response=await fetch("/api/v1/applications",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({job_id:id})});
      if(!response.ok){notify("The job could not be shortlisted; check profile setup and try again");return}
      const result=await response.json();setShortlisted(s=>new Set(s).add(id));notify(result.duplicate?"This job is already in the application pipeline":"Job added to the shortlist");
      const refreshed=await fetch("/api/v1/applications",{cache:"no-store"});if(refreshed.ok){const data=await refreshed.json();setApplications((data.items||[]).map((item:Record<string,unknown>)=>({company:String(item.company||"Unknown company"),title:String(item.title||"Unknown role"),stage:String(item.state||"DISCOVERED"),date:"—",score:Number(item.score||0),id:String(item.id)})))}
      return;
    }
    setShortlisted(s=>{const n=new Set(s);n.has(id)?n.delete(id):n.add(id);return n});
  }
  async function controlWorker(name:string,action:string){
    if(overview.mode==="DEMO") {setWorkerStates(s=>({...s,[name]:action==="PAUSE"?"PAUSED":action==="START"?"RUNNING":action}));notify(`Demo ${name} control updated`);return}
    const response=await fetch("/api/v1/agent/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agent:name,action})});
    notify(response.ok?`${name} ${action.toLowerCase()}`:`${name} control was blocked`);
  }
  async function decideTicket(id:string,decision:string,details:string){
    if(overview.mode==="DEMO") {if(["DENY","SKIP","RESOLVE"].includes(decision))setResolved(s=>new Set(s).add(id));notify(`Demo ticket: ${decision.toLowerCase()} recorded locally`);return}
    const terminal=["DENY","SKIP","RESOLVE"].includes(decision);
    const body={state:terminal?(decision==="RESOLVE"?"RESOLVED":"CANCELLED"):"IN_PROGRESS",decision,
      resolution:terminal?details:undefined,details:["EDIT","PROVIDE_INFORMATION"].includes(decision)?details:undefined};
    const response=await fetch(`/api/v1/tickets/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(!response.ok){notify("Ticket action was blocked; no handler may be available for this decision");return}
    if(terminal)setTickets(items=>items.filter(item=>item.id!==id));notify("Ticket decision recorded; no workflow is resumed unless a handler is explicitly available");
  }

  return <div className={`app-shell ${dark?"theme-dark":"theme-light"}`}>
    <button className="mobile-menu" aria-label="Open navigation" aria-expanded={mobileNav} onClick={()=>setMobileNav(!mobileNav)}>☰</button>
    <aside className={`sidebar ${mobileNav?"sidebar-open":""}`}>
      <div className="brand"><div className="brand-symbol">B<span>.</span></div><div><strong>BruteApply</strong><small>JOB SEARCH OPERATIONS</small></div></div>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(item=><button key={item.id} className={`nav-item ${active===item.id?"nav-active":""}`} onClick={()=>select(item.id)} aria-current={active===item.id?"page":undefined}><span className="nav-icon">{item.icon}</span><span>{item.label}</span>{item.count&&<span className="nav-count">{item.id==="tickets"?openTickets.length:item.count}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="safe-card"><div className="safe-icon">✓</div><div><strong>Safe ramp</strong><small>Submission disabled</small></div><span className="live-dot" /></div><button className="profile-mini" onClick={()=>select("profile")}><span className="avatar">PA</span><span><strong>Private workspace</strong><small>Owner profile</small></span><span className="chevron">⌄</span></button></div>
    </aside>

    <main className="main-area">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="slash">/</span><strong>{nav.find(n=>n.id===active)?.label||"Overview"}</strong></div><div className="top-actions"><Badge tone={overview.mode==="DEMO"?"amber":"green"}>{overview.mode==="DEMO"?"DEMO DATA":"PRIVATE SESSION"}</Badge><button className="icon-button" aria-label="Toggle light and dark appearance" onClick={()=>setDark(!dark)}>{dark?"☼":"◐"}</button><button className="notification-button" aria-label={`${openTickets.length} open tickets`} onClick={()=>select("tickets")}>♧<i>{openTickets.length}</i></button><span className="avatar avatar-small">PA</span></div></header>
      <div className="content">
        {active==="overview"&&<OverviewPage overview={overview} jobs={jobs} tickets={openTickets} workers={workerStates} shortlist={shortlisted} onSelect={select} onOpenJob={()=>select("jobs")} />}
        {active==="jobs"&&<JobsPage jobs={filteredJobs} total={jobs.length} search={search} province={province} setSearch={setSearch} setProvince={setProvince} shortlisted={shortlisted} toggleShortlist={toggleShortlist} notify={notify} />}
        {active==="pipeline"&&<PipelinePage applications={applications} notify={notify} />}
        {active==="tickets"&&<TicketsPage tickets={openTickets} decide={decideTicket} />}
        {active==="profile"&&<ProfilePage mode={overview.mode} notify={notify} />}
        {active==="resumes"&&<ResumePage mode={overview.mode} notify={notify} />}
        {active==="integrations"&&<IntegrationsPage overview={overview} notify={notify} />}
        {active==="analytics"&&<AnalyticsPage overview={overview} />}
        {active==="agents"&&<AgentsPage workers={workerStates} control={controlWorker} emergency={()=>{setWorkerStates(s=>({...s,submission:"EMERGENCY STOP"}));notify("Emergency stop requested; no demo workers run")}} mode={overview.mode} />}
        {active==="audit"&&<AuditPage mode={overview.mode} />}
        {active==="health"&&<HealthPage overview={overview} />}
      </div>
      <footer className="page-footer"><span><i className="live-dot" /> Private workspace · America/Toronto</span><span>BruteApply <b>0.1</b> · <a href="https://github.com/PratikAeraple/BruteApply" target="_blank" rel="noreferrer">Open source ↗</a></span></footer>
    </main>
    {mobileNav&&<button aria-label="Close navigation" className="nav-backdrop" onClick={()=>setMobileNav(false)} />}
    {toast&&<div className="toast" role="status">{toast}</div>}
  </div>;
}

function PageHeading({eyebrow,title,subtitle,action}:{eyebrow:string;title:string;subtitle:string;action?:React.ReactNode}){return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action&&<div className="heading-action">{action}</div>}</div>}

function OverviewPage({overview,jobs,tickets,workers,shortlist,onSelect,onOpenJob}:{overview:Overview;jobs:Job[];tickets:Ticket[];workers:Record<string,string>;shortlist:Set<string>;onSelect:(s:string)=>void;onOpenJob:()=>void}){
 const c=overview.counts;
 return <>
  <PageHeading eyebrow="THURSDAY, OCTOBER 29, 2026" title="Good afternoon." subtitle="Your search is moving forward. Here is what needs attention today." action={<button className="button button-primary" onClick={onOpenJob}><Mark>⌕</Mark> Review new matches</button>} />
  <div className="notice notice-demo"><span className="notice-mark">ⓘ</span><div><strong>{overview.mode==="DEMO"?"Exploring synthetic data":"Private owner workspace"}</strong><p>{overview.mode==="DEMO"?"This preview contains fictional candidates and employers. It never connects to your personal integrations.":"Your data is private. Autonomous submission remains disabled until its one-time activation checklist is complete."}</p></div><button onClick={()=>onSelect("health")} aria-label="View system health">↗</button></div>
  <section className="metric-grid" aria-label="Search pipeline summary">
   <Metric label="Jobs discovered" value={c.discovered||0} change="Last 30 days" color="blue" icon="⌕" />
   <Metric label="Qualified" value={c.qualified||0} change="Match threshold met" color="mint" icon="✓" />
   <Metric label="Shortlisted" value={c.shortlisted||0} change={`${shortlist.size} in active review`} color="violet" icon="☆" />
   <Metric label="Applications" value={(c.submitted||0)+(c.prepared||0)} change={`${c.prepared||0} prepared · ${c.submitted||0} submitted`} color="orange" icon="↗" />
  </section>
  <div className="dashboard-grid">
   <section className="panel matches-panel"><PanelTitle eyebrow="PRIORITIZE QUALITY" title="Strongest matches" action={<button className="text-button" onClick={onOpenJob}>View all <span>→</span></button>} /><div className="match-list">{jobs.slice(0,3).map((job,i)=><article className="match-row" key={job.id}><div className={`company-mark company-${i}`}>{job.company.slice(0,1)}</div><div className="match-main"><div className="match-titleline"><h3>{job.title}</h3><Badge tone={job.score>=85?"green":"blue"}>{job.category}</Badge></div><p>{job.company} <span>·</span> {job.city}, {job.province} <span>·</span> {job.work_mode}</p><div className="skill-row">{job.matched.slice(0,3).map(x=><span className="skill skill-match" key={x}>✓ {x}</span>)}{job.missing.slice(0,1).map(x=><span className="skill skill-missing" key={x}>− {x}</span>)}</div></div><Score value={job.score} /><button className="row-arrow" aria-label={`Review ${job.title}`} onClick={onOpenJob}>↗</button></article>)}</div><button className="button button-secondary full-button" onClick={onOpenJob}>Explore all matches <span>→</span></button></section>
   <section className="panel attention-panel"><PanelTitle eyebrow="OWNER REVIEW" title="Needs your attention" action={<button className="round-count" onClick={()=>onSelect("tickets")}>{tickets.length}</button>} /><div className="ticket-preview">{tickets.slice(0,3).map((t,i)=><button className="attention-item" key={t.id} onClick={()=>onSelect("tickets")}><span className={`attention-icon attention-${i}`}>{i===0?"!":"↗"}</span><span className="attention-copy"><strong>{t.summary}</strong><small>{t.category.replaceAll("_"," ")} <i>·</i> {t.id}</small></span><span className="attention-arrow">›</span></button>)}</div>{tickets.length===0&&<div className="empty-state">Nothing waiting for a decision.</div>}<button className="button button-secondary full-button" onClick={()=>onSelect("tickets")}>Open decision center <span>→</span></button></section>
   <section className="panel pipeline-panel"><PanelTitle eyebrow="MOMENTUM" title="Application pipeline" action={<button className="text-button" onClick={()=>onSelect("pipeline")}>Open board <span>→</span></button>} /><div className="pipeline-stages">{[["Qualified",c.qualified||0,"stage-blue"],["Shortlisted",c.shortlisted||0,"stage-violet"],["Prepared",c.prepared||0,"stage-orange"],["Submitted",c.submitted||0,"stage-green"]].map(([name,value,cls])=><div className="stage" key={String(name)}><div className={`stage-dot ${cls}`} /><strong>{value}</strong><span>{name}</span></div>)}</div><div className="pipeline-bar">{["stage-blue","stage-violet","stage-orange","stage-green"].map((x,i)=><i key={x} className={x} style={{width:`${[42,28,20,10][i]}%`}} />)}</div><div className="pipeline-foot"><span><i className="live-dot" /> No duplicates in this sample</span><span>Submission <b>off</b></span></div></section>
   <section className="panel operations-panel"><PanelTitle eyebrow="AUTOMATION" title="Worker status" action={<button className="text-button" onClick={()=>onSelect("agents")}>Manage <span>→</span></button>} /><div className="worker-list">{Object.entries(workers).slice(0,4).map(([name,status])=><div className="worker-row" key={name}><span className="worker-dot" data-status={status.toLowerCase()} /><span className="worker-name">{name.replaceAll("_"," ")}</span><span className={`worker-status status-${status.toLowerCase().replaceAll(" ","-")}`}>{status}</span></div>)}</div><div className="operation-divider" /><div className="sync-line"><span className="sync-glyph">⟳</span><span><strong>Google Sheet sync</strong><small>Not connected</small></span><Badge tone="neutral">SETUP NEEDED</Badge></div></section>
   <section className="panel activity-panel"><PanelTitle eyebrow={overview.mode==="DEMO"?"SYNTHETIC ACTIVITY":"RECENT CHANGES"} title="Activity" action={<button className="text-button" onClick={()=>onSelect("audit")}>Audit log <span>→</span></button>} />{overview.mode==="DEMO"?<div className="activity-list"><Activity time="09:42" title="Example role matched" detail="Synthetic IT Support Technician · Toronto" icon="⌕" /><Activity time="09:18" title="Example duplicate check" detail="Synthetic cross-post record" icon="✓" /><Activity time="Yesterday" title="Example manual review" detail="Synthetic exception ticket" icon="!" warning /></div>:<div className="empty-state">No live activity is shown until an authorized source or owner action creates an audit event.</div>}</section>
  </div>
 </>
}

function Metric({label,value,change,color,icon}:{label:string;value:number;change:string;color:string;icon:string}){return <article className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon metric-${color}`}>{icon}</span></div><strong className="metric-value">{value}</strong><small>{change}</small><div className={`metric-accent accent-${color}`} /></article>}
function PanelTitle({eyebrow,title,action}:{eyebrow:string;title:string;action?:React.ReactNode}){return <div className="panel-title"><div><div className="eyebrow">{eyebrow}</div><h2>{title}</h2></div>{action}</div>}
function Activity({time,title,detail,icon,warning=false}:{time:string;title:string;detail:string;icon:string;warning?:boolean}){return <div className="activity-row"><span className={`activity-icon ${warning?"activity-warn":""}`}>{icon}</span><div className="activity-copy"><strong>{title}</strong><small>{detail}</small></div><time>{time}</time></div>}

function JobsPage({jobs,total,search,province,setSearch,setProvince,shortlisted,toggleShortlist,notify}:{jobs:Job[];total:number;search:string;province:string;setSearch:(v:string)=>void;setProvince:(v:string)=>void;shortlisted:Set<string>;toggleShortlist:(id:string)=>void;notify:(s:string)=>void}){
 const [minScore,setMinScore]=useState(0);
 const visible=jobs.filter(j=>j.score>=minScore);
 return <><PageHeading eyebrow="DISCOVER & EVALUATE" title="Job explorer" subtitle="Relevant roles, ranked against verified profile facts. Every score includes its reasoning." action={<button className="button button-secondary" onClick={()=>notify("Discovery stays paused until a permitted source is configured")}>⟳ Run discovery</button>} />
  <section className="panel filters-panel"><label className="search-field"><span>⌕</span><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search title, company, skill…" aria-label="Search jobs" /></label><label className="select-field"><span className="sr-only">Province</span><select value={province} onChange={e=>setProvince(e.target.value)}><option>All provinces</option><option>Ontario</option><option>Alberta</option><option>Manitoba</option></select></label><label className="select-field"><span className="sr-only">Minimum match score</span><select value={minScore} onChange={e=>setMinScore(Number(e.target.value))}><option value={0}>Any match</option><option value={70}>70%+</option><option value={80}>80%+</option><option value={85}>85%+</option></select></label><button className="filter-button" onClick={()=>{setSearch("");setProvince("All provinces");setMinScore(0)}}>Reset</button><span className="result-count">{visible.length} of {total} roles</span></section>
  <div className="job-results">{visible.map((j,i)=><article className="panel job-card" key={j.id}><div className={`company-mark company-${i%4}`}>{j.company.slice(0,1)}</div><div className="job-card-main"><div className="job-title-row"><div><h2>{j.title}</h2><p>{j.company}</p></div><Score value={j.score} /></div><div className="job-meta"><span>⌖ {j.city}, {j.province}</span><span>◷ {j.work_mode}</span><span>▧ {j.category}</span><span>Source: {j.source}</span></div><div className="explanation"><strong>Why it matches</strong><p>{j.matched.length} verified skill groups match this posting. Confidence {Math.round(j.confidence*100)}%. {j.missing.length?`Review gaps: ${j.missing.join(", ")}.`:"No configured skill gaps."}</p></div>{j.blockers.length>0&&<div className="blocker">⛔ {j.blockers.join(" · ")}</div>}<div className="job-card-actions"><a className="text-link" href={j.url} target="_blank" rel="noreferrer">Open source posting ↗</a><button className={`button ${shortlisted.has(j.id)?"button-shortlisted":"button-secondary"}`} onClick={()=>{toggleShortlist(j.id);notify(shortlisted.has(j.id)?"Removed from shortlist":"Added to shortlist")}}>{shortlisted.has(j.id)?"✓ Shortlisted":"☆ Shortlist"}</button><button className="button button-primary" onClick={()=>notify("Preparation requires profile review and complete verified facts")}>Prepare application</button></div></div></article>)}{visible.length===0&&<div className="panel empty-state">No roles match those filters.</div>}</div>
 </>
}

function PipelinePage({applications,notify}:{applications:ApplicationRow[];notify:(s:string)=>void}) {
  const count=(state:string)=>applications.filter(item=>item.stage===state).length;
  return <><PageHeading eyebrow="APPLICATION MANAGEMENT" title="Application pipeline" subtitle="A verified application is different from an attempt. Keep evidence attached to every status." action={<button className="button button-primary" onClick={()=>notify("Add a reviewed job from Job Explorer first")}>＋ Add application</button>} />
    <div className="pipeline-summary">
      {[['Draft / qualified',count('QUALIFIED')],['Shortlisted',count('SHORTLISTED')],['Prepared',count('PREPARED')],['Submitted',count('SUBMITTED')],['Unverified',count('SUBMISSION_UNVERIFIED')]].map(([label,value])=><div className="pipeline-summary-card" key={String(label)}><span>{label}</span><strong>{value}</strong></div>)}
    </div>
    <section className="panel table-panel"><div className="table-toolbar"><div><strong>Active applications</strong><small>{applications.length ? 'Owner workspace records' : 'No application records yet'}</small></div><div className="table-actions"><button className="filter-button">All stages⌄</button><button className="filter-button">Filter</button></div></div>
      <div className="table-scroll"><table><thead><tr><th>Company / role</th><th>Match</th><th>Stage</th><th>Last activity</th><th>Next step</th></tr></thead><tbody>
        {applications.map((item)=><tr key={item.id||item.company+item.title}><td><strong>{item.title}</strong><small>{item.company}</small></td><td><Score value={item.score}/></td><td><Badge tone={item.stage==='PREPARED'?'amber':item.stage==='SUBMITTED'?'green':'blue'}>{item.stage}</Badge></td><td>{item.date}</td><td><button className="text-button" onClick={()=>notify('Application timeline is available after an event is recorded')}>Timeline ↗</button></td></tr>)}
      </tbody></table></div>
    </section>
    <div className="notice notice-warn"><span className="notice-mark">ⓘ</span><div><strong>No real submissions have been made</strong><p>Autonomous submission is disabled. Production requires the one-time owner activation checklist, a permitted adapter, and verified answers and documents.</p></div></div>
  </>;
}

function TicketsPage({tickets,decide}:{tickets:Ticket[];decide:(id:string,decision:string,details:string)=>void}){const [details,setDetails]=useState<Record<string,string>>({});return <><PageHeading eyebrow="OWNER DECISIONS" title="Tickets & approvals" subtitle="Decisions affect only the associated workflow. Denied and skipped actions cannot be resumed." /><div className="ticket-toolbar"><span>{tickets.length} open decisions</span><div><button className="filter-button" disabled>All categories</button><button className="filter-button" disabled>Priority</button></div></div><div className="ticket-list">{tickets.map(t=><article className="panel ticket-card" key={t.id}><div className="ticket-leading"><span className={`priority-marker priority-${t.priority.toLowerCase()}`} /><span className="ticket-symbol">◇</span></div><div className="ticket-content"><div className="ticket-kicker"><code>{t.id}</code><Badge tone={t.priority==="HIGH"?"red":"blue"}>{t.priority}</Badge><Badge>{t.category.replaceAll("_"," ")}</Badge></div><h2>{t.summary}</h2><p>{t.required_action}</p><textarea aria-label="Decision details or resolution reason" placeholder="Details for edit/provide information; required reason for deny/skip/resolve" value={details[t.id]||""} maxLength={5000} onChange={e=>setDetails(s=>({...s,[t.id]:e.target.value}))}/><div className="ticket-meta"><span>{t.status}</span><span>·</span><span>No employer submission occurs from this ticket action</span></div></div><div className="ticket-buttons"><button className="button button-primary" onClick={()=>decide(t.id,"APPROVE",details[t.id]||"")}>Approve</button><button className="button button-secondary" onClick={()=>decide(t.id,"PROVIDE_INFORMATION",details[t.id]||"")}>Provide information</button><button className="button button-secondary" onClick={()=>decide(t.id,"EDIT",details[t.id]||"")}>Edit</button><button className="button button-secondary" onClick={()=>decide(t.id,"RETRY",details[t.id]||"")}>Retry</button><button className="button button-secondary" onClick={()=>decide(t.id,"SKIP",details[t.id]||"")}>Skip</button><button className="button button-secondary" onClick={()=>decide(t.id,"DENY",details[t.id]||"")}>Deny</button><button className="button button-secondary" onClick={()=>decide(t.id,"RESOLVE",details[t.id]||"")}>Resolve</button></div></article>)}{tickets.length===0&&<div className="panel empty-state">All clear. New exceptions will appear here with the affected workflow and evidence.</div>}</div></>}

function ProfilePage({mode,notify}:{mode:string;notify:(s:string)=>void}) {
  const [facts,setFacts]=useState<Record<string,string>>({});
  const [factStatus,setFactStatus]=useState<Record<string,{verified:boolean;sensitivity:string}>>({});
  type CandidateFact={id:string;text:string;status:string;section:string;confidence:number;sensitivity:string;page:number;provenance:Record<string,unknown>};
  const [resumeFacts,setResumeFacts]=useState<CandidateFact[]>([]);
  const [selectedFacts,setSelectedFacts]=useState<Set<string>>(new Set());
  const [factSection,setFactSection]=useState("ALL");
  const [factStatusFilter,setFactStatusFilter]=useState("ALL");
  const [factSensitivity,setFactSensitivity]=useState("ALL");
  const [factBusy,setFactBusy]=useState(false);
  const [editingFact,setEditingFact]=useState<string|null>(null);
  const [editedFactText,setEditedFactText]=useState("");
  const [configured,setConfigured]=useState(false);
  const [saving,setSaving]=useState(false);
  const groups:[string,[string,string,boolean?][]][]=[
    ["Personal",[["full_name","Name"]]],
    ["Contact",[["contact_email","Contact email",true],["phone","Phone",true]]],
    ["Work Authorization",[["work_authorization","Owner-approved work authorization",true]]],
    ["Location & Relocation",[["city","Preferred city"],["province","Province"],["relocation","Relocation preference"]]],
    ["Availability",[["availability","Availability"]]],
    ["Experience",[["experience_summary","Experience summary"],["years_experience","Relevant years of experience"]]],
    ["Education",[["education","Education"]]],
    ["Certifications",[["certifications","Certifications"]]],
    ["Technical Skills",[["skills","Technical skills"]]],
    ["Projects",[["projects","Verified project experience"]]],
    ["Preferences",[["work_modes","Preferred work modes"],["target_categories","Target role categories"],["minimum_salary","Minimum salary preference"]]],
  ];
  const fields=groups.flatMap(([,items])=>items);
  async function refreshResumeFacts(){
    const response=await fetch("/api/v1/profile/facts?limit=200",{cache:"no-store"});
    if(response.ok){const data=await response.json();setResumeFacts(data.items||[])}
  }
  useEffect(()=>{if(mode!=="DEMO")Promise.all([fetch("/api/v1/profile",{cache:"no-store"}),fetch("/api/v1/profile/facts?limit=200",{cache:"no-store"})]).then(async([p,f])=>{
    if(p.ok){const value=await p.json();setFacts(value.facts||{});setFactStatus(value.fact_status||{});setConfigured(Boolean(value.configured))}
    if(f.ok){const value=await f.json();setResumeFacts(value.items||[])}
  }).catch(()=>notify("Profile could not be loaded"))},[mode,notify]);
  async function save(event:React.FormEvent<HTMLFormElement>){
    event.preventDefault();if(mode==="DEMO"){notify("Profile data is disabled in the public demo");return}
    const next={...facts,...Object.fromEntries(fields.map(([key])=>[key,String(facts[key]||"").trim()]))} as Record<string,string>;
    setSaving(true);try{const response=await fetch("/api/v1/profile",{method:configured?"PATCH":"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({facts:next})});
      if(!response.ok){notify("Profile was not saved; verify the owner session and try again");return}
      setFacts(next);setFactStatus(s=>({...s,...Object.fromEntries(Object.entries(next).filter(([,value])=>Boolean(value)).map(([key])=>[key,{verified:false,sensitivity:s[key]?.sensitivity||"NORMAL"}]))}));setConfigured(true);notify("Profile facts saved encrypted and left pending verification");
    }catch{notify("Profile was not saved")}finally{setSaving(false)}
  }
  async function verifyFact(key:string){
    const response=await fetch(`/api/v1/profile/facts/${encodeURIComponent(key)}/verify`,{method:"POST"});
    if(!response.ok){notify("This fact requires review or is not eligible for verification");return}
    setFactStatus(s=>({...s,[key]:{verified:true,sensitivity:"NORMAL"}}));notify("Fact verified for explainable matching");
  }
  async function reviewSelected(status:"RESUME_VERIFIED"|"OWNER_APPROVED"|"REJECTED"|"NEEDS_REVIEW"){
    if(selectedFacts.size===0)return;
    setFactBusy(true);try{const response=await fetch("/api/v1/profile/facts/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({fact_ids:[...selectedFacts],status})});
      if(!response.ok){notify("Review was rejected; check provenance and sensitivity for the selected facts");return}
      setSelectedFacts(new Set());await refreshResumeFacts();notify(`${selectedFacts.size} fact(s) moved to ${status.replaceAll("_"," ")}`)
    }catch{notify("Fact review could not be saved")}finally{setFactBusy(false)}
  }
  async function saveFactEdit(id:string){
    setFactBusy(true);try{const response=await fetch(`/api/v1/profile/facts/${id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:editedFactText})});
      if(!response.ok){notify("Fact edit was rejected");return}setEditingFact(null);setEditedFactText("");await refreshResumeFacts();notify("Edited fact saved and returned to review")
    }catch{notify("Fact edit could not be saved")}finally{setFactBusy(false)}
  }
  const visibleResumeFacts=resumeFacts.filter(item=>(factSection==="ALL"||item.section===factSection)&&(factStatusFilter==="ALL"||item.status===factStatusFilter)&&(factSensitivity==="ALL"||item.sensitivity===factSensitivity));
  return <><PageHeading eyebrow="VERIFIED CANDIDATE FACTS" title="My profile" subtitle="Only owner-reviewed facts can be used in applications." />
    <div className="profile-grid"><section className="panel profile-card"><div className="profile-avatar">PA</div><div><Badge tone="blue">{mode==="DEMO"?"SYNTHETIC PROFILE":"PRIVATE PROFILE"}</Badge><h2>{mode==="DEMO"?"Demo candidate":configured?(facts.full_name||"Candidate profile"):"Profile not configured"}</h2><p>{mode==="DEMO"?"No personal information appears in this public preview.":"Profile values are encrypted at rest. New facts remain unverified until reviewed."}</p></div><div className="profile-completeness"><div><span>Profile readiness</span><strong>{configured?"REVIEW":"SETUP"}</strong></div><small>Unknown or unreviewed details are not used for applications.</small></div></section>
      <section className="panel"><PanelTitle eyebrow="OWNER-ENTERED PROFILE" title="Candidate profile" /><form onSubmit={save} className="profile-edit-form">{groups.map(([group,items])=><fieldset className="profile-field-group" key={group}><legend>{group}</legend>{items.map(([key,label,sensitive])=><label key={key}>{label}{sensitive&&<small className="field-sensitivity">Sensitive · encrypted · never inferred</small>}{key==="skills"||key==="experience_summary"||key==="education"||key==="certifications"||key==="projects"?<textarea name={key} rows={3} value={facts[key]||""} onChange={e=>setFacts(s=>({...s,[key]:e.target.value}))} maxLength={5000} disabled={mode==="DEMO"}/>:<input name={key} value={facts[key]||""} onChange={e=>setFacts(s=>({...s,[key]:e.target.value}))} maxLength={5000} disabled={mode==="DEMO"}/>}</label>)}</fieldset>)}<button className="button button-primary" disabled={mode==="DEMO"||saving}>{saving?"Saving…":"Save encrypted facts"}</button></form></section>
    </div>{configured&&<section className="panel health-panel"><PanelTitle eyebrow="OWNER REVIEW" title="Fact verification" />{Object.entries(factStatus).map(([key,status])=><div className="health-row" key={key}><div><strong>{fields.find(([name])=>name===key)?.[1]||key.replaceAll("_"," ")}</strong><small>{status.sensitivity==="NORMAL"?"May be used by deterministic matching after owner verification":"Requires a separate sensitive-answer workflow"}</small></div><Badge tone={status.verified?"green":"amber"}>{status.verified?"VERIFIED":"PENDING REVIEW"}</Badge>{!status.verified&&status.sensitivity==="NORMAL"&&<button className="text-button" onClick={()=>verifyFact(key)}>Verify fact</button>}</div>)}</section>}<div className="notice notice-warn"><span className="notice-mark">ⓘ</span><div><strong>Sensitive and legal answers are kept separate</strong><p>Do not enter work authorization, immigration, health, protected-trait, clearance or salary-commitment answers here. They require a dedicated owner-approved answer workflow and are never inferred.</p></div></div>
    <section className="panel health-panel"><PanelTitle eyebrow="CANONICAL RESUME" title="Fact review queue" action={<Badge>{resumeFacts.length} facts</Badge>} /><p className="muted-note">Resume verified means the statement is present in the source document; it is not owner approval for sensitive declarations or autonomous applications.</p><div className="fact-review-filters"><label>Section<select value={factSection} onChange={e=>setFactSection(e.target.value)}><option value="ALL">All sections</option>{[...new Set(resumeFacts.map(x=>x.section))].sort().map(x=><option key={x}>{x}</option>)}</select></label><label>Status<select value={factStatusFilter} onChange={e=>setFactStatusFilter(e.target.value)}>{["ALL","NEEDS_REVIEW","RESUME_VERIFIED","OWNER_APPROVED","REJECTED","SUPERSEDED"].map(x=><option key={x}>{x}</option>)}</select></label><label>Sensitivity<select value={factSensitivity} onChange={e=>setFactSensitivity(e.target.value)}>{["ALL","NORMAL","SENSITIVE","LEGAL"].map(x=><option key={x}>{x}</option>)}</select></label></div><div className="fact-review-actions"><button className="small-control" disabled={factBusy||selectedFacts.size===0} onClick={()=>reviewSelected("RESUME_VERIFIED")}>Mark as resume evidence</button><button className="small-control" disabled={factBusy||selectedFacts.size===0} onClick={()=>reviewSelected("OWNER_APPROVED")}>Owner approve</button><button className="small-control" disabled={factBusy||selectedFacts.size===0} onClick={()=>reviewSelected("REJECTED")}>Reject</button><button className="small-control" disabled={factBusy||selectedFacts.size===0} onClick={()=>reviewSelected("NEEDS_REVIEW")}>Needs review</button></div><div className="resume-fact-list">{visibleResumeFacts.map(item=><article className="resume-fact-row" key={item.id}><input type="checkbox" aria-label={`Select fact from ${item.section}, page ${item.page}`} checked={selectedFacts.has(item.id)} onChange={e=>setSelectedFacts(current=>{const next=new Set(current);e.target.checked?next.add(item.id):next.delete(item.id);return next})}/><span><small>{item.section} · page {item.page} · {item.sensitivity} · confidence {Math.round(item.confidence*100)}%</small>{editingFact===item.id?<><textarea aria-label="Edit candidate fact" value={editedFactText} onChange={e=>setEditedFactText(e.target.value)} maxLength={5000}/><div><button className="small-control" disabled={factBusy} onClick={()=>saveFactEdit(item.id)}>Save edit</button><button className="small-control" disabled={factBusy} onClick={()=>setEditingFact(null)}>Cancel</button></div></>:<><strong>{item.text}</strong><small>Source SHA {String(item.provenance.source_sha256||"").slice(0,16)}… · {item.status.replaceAll("_"," ")}</small><button className="text-button" onClick={()=>{setEditingFact(item.id);setEditedFactText(item.text)}}>Edit fact</button></>}</span></article>)}{visibleResumeFacts.length===0&&<p className="muted-note">No candidate facts match these filters.</p>}</div></section>
  </>;
}

function ResumePage({mode,notify}:{mode:string;notify:(s:string)=>void}) {
  type ResumeDoc={id:string;version:string;label:string;category:string;sha256:string;size_bytes:number;page_count:number|null;canonical:boolean;active:boolean;imported_at:string};
  type GeneratedDoc={id:string;document_type:string;sha256:string;size_bytes:number;created_at:string;download_url:string;provenance?:{variant?:string|null}};
  const [items,setItems]=useState<ResumeDoc[]>([]);
  const [generated,setGenerated]=useState<GeneratedDoc[]>([]);
  const [busy,setBusy]=useState(false);
  const refresh=useCallback(async()=>{if(mode==="DEMO")return;const [r,d]=await Promise.all([fetch("/api/v1/resumes",{cache:"no-store"}),fetch("/api/v1/documents",{cache:"no-store"})]);if(r.ok){const value=await r.json();setItems(value.items||[])}if(d.ok){const value=await d.json();setGenerated(value.items||[]) }},[mode]);
  useEffect(()=>{if(mode==="DEMO")return;let current=true;Promise.all([fetch("/api/v1/resumes",{cache:"no-store"}),fetch("/api/v1/documents",{cache:"no-store"})]).then(async([r,d])=>{const [rv,dv]=await Promise.all([r.ok?r.json():null,d.ok?d.json():null]);if(current&&rv)setItems(rv.items||[]);if(current&&dv)setGenerated(dv.items||[])}).catch(()=>{if(current)notify("Private document list could not be loaded")});return()=>{current=false}},[mode,notify]);
  async function upload(event:React.ChangeEvent<HTMLInputElement>){
    const file=event.target.files?.[0];if(!file)return;
    if(mode==="DEMO"){notify("Uploads are disabled in the public demo");event.target.value="";return}
    const body=new FormData();body.set("file",file);setBusy(true);
    try{const response=await fetch("/api/v1/documents/resumes",{method:"POST",body});const result=await response.json();
      if(!response.ok){notify(result.detail||"Resume upload was rejected");return}
      await refresh();
      notify(result.canonical?"Canonical resume encrypted and imported":"Resume encrypted and imported");
    }catch{notify("Resume upload failed")}finally{setBusy(false);event.target.value=""}
  }
  async function generateVariant(variant:string){
    if(mode==="DEMO"){notify("Private resume generation is unavailable in demo mode");return}
    setBusy(true);try{const response=await fetch("/api/v1/documents/generate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:"ATS_RESUME",variant})});const result=await response.json();if(!response.ok){notify(result.detail||"Resume generation needs reviewed source facts");return}await refresh();notify(`${variant} variant created from verified source facts`)}catch{notify("Resume variant generation failed")}finally{setBusy(false)}
  }
  return <><PageHeading eyebrow="PRIVATE DOCUMENT LIBRARY" title="Resume manager" subtitle="PDF files are validated, encrypted and stored outside the public web root." action={<label className="button button-primary">{busy?"Importing…":"＋ Import resume"}<input type="file" accept="application/pdf,.pdf" hidden disabled={mode==="DEMO"||busy} onChange={upload}/></label>} />
    {mode==="DEMO"?<div className="resume-grid">{[["Resume A","Systems / Infrastructure","Synthetic preview"],["Resume B","L2 / Desktop / Technical","Not created"],["Resume C","L1 / Help Desk","Not created"],["Resume D","Cloud","Not created"]].map(([tag,label,state],i)=><article className="panel resume-card" key={tag}><div className={`resume-art resume-art-${i}`}><div className="paper"><span/><span/><span/><i/><span/><span/></div><div className="doc-lock">▣</div></div><div className="resume-details"><Badge>{state}</Badge><h2>{label}</h2><p>Synthetic demo only; no real resume is bundled.</p></div></article>)}</div>:<section className="panel table-panel"><div className="table-toolbar"><div><strong>Private resumes</strong><small>{items.length} encrypted document(s)</small></div></div><div className="audit-rows">{items.map(item=><div className="audit-row" key={item.id}><span>{item.canonical?"CANONICAL":"VERSION"}</span><code>{item.version}</code><span>{item.label} · {item.page_count??"?"} pages · {(item.size_bytes/1024).toFixed(0)} KB · SHA {item.sha256.slice(0,12)}…</span><Badge tone={item.active?"green":"neutral"}>{item.active?"ACTIVE":"SUPERSEDED"}</Badge><a className="text-button" href={`/api/v1/resumes/${item.id}/download`}>Download</a></div>)}{items.length===0&&<p className="muted-note">No private resume records are in the production database.</p>}</div><p className="muted-note">Private preview opens through the authenticated download route. Fact evidence and review status are managed in My Profile.</p></section>}
    {mode!=="DEMO"&&<><section className="panel"><PanelTitle eyebrow="PRIVATE VARIANTS" title="Targeted resume versions" />{[["A","Resume A","Systems / Infrastructure"],["B","Resume B","L2 / Desktop / Technical"],["C","Resume C","Help Desk / L1"],["D","Resume D","Cloud"]].map(([variant,version,label])=>{const match=generated.find(doc=>doc.document_type==="TAILORED_RESUME"&&doc.provenance?.variant===label);return <div className="health-row" key={version}><div><strong>{version} · {label}</strong><small>Only verbatim owner-reviewed or canonical resume facts are included.</small></div>{match?<><Badge tone="green">GENERATED</Badge><a className="text-button" href={match.download_url}>Download</a></>:<><Badge>NOT GENERATED</Badge><button className="button button-secondary" disabled={busy} onClick={()=>generateVariant(variant)}>Generate privately</button></>}</div>})}</section><section className="panel"><PanelTitle eyebrow="PRIVATE DOCUMENTS" title="Generated materials" />{generated.length?generated.map(doc=><div className="health-row" key={doc.id}><div><strong>{doc.document_type.replaceAll("_"," ")}</strong><small>{(doc.size_bytes/1024).toFixed(1)} KB · SHA {doc.sha256.slice(0,12)}…</small></div><a className="text-button" href={doc.download_url}>Download</a></div>):<p className="muted-note">Generated documents remain encrypted outside Git.</p>}</section></>}
    <div className="notice notice-warn"><span className="notice-mark">ⓘ</span><div><strong>Production backup is unavailable</strong><p>Canonical and generated resumes are private data and are not protected by a production backup. Backup remains DEFERRED / NOT IMPLEMENTED.</p></div></div>
  </>;
}

function IntegrationsPage({overview,notify}:{overview:Overview;notify:(s:string)=>void}){const items=[["Google Sheets","Existing tracker","Least-privilege OAuth · first-write snapshot required",overview.integrations.google_sheets||"NOT CONFIGURED","G"],["OpenAI","Document assistance","Optional provider · fake provider used in tests",overview.integrations.ai||"NOT CONFIGURED","✳"],["Email","Recruiter correspondence","Gmail OAuth · read-only until owner permits actions",overview.integrations.email||"NOT CONFIGURED","✉"],["Calendar","Interview & deadline visibility","Read-only import · no automatic accept/reschedule","NOT CONFIGURED","▦"]];return <><PageHeading eyebrow="CONNECTED SERVICES" title="Integrations" subtitle="Each connector uses a scoped authorization and a visible sync state. No credential is stored in source." /><div className="integration-list">{items.map(([name,sub,desc,state,icon])=><article className="panel integration-card" key={String(name)}><div className="integration-icon">{icon}</div><div className="integration-info"><h2>{name}<small>{sub}</small></h2><p>{desc}</p><div className="integration-state"><i className={state==="CONNECTED"?"live-dot":"state-dot"}/>{state}</div></div><button className="button button-secondary" onClick={()=>notify(`${name} authorization is not configured; no changes were made`)}>Configure</button></article>)}</div><section className="panel sheet-compat"><div><div className="eyebrow">EXISTING TRACKER</div><h2>Pratik Aeraple — IT Job Application Tracker</h2><p>Applications, Dashboard and Lists tabs remain the owner-facing view. Formula columns are preserved; a first-write snapshot must precede synchronization.</p></div><div className="compat-stats"><span><strong>32</strong> columns</span><span><strong>3</strong> preserved tabs</span><span><strong>2</strong> formula ranges</span></div></section></>}

function AnalyticsPage({overview}:{overview:Overview}){
  type Analytics={mode:string;available:boolean;small_sample:boolean;sample_size:number;jobs_by_week:{week:string;count:number}[];funnel:{stage:string;count:number}[];score_distribution:{band:string;count:number}[];source_performance:{source:string;discovered:number;applications:number;submitted:number}[];category_performance:{category:string;jobs:number;applications:number}[];province_performance:{province:string;applications:number}[]};
  const [data,setData]=useState<Analytics|null>(null);const [error,setError]=useState(false);
  useEffect(()=>{if(overview.mode!=="PRODUCTION")return;fetch("/api/v1/analytics",{cache:"no-store"}).then(async r=>{if(!r.ok){setError(true);return}setData(await r.json())}).catch(()=>setError(true))},[overview.mode]);
  const production=overview.mode==="PRODUCTION";const maxWeek=Math.max(1,...(data?.jobs_by_week||[]).map(x=>x.count));
  const funnel=data?.funnel||[];const discovered=funnel.find(x=>x.stage==="DISCOVERED")?.count||0;
  return <><PageHeading eyebrow="SEARCH SIGNALS" title="Analytics" subtitle="Counts come from recorded BruteApply history. Small samples are shown without conversion claims." />
    {!production?<div className="notice notice-demo"><span className="notice-mark">ⓘ</span><div><strong>Production analytics are not shown in demo mode</strong><p>This preview uses fictional example counts elsewhere; no trend or outcome data is presented as real.</p></div></div>:error?<div className="notice notice-warn"><span className="notice-mark">ⓘ</span><div><strong>Analytics are temporarily unavailable</strong><p>No sample data is substituted.</p></div></div>:<>
      <div className="notice notice-demo"><span className="notice-mark">ⓘ</span><div><strong>{data?.sample_size||0} application records</strong><p>{data?.small_sample?"Sample is too small for meaningful conversion comparisons.":"Rates are omitted until the underlying evidence supports them."}</p></div></div>
      <div className="analytics-grid"><section className="panel chart-panel"><PanelTitle eyebrow="DISCOVERY · LAST 12 WEEKS" title="Jobs discovered" /><div className="bar-chart" aria-label="Weekly discovered job counts">{data?.jobs_by_week.map(item=><i key={item.week} title={`${item.week}: ${item.count} jobs`} style={{height:`${Math.max(2,item.count/maxWeek*100)}%`}} />)}</div><p className="muted-note">Empty periods are omitted. Source volume is not an application success metric.</p></section><section className="panel"><PanelTitle eyebrow="WORKFLOW" title="Current funnel" />{funnel.map((item,index)=><div className="funnel-row" key={item.stage}><div><span>{item.stage.replaceAll("_"," ")}</span><strong>{item.count}</strong></div><div className="funnel-track"><i className={["bar-blue","bar-mint","bar-violet","bar-orange","bar-green"][index%5]} style={{width:`${Math.max(2,item.count/Math.max(1,discovered)*100)}%`}} /></div></div>)}</section></div>
      <div className="analytics-grid"><section className="panel"><PanelTitle eyebrow="MATCH EVIDENCE" title="Score distribution" />{data?.score_distribution.map(item=><div className="health-row" key={item.band}><strong>{item.band}</strong><span>{item.count} match records</span></div>)}{!data?.score_distribution.length&&<p className="muted-note">No match records yet.</p>}</section><section className="panel"><PanelTitle eyebrow="SOURCE PERFORMANCE" title="Discovery and applications" />{data?.source_performance.map(item=><div className="health-row" key={item.source}><div><strong>{item.source}</strong><small>{item.discovered} discovered · {item.applications} tracked</small></div><Badge>{item.submitted} verified submissions</Badge></div>)}{!data?.source_performance.length&&<p className="muted-note">No source history yet.</p>}</section></div>
      <div className="analytics-grid"><section className="panel"><PanelTitle eyebrow="ROLE CATEGORIES" title="Category activity" />{data?.category_performance.map(item=><div className="health-row" key={item.category}><strong>{item.category}</strong><span>{item.jobs} jobs · {item.applications} applications</span></div>)}</section><section className="panel"><PanelTitle eyebrow="LOCATION" title="Province activity" />{data?.province_performance.map(item=><div className="health-row" key={item.province}><strong>{item.province}</strong><span>{item.applications} applications</span></div>)}</section></div>
    </>}</>;
}

function AgentsPage({workers,control,emergency,mode}:{workers:Record<string,string>;control:(n:string,a:string)=>void;emergency:()=>void;mode:string}){const list=[["discovery","Find eligible roles","Permitted feeds and owner-imported jobs"],["matching","Score opportunities","Deterministic requirements and verified facts"],["documents","Prepare materials","Truthful, traceable documents"],["submission","Submit applications","OFF until owner activation gate"],["sheet_sync","Synchronize tracker","Preserve sheet formulas and detect conflicts"],["email","Process recruiter email","Untrusted content; ticket ambiguous messages"],["followups","Schedule follow-ups","Owner-approved policy only"]];return <><PageHeading eyebrow="AUTOMATION CONTROL" title="Agent control" subtitle="Workers are independently controlled. Stop submissions immediately without stopping discovery or review." action={<button className="button button-danger" onClick={emergency}>⏹ Emergency stop</button>} /><div className="control-banner"><span>◉</span><div><strong>Real autonomous submission is disabled</strong><p>{mode==="DEMO"?"Controls below update only this synthetic preview.":"The one-time owner activation checklist has not been completed."}</p></div><Badge tone="red">SUBMISSION OFF</Badge></div><div className="agent-list">{list.map(([id,title,desc])=><article className="panel agent-row" key={id}><span className={`agent-state-dot ${String(workers[id]||"").toLowerCase()}`} /><div className="agent-copy"><h2>{title}</h2><p>{desc}</p></div><Badge tone={id==="submission"?"red":workers[id]==="RUNNING"?"green":"neutral"}>{workers[id]||"READY"}</Badge><div className="agent-actions"><button className="small-control" onClick={()=>control(id,"START")} aria-label={`Start ${title}`}>Start</button><button className="small-control" onClick={()=>control(id,"PAUSE")} aria-label={`Pause ${title}`}>Pause</button><button className="small-control" onClick={()=>control(id,"DRAIN")} aria-label={`Drain ${title}`}>Drain</button></div></article>)}</div></>}

function AuditPage({mode}:{mode:string}){
  type Event={action:string;object_type:string;result:string;created_at:string};
  const [events,setEvents]=useState<Event[]>([]);
  useEffect(()=>{fetch("/api/v1/audit",{cache:"no-store"}).then(async r=>{if(r.ok){const data=await r.json();setEvents(data.items||[])}}).catch(()=>{})},[mode]);
  return <><PageHeading eyebrow="ACCOUNTABILITY" title="Audit log" subtitle="Important actions are append-only events. Private values and message bodies are excluded." /><section className="panel table-panel"><div className="table-toolbar"><div><strong>{mode==="DEMO"?"Synthetic activity":"Latest events"}</strong><small>Actor identities and object IDs are withheld from this view</small></div><Badge>{events.length} events</Badge></div><div className="audit-rows">{events.map((event,index)=><div className="audit-row" key={`${event.created_at}-${event.action}-${index}`}><time>{new Intl.DateTimeFormat("en-CA",{timeZone:"America/Toronto",hour:"2-digit",minute:"2-digit"}).format(new Date(event.created_at))}</time><code>{event.action}</code><span>{event.object_type}</span><Badge tone={event.result==="SYNTHETIC"?"neutral":"blue"}>{event.result||"RECORDED"}</Badge></div>)}{events.length===0&&<p className="muted-note">No recorded events yet.</p>}</div></section>{mode==="DEMO"&&<p className="muted-note">Demo events are illustrative only. No employer or application records are represented.</p>}</>;
}

function HealthPage({overview}:{overview:Overview}){const components=overview.components||{};const checks=[["Application API","Authenticated dashboard data source",overview.mode==="DEMO"?"DEMO":"UP"],["PostgreSQL","Database query result",components.database||"UNKNOWN"],["Redis queue","Broker ping",components.queue||"UNKNOWN"],["Background workers","Live worker heartbeat",components.worker||"UNKNOWN"],["Google Sheet sync","OAuth and first-write snapshot",overview.integrations.google_sheets||"NOT CONFIGURED"],["AI provider","Optional provider",overview.integrations.ai||"NOT CONFIGURED"],["Production backup",overview.backup.status,overview.backup.status]];return <><PageHeading eyebrow="OPERATIONS" title="System health" subtitle="Health reports distinguish configured, observed and unverified components." /><div className="health-banner"><div className="health-pulse">♡</div><div><Badge tone="amber">{overview.mode==="DEMO"?"DEMO MODE":"PRODUCTION API · PRIVATE OWNER VIEW"}</Badge><h2>{overview.mode==="DEMO"?"Synthetic local workspace":"Private application runtime"}</h2><p>{overview.mode==="DEMO"?"This local preview uses fictional records and has no production integrations.":"Component checks reflect the live private API. Worker heartbeat and external integrations remain UNKNOWN until separately instrumented."}</p></div></div><section className="panel health-panel"><PanelTitle eyebrow="COMPONENT STATUS" title="Dependencies & safeguards" />{checks.map(([n,d,s])=><div className="health-row" key={n}><span className="health-check-icon">{s==="UP"?"✓":"·"}</span><div><strong>{n}</strong><small>{d}</small></div><Badge tone={s==="UP"||s==="DEMO"?"green":String(s).includes("DEFERRED")?"red":"neutral"}>{s}</Badge></div>)}</section><section className="panel backup-panel"><div className="backup-symbol">▣</div><div><div className="eyebrow">DATA PROTECTION</div><h2>Production Backup</h2><p>{overview.backup.status}</p><small>Restore test: {overview.backup.restore_test}. Prepared BruteHost tooling is not an active production backup.</small></div><Badge tone="red">NOT PROTECTED</Badge></section></>}
