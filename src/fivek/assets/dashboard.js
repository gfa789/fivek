const BASE = document.querySelector('meta[name="fivek-base-path"]').content;
const screen = document.getElementById('screen');
const syncStatus = document.getElementById('sync-status');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateLabel = value => new Date(value + 'T12:00:00Z').toLocaleDateString('en-GB', {day:'numeric',month:'short',timeZone:'UTC'});
const time = seconds => seconds == null ? '—' : Math.floor(Math.round(seconds) / 60) + ':' + String(Math.round(seconds) % 60).padStart(2,'0');
const short = (text, length=100) => text?.length > length ? text.slice(0,length).replace(/\s+\S*$/,'') + '…' : text || '';
const volume = s => s.gym && s.kind!=='strength' ? (s.estimated_minutes||s.minutes||'Run')+' + '+s.gym.minutes+' min' : s.km ? s.km + ' km' : s.minutes ? s.minutes + ' min' : s.kind === 'football' ? 'Evening' : 'Recovery';
let report = null, etag = null, busy = false, loading = false, timer = null, activityRequest = 0;
const activityCache = new Map();
const metrics = {
  vo2_max:['VO₂ max','Garmin estimate'], resting_hr:['Resting heart rate','bpm'],
  hrv_ms:['Overnight HRV','ms'], sleep_hours:['Sleep','hours'], acute_load:['Acute load','Garmin units'],
  readiness:['Training readiness','0–100'], stress:['Stress','0–100'], body_battery_last:['Body battery','0–100'],
};
const metricKeys = Object.keys(metrics);
const recoveryLabel = status => ({normal:'No flags',unknown:'Unknown',reduce:'Take it easy',pause:'Pause impact'}[status] || 'Unknown');
const empty = text => '<p class="empty">' + esc(text) + '</p>';
const back = (href, label) => '<a class="back" href="' + href + '">← ' + esc(label) + '</a>';
function pager(index, count, href) {
  return '<div class="pager"><a aria-label="Previous page" aria-disabled="' + (index<=0) + '" href="' + href(Math.max(0,index-1)) + '">← Previous</a><span>' + (count ? index+1 : 0) + ' / ' + count + '</span><a aria-label="Next page" aria-disabled="' + (index>=count-1) + '" href="' + href(Math.min(count-1,index+1)) + '">Next →</a></div>';
}
function chunks(text) {
  // Keep all words available across pages instead of clipping detail paragraphs.
  const limit = innerWidth <= 760 ? (innerHeight < 720 ? 240 : 350) : 520;
  const result = []; let current = '';
  for (const word of String(text || '').split(/\s+/)) {
    if (current && current.length + word.length > limit) { result.push(current); current = ''; }
    current += (current ? ' ' : '') + word;
  }
  if (current) result.push(current);
  return result.length ? result : ['No further detail is available yet.'];
}
function textPage(title,label,text,parent,route,page=0,segments='') {
  return brief(title,label,text,parent,route,page,segments);
}
const sessionIcon = s => s.kind==='football'?'⚽':s.kind==='strength'?'▰':s.kind==='rest'?'◌':'↗';
const sessionTone = s => s.kind==='football'?'football':s.gym&&s.kind==='strength'?'strength':s.kind==='rest'?'rest':'run';
const sessionLink = s => '#workout/'+s.date+'/'+(s.kind==='strength'&&s.gym?'gym':'overview')+'/0';
const val = (number,digits=1) => Number.isFinite(number)?Number(number.toFixed(digits)):'—';
function head(title,parent='#training',subtitle='',action='') {
  return '<div class="page-head">'+(parent?'<a class="back" href="'+esc(parent)+'" aria-label="Back to overview">←</a>':'')+'<div><h1>'+esc(title)+'</h1>'+(subtitle?'<p class="note">'+esc(subtitle)+'</p>':'')+'</div>'+action+'</div>';
}
function facts(items) {
  return '<div class="fact-grid">'+items.map(([label,value])=>'<div class="fact"><span class="eyebrow">'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>').join('')+'</div>';
}
function instruction(title,text,tone='') {return '<article class="instruction '+tone+'"><h3>'+esc(title)+'</h3><p>'+esc(text)+'</p></article>';}
function sentences(text) {return String(text||'').split(/(?<=[.!?])\s+(?=[A-Z])/u).filter(Boolean);}
function brief(title,label,text,parent,route,page=0,segments='') {
  const all=chunks(text),perPage=innerWidth>900?4:2,count=Math.ceil(all.length/perPage),index=Math.max(0,Math.min(count-1,Number(page)||0));
  screen.innerHTML=head(title,parent,label)+segments+'<div class="instructions">'+all.slice(index*perPage,(index+1)*perPage).map((part,i)=>instruction(index===0&&i===0?'The takeaway':'What this means',part)).join('')+'</div>'+(count>1?pager(index,count,route):'');
}
function gymShortcuts() {
  return '<div class="gym-shortcuts">'+['A','B','C'].map(key=>{
    const s=report.plan.sessions.find(s=>s.gym&&(s.gym.programme_key||s.gym.name.match(/^Gym ([ABC])/u)?.[1])===key);
    return s?'<a data-gym-shortcut="'+key+'" href="#workout/'+s.date+'/gym/0" aria-label="Gym '+key+' exercise list"><span aria-hidden="true">▰</span> Gym '+key+'</a>':'';
  }).join('')+'</div>';
}
function dayRow(s) {
  return '<a class="day-row '+(s.date===report.as_of?'today':'')+'" href="'+sessionLink(s)+'"><span class="day-label">'+esc(s.weekday.slice(0,3))+' '+s.date.slice(8)+'</span><span class="day-mark '+sessionTone(s)+'" aria-hidden="true"></span><div class="day-content"><strong>'+esc(s.title)+'</strong><small>'+esc(volume(s))+'</small></div><span aria-hidden="true">→</span></a>';
}
function stages(s) {
  const steps=s.steps||[],work=steps.filter(v=>v.kind==='work'),recovery=steps.filter(v=>v.kind==='recovery');
  if(work.length&&work.every(v=>v.minutes===work[0].minutes&&v.effort===work[0].effort)) {
    return [{label:'Warm up',dose:steps[0].minutes+' min',note:steps[0].effort},
      {label:'Controlled repetitions',dose:work.length+' × '+work[0].minutes+' min',note:work[0].effort+(recovery.length?' · '+recovery[0].minutes+' min easy between reps':''),work:true},
      {label:'Cool down',dose:steps.at(-1).minutes+' min',note:steps.at(-1).effort}];
  }
  return steps.map(v=>({label:v.kind.replaceAll('_',' '),dose:v.minutes+' min',note:v.effort}));
}
function timeline(s) {
  return '<div class="timeline">'+stages(s).map((v,i)=>'<div class="stage '+(v.work?'work':'')+'"><span class="stage-index">'+(i+1)+'</span><div><h3>'+esc(v.label)+'</h3><p class="note">'+esc(v.note)+'</p></div><strong class="dose">'+esc(v.dose)+'</strong></div>').join('')+'</div>';
}
function workoutTabs(s,section) {
  const items=[['overview','Overview']];
  if(s.steps?.length) items.push(['steps','Run plan']);
  if(s.gym) items.push(['gym','Gym '+(s.gym.programme_key||'')]);
  items.push(['why','Coach']);
  return '<div class="segments">'+items.map(([key,label])=>'<a href="#workout/'+s.date+'/'+key+'/0" aria-current="'+((section===key||section.startsWith('gym')&&key==='gym'||section==='fuel'&&key==='why')?'page':'false')+'">'+label+'</a>').join('')+'</div>';
}
function plot(rows,key,label,{bar=false,unit='',seconds=false,second=null,timeline=false}={}) {
  const data=rows.filter(r=>Number.isFinite(r[key]));
  if(!data.length)return '<div class="plot">'+empty('No recorded '+label.toLowerCase()+' in this window.')+'</div>';
  const values=data.map(r=>r[key]).concat(second?rows.filter(r=>Number.isFinite(r[second])).map(r=>r[second]):[]);
  let low=bar?0:Math.min(...values),high=Math.max(...values);
  const pad=bar?Math.max(1,high*.08):Math.max((high-low)*.18,key.includes('weight')?.2:.5);
  high+=pad;if(!bar)low=Math.max(0,low-pad);if(high===low)high=low+1;
  if(['readiness','stress','body_battery_last'].includes(key)){low=0;high=100;}
  const stamps=rows.map((r,i)=>timeline?r.minutes:Date.parse((r.date||r.start)+'T12:00:00Z')||i),first=Math.min(...stamps),last=Math.max(...stamps);
  const x=i=>55+((stamps[i]-first)/Math.max(1,last-first))*400,y=value=>128-(value-low)/(high-low)*104;
  const format=n=>seconds?time(n):String(Number(n.toFixed(1)));
  let graphics='';
  if(bar) {
    const width=Math.min(42,320/rows.length);
    graphics=rows.map((r,i)=>Number.isFinite(r[key])?'<rect x="'+(x(i)-width/2)+'" y="'+y(r[key])+'" width="'+width+'" height="'+(128-y(r[key]))+'" rx="3" fill="'+(i===rows.length-1?'#264f3a':'#b1c68e')+'"><title>'+esc((r.date||r.start)+': '+format(r[key])+' '+unit)+'</title></rect>':'').join('');
  }else {
    for(const [channel,color] of [[key,'#264f3a'],...(second?[[second,'#6897c5']]:[])]) {
      let path='',connected=false,dots='';
      rows.forEach((r,i)=>{if(r.gap_before?.includes(channel))connected=false;if(!Number.isFinite(r[channel])){connected=false;return;}
        path+=(connected?'L':'M')+x(i).toFixed(1)+','+y(r[channel]).toFixed(1)+' ';connected=true;
        dots+='<circle cx="'+x(i)+'" cy="'+y(r[channel])+'" r="'+(rows.length<20?3:1.7)+'" fill="'+color+'"><title>'+esc((r.date||r.start)+': '+format(r[channel])+' '+unit)+'</title></circle>';
      });
      graphics+='<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="3" stroke-linejoin="round"/>'+dots;
    }
  }
  return '<div class="plot"><svg viewBox="0 0 480 164" preserveAspectRatio="xMidYMid meet" role="img" aria-label="'+esc(label)+'"><line x1="50" y1="24" x2="460" y2="24" stroke="#e6ebe2"/><line x1="50" y1="76" x2="460" y2="76" stroke="#e6ebe2"/><line x1="50" y1="128" x2="460" y2="128" stroke="#dfe6dc"/><text x="44" y="29" text-anchor="end" font-size="15" fill="#66756c">'+esc(format(high))+'</text><text x="44" y="132" text-anchor="end" font-size="15" fill="#66756c">'+esc(format(low))+'</text>'+graphics+'<text x="50" y="155" font-size="15" fill="#66756c">'+esc(timeline?val(rows[0].minutes)+' min':dateLabel(rows[0].date||rows[0].start))+'</text><text x="460" y="155" text-anchor="end" font-size="15" fill="#66756c">'+esc(timeline?val(rows.at(-1).minutes)+' min':dateLabel(rows.at(-1).date||rows.at(-1).start))+'</text></svg></div>';
}
function chartCard(title,value,unit,rows,key,href,options={},extra=false,caption='') {
  return '<a class="panel chart-card '+(extra?'desktop-extra':'')+'" href="'+href+'"><div class="chart-card-head"><div><h2>'+esc(title)+'</h2><span class="chart-unit">'+esc(unit)+'</span></div><span aria-hidden="true">↗</span></div><strong class="chart-value">'+esc(value)+'</strong>'+plot(rows,key,title,{unit,...options})+(options.second?'<div class="chart-legend"><span><i class="legend-dot"></i>Acute</span><span><i class="legend-dot secondary"></i>Chronic</span></div>':'')+'<p class="chart-caption">'+esc(caption||'Tap for the recorded readings')+'</p></a>';
}
function windowRows(rows,days) {
  const start=new Date(report.as_of+'T00:00:00Z');start.setUTCDate(start.getUTCDate()-days+1);
  return (rows||[]).filter(r=>(r.date||r.start)>=start.toISOString().slice(0,10)&&(r.date||r.start)<=report.as_of);
}
function lastValue(rows,key){return [...rows].reverse().find(r=>Number.isFinite(r[key]))?.[key];}

function training() {
  const p=report.plan,sessions=p.sessions.slice(0,7),today=sessions.find(s=>s.date===report.as_of)||sessions[0],tomorrow=sessions[1],a=report.metrics.athlete||{},latest=a.recovery?.latest||{};
  if(!today){screen.innerHTML=head('No upcoming workout',null,'Your saved goal may have passed.')+empty('There are no future sessions in the saved plan.');return;}
  const headline=today.kind==='rest'?'Recovery is today’s workout.':today.title;
  const ticker='<div class="mobile-week"><div class="section-label"><h3>Your week</h3><a class="button" href="#weeks/0">Full plan →</a></div><div class="week-ticker">'+sessions.map(s=>'<a class="'+(s.date===today.date?'today':'')+'" href="'+sessionLink(s)+'" aria-label="'+esc(s.weekday+' '+s.title)+'"><span>'+esc(s.weekday.slice(0,3))+'</span><span class="day-mark '+sessionTone(s)+'"></span></a>').join('')+'</div></div>';
  const component=today.gym&&today.kind!=='strength'?'<div class="component-actions"><a href="#workout/'+today.date+'/steps/0">Run plan <strong>'+esc(today.estimated_minutes||today.minutes||volume(today))+' min ↗</strong></a><a href="#workout/'+today.date+'/gym/0">Gym '+esc(today.gym.programme_key||'')+' <strong>'+today.gym.minutes+' min ↗</strong></a></div>':'';
  const summary=stages(today);
  const preview=plot(dailyRows('readiness',28),'readiness','Training readiness');
  screen.innerHTML='<div class="heading"><div><span class="eyebrow">'+esc(today.weekday+' · '+dateLabel(today.date))+'</span><h1>Today, made simple.</h1></div><span class="pill">'+Math.max(0,p.days_remaining)+' days to go</span></div><div class="today-layout"><div class="today-main"><section class="panel today-hero"><div class="hero-top"><span class="eyebrow">'+(today.status==='recorded'?'RECORDED TODAY':'YOUR NEXT SESSION')+'</span><span class="hero-icon" aria-hidden="true">'+sessionIcon(today)+'</span></div><h2>'+esc(headline)+'</h2><div class="hero-facts"><span>'+esc(volume(today))+'</span><span>'+esc(today.effort)+'</span>'+(today.gym?'<span>'+today.gym.exercises.length+' exercises</span>':'')+'</div><p class="note">'+esc(short(today.next_action||today.details,120))+'</p><a class="button primary today-cta" data-today-workout href="'+sessionLink(today)+'">Open today’s workout <span aria-hidden="true">→</span></a>'+component+'</section><div class="panel readiness-strip"><div><span>Readiness</span><strong>'+esc(latest.readiness??'—')+'<small> /100</small></strong></div><div><span>Last sleep</span><strong>'+esc(val(latest.sleep_hours))+'<small> h</small></strong></div><div><span>Your recovery</span><strong>'+esc(recoveryLabel(report.metrics.recovery.status))+'</strong></div></div><section class="panel today-preview"><div class="section-label"><h2>Readiness over 28 days</h2><a class="button" href="#recovery/0">Recovery detail →</a></div>'+preview+'</section></div><div class="today-side">'+(tomorrow?'<a class="panel next-up mobile-next" href="'+sessionLink(tomorrow)+'"><div class="date-box">'+esc(tomorrow.weekday.slice(0,3))+'<strong>'+tomorrow.date.slice(8)+'</strong></div><div><span class="eyebrow">Up next · tomorrow</span><h3>'+esc(tomorrow.title)+'</h3><span class="note">'+esc(volume(tomorrow))+' · '+esc(tomorrow.effort)+'</span></div><span aria-hidden="true">→</span></a>':'')+'<section class="plan-mini"><div class="section-label"><h2>Your seven days</h2><a class="button" href="#weeks/0">Plan →</a></div>'+sessions.map(dayRow).join('')+'</section>'+ticker+gymShortcuts()+'</div></div>';
}

function workout(date,section='overview',page=0) {
  const s=report.plan.sessions.find(s=>s.date===date);
  if(!s)return brief('Session unavailable','Saved plan','This session is no longer in your current plan. Return to Today for the latest workout.','#training',()=> '#training');
  const tabs=workoutTabs(s,section),header=head(s.title,'#training',dateLabel(s.date)+' · '+volume(s));
  if(s.gym&&section==='gym')return gymPage(s,page,tabs);
  if(s.gym&&section==='gym-exercise')return gymExercise(s,page);
  if(s.gym&&section==='gym-progress'){
    const cards=[['Build up to the work sets','Use ramp-up sets, then choose a familiar submaximal weight. '+s.gym.effort],['Earn the next increase','When every prescribed working set meets its reps with at least two reps in reserve and sound technique, add the smallest available increment next exposure (about 2–5%).'],['If the reps are not there','Repeat the load after missed reps. After two difficult exposures, reduce the load 5–10% and rebuild. Do not use old personal records as today’s starting weight.'],['Keep the whole week working',s.gym.adjustment]];
    const per=innerWidth>900?4:2,count=Math.ceil(cards.length/per),i=Math.max(0,Math.min(count-1,Number(page)||0));
    screen.innerHTML=head('Working weights','#workout/'+date+'/gym/0',s.gym.name)+'<div class="instructions">'+cards.slice(i*per,(i+1)*per).map(([title,text],n)=>'<article class="instruction"><span class="badge-number">0'+(i*per+n+1)+'</span><h3>'+title+'</h3><p>'+esc(text)+'</p></article>').join('')+'</div>'+pager(i,count,n=>'#workout/'+date+'/gym-progress/'+n);return;
  }
  if(section==='why'||section==='fuel'){
    const rawInfo=section==='fuel'?[['Before training','Have a carbohydrate-containing meal or snack and arrive hydrated.'],['After training','Have a normal meal with carbohydrate and protein. Eat and drink between double sessions.'],['Your fuelling instruction',s.fuel||'Eat to support training and recovery.']]:[['Why this session',(s.decision_reasons||[]).join(' ')||s.readiness_note||'This session fits your running, strength and football week.'],['What changes the plan',report.metrics.recovery.flags.length?report.metrics.recovery.flags.join('. '):'No current recovery flags are recorded. Effort and how you feel still matter.'],['After today',s.next_action||s.readiness_note||'Keep the next session as planned if recovered; do not catch up missed work.']];
    const info=rawInfo.flatMap(([title,text])=>chunks(text).map((part,i)=>[i?title+' · continued':title,part]));
    const per=innerWidth>900?3:2,count=Math.ceil(info.length/per),i=Math.max(0,Math.min(count-1,Number(page)||0));
    screen.innerHTML=header+tabs+'<div class="segments"><a href="#workout/'+date+'/why/0" aria-current="'+(section==='why'?'page':'false')+'">Session coaching</a><a href="#workout/'+date+'/fuel/0" aria-current="'+(section==='fuel'?'page':'false')+'">Fuelling</a></div><div class="instructions">'+info.slice(i*per,(i+1)*per).map(([t,v])=>instruction(t,v)).join('')+'</div>'+pager(i,count,n=>'#workout/'+date+'/'+section+'/'+n);return;
  }
  const isRun=!!s.steps?.length;
  screen.dataset.workoutKind=isRun?'run':s.kind;
  const primary=isRun?'<div class="section-label"><h2>Your run plan</h2><span class="pill">'+esc(s.estimated_minutes||s.minutes||'—')+' min total</span></div>'+timeline(s):instruction(s.kind==='rest'?'Today’s priority':'The session',s.details,'action');
  const gymLink=s.gym?'<a class="button dark" href="#workout/'+date+'/gym/0">'+esc(s.gym.name)+' · '+s.gym.exercises.length+' exercises →</a>':'';
  screen.innerHTML=header+tabs+'<div class="workout-layout"><section class="workout-primary">'+facts([['Duration',s.total_minutes?s.total_minutes+' min':volume(s)],['Effort',s.kind==='easy'?'RPE 2–3':s.effort],['Status',s.status==='recorded'?'Recorded':'Planned']])+primary+gymLink+'</section><aside class="workout-aside">'+instruction('Make this session count',isRun?'Warm up first, follow the work and recoveries, and keep the cool-down. '+(s.pace_range_seconds_km?'Easy pace starting range '+s.pace_range_seconds_km.map(time).join('–')+'/km; conversational effort takes priority.':'Use the prescribed effort, not a new personal best.'):(s.kind==='football'?(s.effort==='hard'?'This is a hard intermittent session in your week.':'Keep the planned match moderate; limit all-out sprints and shorten it if tired.'):'Everyday easy walking is fine. Keep the day free of catch-up training.'))+instruction('Recovery check',short(s.next_action||s.readiness_note||'Check soreness before starting. Shorten or stop the session if pain, unusual fatigue or poor recovery changes how you move.',240),'secondary-coach')+'<a class="button" href="#workout/'+date+'/why/0">Why this session? →</a></aside></div>';
}

function gymPage(s,page,segments) {
  const gym=s.gym,per=innerWidth<=900?(innerHeight<700?5:6):8,count=Math.ceil(gym.exercises.length/per),i=Math.max(0,Math.min(count-1,Number(page)||0));
  screen.innerHTML=head(gym.name,'#training',dateLabel(s.date)+' · '+gym.minutes+' min'+(gym.optional?' · optional third session':''))+segments+'<section class="gym-body"><div class="session-banner"><p>'+esc(gym.effort)+'</p></div><div class="exercise-list">'+gym.exercises.slice(i*per,(i+1)*per).map((e,n)=>'<a class="exercise-row" href="#workout/'+s.date+'/gym-exercise/'+(i*per+n)+'"><span class="exercise-number">'+(i*per+n+1)+'</span><div><strong>'+esc(e.name)+'</strong><span class="exercise-dose">'+e.sets+' sets × '+esc(e.reps)+' · rest '+(e.rest_seconds%60?e.rest_seconds+' sec':e.rest_seconds/60+' min')+'</span></div><span class="exercise-arrow" aria-hidden="true">→</span></a>').join('')+'</div><p class="footer-note">Working sets shown. Warm-up / ramp-up sets are additional.</p><div class="gym-footer">'+(count>1?pager(i,count,n=>'#workout/'+s.date+'/gym/'+n):'')+'<a class="button dark gym-progress" href="#workout/'+s.date+'/gym-progress/0">Working weights & progression →</a></div></section>';
}

function gymExercise(s,index=0) {
  const g=s.gym,i=Math.max(0,Math.min(g.exercises.length-1,Number(index)||0)),e=g.exercises[i],per=innerWidth<=900?(innerHeight<700?5:6):8;
  screen.innerHTML=head(e.name,'#workout/'+s.date+'/gym/'+Math.floor(i/per),g.name)+'<div class="session-banner"><strong>'+esc(e.reps)+' per set</strong><span class="pill">Rest '+e.rest_seconds/60+' min</span></div><div><span class="eyebrow">Planned working sets</span><div class="set-strip">'+Array.from({length:e.sets},(_,n)=>'<div class="set-box"><span>SET '+(n+1)+'</span><strong>'+esc(e.reps)+'</strong><span>reps'+(e.reps.includes('sec')?' / timed hold':'')+'</span></div>').join('')+'</div></div><div class="two-column"><div class="instruction action"><h3>Technique focus</h3><p>'+esc(e.cue)+'</p><div class="cue-visual" aria-label="Use ramp-up sets before working sets"><i class="rep-block"></i><i class="rep-block"></i><i class="rep-block"></i><span>Ramp up first.<br>These are work sets, not warm-ups.</span></div></div><div class="instruction"><h3>Choose your working weight</h3><p>'+esc(g.effort)+'. Use a current submaximal load, not your old personal record.</p><a class="button" href="#workout/'+s.date+'/gym-progress/0">Load progression →</a></div></div><div class="pager"><a href="#workout/'+s.date+'/gym-exercise/'+Math.max(0,i-1)+'" aria-disabled="'+(i===0)+'">← Exercise</a><span>'+(i+1)+' / '+g.exercises.length+'</span><a href="#workout/'+s.date+'/gym-exercise/'+Math.min(g.exercises.length-1,i+1)+'" aria-disabled="'+(i===g.exercises.length-1)+'">Exercise →</a></div>';
}

const sportMatch = (a,sport) => sport==='all' || (sport==='strength'?a.sport==='strength_training':a.kind===sport);
function recentActivities() {
  const start=new Date(report.as_of+'T00:00:00Z'); start.setUTCDate(start.getUTCDate()-27);
  return report.metrics.activities.filter(a=>a.date>=start.toISOString().slice(0,10)&&a.date<=report.as_of);
}
function recent() {trends('workload',28);}

function insight(index=0,page=0) {
  const values=report.metrics.insights, i=Math.max(0,Math.min(values.length-1,Number(index)||0)), value=values[i];
  if(!value) return textPage('Training insights','Saved data','There are no insights yet. The next background import will update this view.','#recent',n=>'#recent');
  const segments='<div class="segments"><a href="#insight/'+Math.max(0,i-1)+'/0">← Finding</a><span class="pill">'+(i+1)+' / '+values.length+'</span><a href="#insight/'+Math.min(values.length-1,i+1)+'/0">Finding →</a></div>';
  textPage(value.title,'Training evidence',value.detail+' Evidence: '+value.evidence,'#recent',n=>'#insight/'+i+'/'+n,page,segments);
}
function programme(page=0) {
  const p=report.plan, progression=p.progression;
  const text='Goal: your best achievable 5K by '+dateLabel(p.target_date)+' '+p.target_date.slice(0,4)+'. '+p.strategy+' '+(progression?progression.title+'. '+progression.action+' Taper begins '+dateLabel(progression.taper_start)+'. ':'')+p.rules.join(' ')+' '+(progression?.milestones||[]).map(m=>m.title+': '+m.detail).join(' ');
  textPage('The road to '+dateLabel(p.target_date),'Consistency → build → 5K specific → taper',text,'#training',n=>'#programme/'+n,page);
}
function activities(sport='all',page=0) {
  const rows=recentActivities().filter(a=>sportMatch(a,sport)).sort((a,b)=>b.date.localeCompare(a.date));
  const perPage=innerHeight<720?4:5, count=Math.ceil(rows.length/perPage), index=Math.max(0,Math.min(count-1,Number(page)||0));
  screen.innerHTML='<div class="detail-head">'+back('#recent','Recent workload')+'<span class="eyebrow">Recorded · last 28 days</span><h1>'+esc({run:'Running',strength:'Strength',football:'Football',all:'All activities'}[sport]||'Activities')+'</h1></div><div class="rows">'+rows.slice(index*perPage,(index+1)*perPage).map(a=>'<a class="row" href="#activity/'+esc(a.id)+'/0/'+sport+'/'+index+'"><div><strong>'+esc(short(a.name,55))+'</strong><span class="note">'+esc(dateLabel(a.date)+' · '+a.sport.replaceAll('_',' '))+'</span></div><span>'+esc(a.minutes==null?'—':Math.round(a.minutes)+' min')+' ↗</span></a>').join('')+(!rows.length?empty('No recorded sessions in this window.'):'')+'</div>'+pager(index,count,n=>'#activities/'+sport+'/'+n)+'<p class="footer-note">Missing recordings are not counted as completed training.</p>';
}
async function activity(id,view='heart_rate',sport='all',listPage=0,detailPage=0) {
  const request=++activityRequest,parent='#activities/'+sport+'/'+listPage,saved=report.metrics.activities.find(a=>String(a.id)===id);
  screen.innerHTML=head(saved?.name||'Recorded workout',parent,'Loading saved Garmin detail…');
  try {
    if(!activityCache.has(id)) {
      const response=await fetch(BASE+'api/activities/'+encodeURIComponent(id));if(!response.ok)throw Error('Unavailable');
      activityCache.set(id,await response.json());
    }
    if(request!==activityRequest||!location.hash.startsWith('#activity/'+id+'/'))return;
    const detail=activityCache.get(id),a=detail.activity||saved||{},key=view==='pace_seconds_km'?'pace_seconds_km':'heart_rate';
    const tabs='<div class="segments">'+[['heart_rate','Heart rate'],['pace_seconds_km','Pace'],['laps','Splits']].map(([k,label])=>'<a href="#activity/'+id+'/'+k+'/'+sport+'/'+listPage+'/0" aria-current="'+((view==='laps'?k==='laps':key===k)?'page':'false')+'">'+label+'</a>').join('')+'</div>';
    const heading=head(a.name||'Recorded workout',parent,(a.date?dateLabel(a.date)+' · ':'')+(a.sport||'Garmin activity').replaceAll('_',' '));
    const numbers=facts([['Distance',a.km==null?'—':val(a.km,2)+' km'],['Duration',a.minutes==null?'—':val(a.minutes,0)+' min'],['Average HR',a.average_hr==null?'—':val(a.average_hr,0)+' bpm']]);
    if(view==='laps') {
      const rows=detail.laps||[],per=innerWidth<=900?4:6,count=Math.ceil(rows.length/per),i=Math.max(0,Math.min(count-1,Number(detailPage)||0));
      screen.innerHTML=heading+numbers+tabs+'<div class="rows">'+rows.slice(i*per,(i+1)*per).map(l=>'<div class="row"><div><strong>Split '+l.index+' · '+esc(l.distance_km??'—')+' km</strong><span class="note">'+esc(l.average_hr??'—')+' bpm · '+(l.pace_seconds_km?time(l.pace_seconds_km)+'/km':'pace unavailable')+'</span></div><strong>'+time(l.seconds)+'</strong></div>').join('')+(!rows.length?empty('No split readings have been imported yet.'):'')+'</div>'+pager(i,count,n=>'#activity/'+id+'/laps/'+sport+'/'+listPage+'/'+n);return;
    }
    screen.innerHTML=heading+numbers+tabs+'<section class="panel detail-chart"><h2>'+(key==='heart_rate'?'Heart rate through the workout':'Pace through the workout')+'</h2>'+plot(detail.samples||[],key,key==='heart_rate'?'Workout heart rate':'Workout pace',{timeline:true,unit:key==='heart_rate'?'bpm':'per km',seconds:key==='pace_seconds_km'})+'</section><p class="footer-note">Actual saved samples; gaps remain gaps. '+(detail.display_sample_count??0)+' displayed samples. This does not fetch data from Garmin.</p>';
  }catch(_) {
    if(request===activityRequest)screen.innerHTML=head(saved?.name||'Recorded workout',parent,'Saved activity')+instruction('Detailed samples are not available yet','The recorded session is retained. Missing detail is filled by the next background import; opening this page does not request it from Garmin.');
  }
}
function dailyRows(key,days) {
  const start=new Date(report.as_of+'T00:00:00Z'); start.setUTCDate(start.getUTCDate()-days+1);
  return report.metrics.daily.filter(d=>d.date>=start.toISOString().slice(0,10)&&d.date<=report.as_of);
}
function trends(category='performance',range=90) {
  if(!['performance','recovery','body','workload'].includes(category))category='performance';
  const days=[28,90].includes(Number(range))?Number(range):90,a=report.metrics.athlete||{},f=a.performance||{},body=a.body||{},r=a.recovery||{},latest=r.latest||{};
  const weeks=(report.metrics.history_weeks||report.metrics.weeks).slice(-(days===28?4:13)),daily=windowRows(report.metrics.daily,days),races=windowRows(f.history,days),weights=windowRows(body.readings,days);
  let cards='';
  if(category==='performance')cards=chartCard('Garmin 5K estimate',f.available?time(f.current_seconds):'—','minutes · lower is faster',races,'seconds','#forecast/0',{seconds:true},false,'Watch estimate, not a recorded race')+chartCard('Running volume',val(weeks.at(-1)?.run_km)+' km','recorded km per seven-day window',weeks,'run_km','#metric/run_km/26/0',{bar:true})+chartCard('VO₂ max',val(lastValue(daily,'vo2_max')),'Garmin estimate',daily,'vo2_max','#metric/vo2_max/90/0',{},true)+chartCard('Body mass',val(body.latest?.weight_kg)+' kg','recorded scale readings',weights,'weight_kg','#body/0',{},true);
  else if(category==='recovery')cards=chartCard('Overnight HRV',val(lastValue(daily,'hrv_ms'))+' ms','recorded overnight average',daily,'hrv_ms','#metric/hrv_ms/'+days+'/0')+chartCard('Sleep duration',val(lastValue(daily,'sleep_hours'))+' h','recorded nights only',daily,'sleep_hours','#metric/sleep_hours/'+days+'/0',{bar:true})+chartCard('Resting heart rate',val(lastValue(daily,'resting_hr'))+' bpm','Garmin daily reading',daily,'resting_hr','#metric/resting_hr/'+days+'/0',{},true)+chartCard('Garmin load',val(latest.acute_load,0),'acute and chronic device units',daily,'acute_load','#recovery/0',{second:'chronic_load'},true);
  else if(category==='body')cards=chartCard('Body mass',val(body.latest?.weight_kg)+' kg','recorded scale readings',weights,'weight_kg','#body/0',{},false,'Only recorded measurements are plotted')+chartCard('Body fat estimate',val(body.latest?.body_fat_pct)+'%','scale estimate · hydration-sensitive',weights,'body_fat_pct','#body/0')+chartCard('Muscle mass estimate',val(body.latest?.muscle_kg)+' kg','scale estimate',weights,'muscle_kg','#body/0',{},true)+chartCard('Body water estimate',val(body.latest?.body_water_pct)+'%','scale estimate',weights,'body_water_pct','#body/0',{},true);
  else cards=chartCard('Running volume',val(weeks.at(-1)?.run_km)+' km','recorded km per seven-day window',weeks,'run_km','#metric/run_km/26/0',{bar:true})+chartCard('Garmin training load',val(latest.acute_load,0),'acute and chronic device units',daily,'acute_load','#recovery/0',{second:'chronic_load'})+chartCard('Running frequency',val(weeks.at(-1)?.run_count,0)+' runs','recorded runs per seven-day window',weeks,'run_count','#activities/run/0',{bar:true},true)+chartCard('Training readiness',val(latest.readiness,0)+'/100','Garmin daily reading',daily,'readiness','#metric/readiness/'+days+'/0',{},true);
  screen.innerHTML='<div class="insight-top"><div><span class="eyebrow">Your data, in context</span><h1>Insights</h1></div><div class="range-switch">'+[28,90].map(n=>'<a href="#trends/'+category+'/'+n+'" aria-current="'+(n===days?'page':'false')+'">'+n+'D</a>').join('')+'</div></div><div class="segments">'+[['performance','Fitness'],['recovery','Recovery'],['body','Body'],['workload','Workload']].map(([key,label])=>'<a href="#trends/'+key+'/'+days+'" aria-current="'+(key===category?'page':'false')+'">'+label+'</a>').join('')+'</div><div class="insights-summary">'+[['5K estimate',f.available?time(f.current_seconds):'—'],['January scenario',f.available?time(f.january_seconds):'—'],['Readiness',val(latest.readiness,0)+'/100'],['Scale',val(body.latest?.weight_kg)+' kg']].map(([label,value])=>'<div><span class="eyebrow">'+label+'</span><strong>'+esc(value)+'</strong></div>').join('')+'</div><div class="insight-grid">'+cards+'</div><div class="insight-footer"><a class="button" href="#activities/all/0">Recorded activities →</a><a class="button" href="'+(category==='recovery'?'#recovery/0':category==='body'?'#body/0':'#forecast/0')+'">'+(category==='recovery'?'Recovery context':category==='body'?'Target & context':'January outlook')+' →</a></div>';
}

function forecast(section='chart',page=0) {
  const f=report.metrics.athlete?.performance||{};
  if(!f.available)return brief('5K outlook','Saved data',f.note||'No current prediction is available.','#trends',n=>'#forecast/'+n);
  if(section==='method')return brief('How to read the outlook','Planning assumptions',f.assumptions+' '+f.method+' '+f.pace_note,'#forecast/0',n=>'#forecast/method/'+n,page);
  screen.innerHTML=head('Your 5K outlook','#trends','Watch estimate and conditional January scenario')+'<div class="compare-values"><div><span class="eyebrow">Garmin now</span><strong>'+time(f.current_seconds)+'</strong><p class="note">'+esc(f.source)+' · '+esc(dateLabel(f.source_date))+'</p></div><div><span class="eyebrow">31 January scenario</span><strong>'+time(f.january_seconds)+'</strong><p class="note">'+f.january_range_seconds.map(time).join('–')+' planning range</p></div></div><section class="panel detail-chart"><div class="section-label"><h2>Your watch estimate over time</h2><span class="pill">Lower is faster</span></div>'+plot(windowRows(f.history,90),'seconds','Garmin 5K prediction',{seconds:true})+'<p class="chart-caption">Historical watch estimates. January is a scenario, not a measured result.</p></section>'+instruction('What needs to happen','Consistent prescribed running, strength within recovery capacity, and a taper. The January scenario has low confidence and does not include an automatic weight-loss bonus.')+'<a class="button" href="#forecast/method/0">Assumptions, method & limitations →</a>';
}

function recovery(page=0) {
  const r=report.metrics.athlete?.recovery||{},l=r.latest||{},score=Number.isFinite(l.readiness)?Math.max(0,Math.min(100,l.readiness)):null,daily=windowRows(report.metrics.daily,28),flags=report.metrics.recovery.flags;
  const gauge='<svg class="gauge" viewBox="0 0 100 100" role="img" aria-label="Readiness '+(score??'unavailable')+' out of 100"><circle cx="50" cy="50" r="40" fill="none" stroke="#e7edde" stroke-width="9"/><circle cx="50" cy="50" r="40" fill="none" stroke="#7b9e55" stroke-width="9" stroke-dasharray="'+((score||0)/100*251.3)+' 251.3" transform="rotate(-90 50 50)"/><text x="50" y="55" text-anchor="middle" font-size="26" font-weight="700" fill="#152c22">'+(score??'—')+'</text><text x="50" y="70" text-anchor="middle" font-size="9" fill="#66756c">READINESS</text></svg>';
  screen.innerHTML=head('Recovery, explained','#trends/recovery/28','Latest useful watch date '+(l.date?dateLabel(l.date):'unknown'))+'<section class="panel recovery-score">'+gauge+'<div><h2>'+esc(recoveryLabel(report.metrics.recovery.status))+'</h2><p class="note">'+esc(l.training_status||'No Garmin status')+' · '+(flags.length?flags.length+' signals to review':'No active recovery flags recorded')+'</p></div></section>'+facts([['Sleep vs baseline',val(r.sleep?.recent)+' / '+val(r.sleep?.baseline)+' h'],['HRV vs baseline',val(r.hrv?.recent,0)+' / '+val(r.hrv?.baseline,0)],['RHR vs baseline',val(r.resting_hr?.recent,0)+' / '+val(r.resting_hr?.baseline,0)]])+'<section class="panel detail-chart"><h2>Acute and chronic load</h2>'+plot(daily,'acute_load','Garmin training load',{second:'chronic_load'})+'<div class="chart-legend"><span><i class="legend-dot"></i>Acute</span><span><i class="legend-dot secondary"></i>Chronic</span></div></section><p class="footer-note">'+esc(flags.length?short(flags.join('. '),160):'Baselines exclude the latest seven days. Device load is context, not an injury-risk threshold.')+'</p>';
}

function body(section=0,page=0) {
  const b=report.metrics.athlete?.body||{},l=b.latest||{};
  if(!b.available)return brief('Body composition','Saved scale data',b.note||'No scale readings yet.','#trends/body/90',n=>'#body/'+n);
  if(section==='context')return brief('Weight & performance','Your body-mass approach',b.target_kind+'. '+b.note,'#body/0',n=>'#body/context/'+n,page);
  screen.innerHTML=head('Body composition','#trends/body/90','Latest scale reading '+dateLabel(l.date))+facts([['Body mass',val(l.weight_kg)+' kg'],['Body fat',val(l.body_fat_pct)+'%'],['Muscle estimate',val(l.muscle_kg)+' kg']])+'<section class="panel detail-chart"><div class="section-label"><h2>Recorded body mass</h2><span class="pill">'+(b.seven_day_kg?val(b.seven_day_kg)+' kg · 7-day mean':'No recent mean')+'</span></div>'+plot(windowRows(b.readings,90),'weight_kg','Body mass',{unit:'kg'})+'<p class="chart-caption">Scale composition estimates are hydration-sensitive.</p></section>'+instruction('Your current approach',(b.target_range_kg?b.target_range_kg.join('–')+' kg review range. ':'')+b.target_kind+'. Protect strength, recovery and training quality.','action')+'<div class="session-links"><a class="button" href="#body-readings/0">Measurements →</a><a class="button" href="#body/context/0">Target context →</a></div>';
}

function bodyReadings(page=0) {
  const rows=[...(report.metrics.athlete?.body?.readings||[])].reverse(),perPage=innerHeight<720?4:5,count=Math.ceil(rows.length/perPage),index=Math.max(0,Math.min(count-1,Number(page)||0));
  screen.innerHTML='<div class="detail-head">'+back('#body/0','Body composition')+'<span class="eyebrow">Recorded scale measurements</span><h1>Weight trend</h1></div><div class="rows">'+rows.slice(index*perPage,(index+1)*perPage).map(r=>'<div class="row"><div><strong>'+esc(dateLabel(r.date))+'</strong><span class="note">Body fat estimate '+esc(r.body_fat_pct??'—')+'%</span></div><strong>'+r.weight_kg.toFixed(2)+' kg</strong></div>').join('')+(!rows.length?empty('No scale readings yet.'):'')+'</div>'+pager(index,count,n=>'#body-readings/'+n);
}
function weeks(page=0) {
  const values=report.plan.weeks,i=Math.max(0,Math.min(values.length-1,Number(page)||0)),w=values[i];
  if(!w)return programme();
  const end=new Date(w.start+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+6);
  const rows=report.plan.sessions.filter(s=>s.date>=w.start&&s.date<=end.toISOString().slice(0,10));
  screen.innerHTML=head('Week of '+dateLabel(w.start),'#training',w.phase+' · '+w.training_days+' training days · '+w.gym_sessions+' gym exposures')+'<div class="plan-layout"><div class="week-desktop">'+rows.map(s=>'<article class="week-column"><span class="eyebrow">'+esc(s.weekday)+'</span><strong class="week-kind" aria-hidden="true">'+sessionIcon(s)+'</strong><h3>'+esc(s.title)+'</h3><span class="pill">'+esc(volume(s))+'</span><p class="note">'+esc(short(s.details,150))+'</p>'+(s.gym?'<p class="week-meta">'+s.gym.exercises.length+' gym exercises</p>':'')+'<a class="button" href="'+sessionLink(s)+'">Open workout →</a></article>').join('')+'</div><div class="forecast-week">'+rows.map(dayRow).join('')+'</div>'+pager(i,values.length,n=>'#weeks/'+n)+'<div class="plan-note"><p>'+esc(w.conditional?'Future sessions adapt to recorded training and recovery.':'Current week · follow the stated session effort, not catch-up mileage.')+'</p><a class="button" href="#programme/0">Progression rules →</a></div></div>';
}

function metric(key='vo2_max',range=90,page=0) {
  if(key!=='run_km'&&!metrics[key])key='vo2_max';
  const running=key==='run_km',days=running?([8,26].includes(Number(range))?Number(range):26):([14,28,90].includes(Number(range))?Number(range):90),rows=running?(report.metrics.history_weeks||report.metrics.weeks).slice(-days):dailyRows(key,days),label=running?'Running volume':metrics[key][0],unit=running?'km / week':metrics[key][1];
  const values=rows.filter(r=>Number.isFinite(r[key])),last=values.at(-1),first=values[0],delta=last&&first?last[key]-first[key]:null,per=3,reversed=[...rows].reverse(),count=Math.ceil(rows.length/per),i=Math.max(0,Math.min(count-1,Number(page)||0));
  screen.innerHTML=head(label,'#trends',unit)+'<div class="segments">'+(running?[8,26]:[14,28,90]).map(n=>'<a href="#metric/'+key+'/'+n+'/0" aria-current="'+(n===days?'page':'false')+'">'+n+(running?' weeks':' days')+'</a>').join('')+'</div><div class="session-banner"><div><span class="eyebrow">Latest recorded</span><strong class="big-number">'+esc(val(last?.[key]))+'</strong> <span class="note">'+esc(unit)+'</span></div><p>'+esc(delta==null?'':(delta>0?'+':'')+val(delta)+' over window')+'</p></div><section class="panel metric-chart">'+plot(rows,key,label,{bar:running,unit})+'</section><div class="metric-readings">'+reversed.slice(i*per,i*per+3).map(r=>'<div><span class="note">'+esc(dateLabel(r.date||r.start))+'</span><strong>'+esc(r[key]??'No reading')+'</strong></div>').join('')+'</div>'+pager(i,count,n=>'#metric/'+key+'/'+days+'/'+n)+'<p class="footer-note">'+(running?'Zero means no running distance recorded; recordings may be missing.':'Missing readings remain missing. Changes are descriptive, not a performance prediction.')+'</p>';
}

function render(focus=false) {
  if(!report) return;
  const [route,a,b,c,d,e]=(location.hash.slice(1)||'training').split('/');
  const tab=['weeks','programme'].includes(route)?'plan':route==='workout'?(a===report.as_of?'training':'plan'):['recent','activities','activity','insight','trends','metric','forecast','body','body-readings','recovery'].includes(route)?'trends':'training';
  document.querySelectorAll('nav a').forEach(link=>link.setAttribute('aria-current',link.dataset.tab===tab?'page':'false'));
  document.getElementById('goal-label').textContent=dateLabel(report.plan.target_date)+' '+report.plan.target_date.slice(0,4)+' · 5K';
  screen.dataset.route=route;
  const context=document.querySelector('.desktop-context');if(context)context.textContent=tab==='plan'?'TRAINING PLAN':tab==='trends'?'INSIGHTS':'TODAY';
  activityRequest++;
  if(route==='workout') workout(a,b,c);
  else if(route==='programme') programme(a);
  else if(route==='weeks') weeks(a);
  else if(route==='forecast') forecast(a,b);
  else if(route==='body') body(a,b);
  else if(route==='body-readings') bodyReadings(a);
  else if(route==='recovery') recovery(a);
  else if(route==='recent') recent();
  else if(route==='insight') insight(a,b);
  else if(route==='activities') activities(a,b);
  else if(route==='activity') activity(a,b,c,d,e);
  else if(route==='trends') trends(a,b);
  else if(route==='metric') metric(a,b,c);
  else training();
  if(focus) screen.focus({preventScroll:true});
}
async function loadReport() {
  const response=await fetch(BASE+'api/report',{headers:etag?{'If-None-Match':etag}:{}});
  if(response.status===304) return false;
  if(!response.ok) throw Error('Saved training is temporarily unavailable.');
  report=await response.json(); etag=response.headers.get('ETag'); activityCache.clear(); render(); return true;
}
async function poll() {
  if(document.hidden||loading) return;
  loading=true;
  try {
    const response=await fetch(BASE+'api/status');
    if(!response.ok) throw Error('Connection unavailable');
    const state=await response.json(); busy=state.busy;
    const run=state.sync?.run;
    syncStatus.classList.toggle('error',!!state.error||!!state.mfa);
    const stamp=run?.finished_at||run?.started_at;
    syncStatus.textContent=state.mfa?'Garmin requires account verification before background sync can continue.':state.error?'Sync paused · saved training is still available.':busy?'Background import running · showing saved training.':'Auto sync every 2 hours'+(stamp?' · last import '+new Date(stamp).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}):' · waiting for first import');
    syncStatus.title=state.error||state.message||syncStatus.textContent;
    if(!report||state.report_generated_at!==report.generated_at||state.as_of!==report.as_of) await loadReport();
  } catch(_) {
    syncStatus.textContent=report?'Offline · showing saved training.':'Could not load saved training. Reopen when connected.';
    syncStatus.classList.add('error');
  } finally {
    loading=false; clearTimeout(timer);
    if(!document.hidden) timer=setTimeout(poll,busy?15000:60000);
  }
}
addEventListener('hashchange',()=>render(true));
let resizeTimer;
addEventListener('resize',()=>{clearTimeout(resizeTimer); resizeTimer=setTimeout(()=>render(),150);});
document.addEventListener('visibilitychange',()=>{clearTimeout(timer); if(!document.hidden) poll();});
// This viewer never starts a Garmin import. Only the independent server timer
// contacts Garmin; report and activity routes read the persistent local cache.
loadReport().catch(()=>{}).finally(poll);
