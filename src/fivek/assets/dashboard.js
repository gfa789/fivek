const BASE = document.querySelector('meta[name="fivek-base-path"]').content;
const screen = document.getElementById('screen');
const syncStatus = document.getElementById('sync-status');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateLabel = value => new Date(value + 'T12:00:00Z').toLocaleDateString('en-GB', {day:'numeric',month:'short',timeZone:'UTC'});
const time = seconds => seconds == null ? '—' : Math.floor(Math.round(seconds) / 60) + ':' + String(Math.round(seconds) % 60).padStart(2,'0');
const short = (text, length=100) => text?.length > length ? text.slice(0,length).replace(/\s+\S*$/,'') + '…' : text || '';
const volume = s => s.km ? s.km + ' km' : s.minutes ? s.minutes + ' min' : s.kind === 'football' ? 'Evening' : 'Recovery';
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
  const limit = innerWidth < 700 ? (innerHeight < 720 ? 360 : 520) : 950;
  const result = []; let current = '';
  for (const word of String(text || '').split(/\s+/)) {
    if (current && current.length + word.length > limit) { result.push(current); current = ''; }
    current += (current ? ' ' : '') + word;
  }
  if (current) result.push(current);
  return result.length ? result : ['No further detail is available yet.'];
}
function textPage(title, label, text, parent, route, page=0, segments='') {
  const pages = chunks(text), index = Math.max(0,Math.min(pages.length-1,Number(page)||0));
  screen.innerHTML = '<div class="detail-head">' + back(parent,'Overview') + '<span class="eyebrow">' + esc(label) + '</span><h1>' + esc(title) + '</h1></div>' + segments + '<section class="panel detail-body"><span class="detail-label">' + esc(label) + '</span><p class="reading">' + esc(pages[index]) + '</p>' + pager(index,pages.length,n=>route(n)) + '</section>';
}
function chart(rows, key, label) {
  const available = rows.filter(r=>Number.isFinite(r[key]));
  if (!available.length) return empty('No recorded ' + label.toLowerCase() + ' yet.');
  const maximum = Math.max(1,...available.map(r=>r[key]));
  const step = 500 / Math.max(rows.length,1);
  return '<svg viewBox="0 0 560 190" role="img" aria-label="' + esc(label) + '"><text x="8" y="20" fill="#63776c" font-size="12">' + esc(Math.round(maximum*10)/10) + '</text><line x1="40" y1="155" x2="545" y2="155" stroke="#d9e1d4"/>' + rows.map((row,i)=>{
    const value=row[key], height=Number.isFinite(value) ? value/maximum*125 : 0;
    return '<g><title>' + esc((row.start||row.date) + ': ' + (value ?? 'missing')) + '</title><rect x="' + (42+i*step) + '" y="' + (155-height) + '" width="' + Math.max(1,step*.7) + '" height="' + height + '" rx="2" fill="' + (i===rows.length-1?'#173e32':'#b3c88b') + '"/></g>';
  }).join('') + '<text x="40" y="180" font-size="12" fill="#63776c">' + esc(dateLabel(rows[0].start||rows[0].date)) + '</text><text x="540" y="180" text-anchor="end" font-size="12" fill="#63776c">' + esc(dateLabel(rows.at(-1).start||rows.at(-1).date)) + '</text></svg>';
}
function training() {
  const p=report.plan, sessions=p.sessions.slice(0,7);
  screen.innerHTML = '<div class="heading overview-title"><div><span class="eyebrow">Your January 5K</span><h1>Make the next session count.</h1><p class="note">' + esc(Math.max(0,p.days_remaining)) + ' days to ' + esc(dateLabel(p.target_date)) + ' · ' + esc(recoveryLabel(report.metrics.recovery.status)) + '</p></div><a class="button" href="#programme/0">The road ahead ↗</a></div><div class="focus">' + sessions.slice(0,2).map((s,i)=>'<a class="panel focus-card" href="#workout/' + s.date + '/overview/0"><span class="eyebrow">' + (i?'Tomorrow':'Today') + ' · ' + esc(dateLabel(s.date)) + '</span><h2>' + esc(s.title) + '</h2><p>' + esc(short(s.next_action || s.details,92)) + '</p><div><span class="pill">' + esc(s.status==='recorded'?'Recorded':volume(s)) + '</span></div><span class="open">Workout & strength →</span></a>').join('') + '</div><section class="panel week-panel"><div class="section-label"><h2>The next seven days</h2><span class="eyebrow">Run · strength · football</span></div><div class="week">' + sessions.map(s=>'<a class="day" data-kind="' + esc(s.kind) + '" href="#workout/' + s.date + '/overview/0"><span class="eyebrow">' + esc(s.weekday.slice(0,3)+' '+s.date.slice(8)) + '</span><strong>' + esc(s.title) + '</strong><span class="day-volume">' + esc(volume(s)) + ' ↗</span></a>').join('') + '</div>' + (!sessions.length?empty('No upcoming sessions. Your saved goal may have passed.'):'') + '</section>';
}
function workout(date, section='overview', page=0) {
  const s=report.plan.sessions.find(s=>s.date===date);
  if(!s) return textPage('Session unavailable','Saved plan','This session is no longer in the current plan. Return to the overview for your latest training.','#training',n=>'#training');
  const sections = {
    overview: {label:'Workout',text:[s.details,s.readiness_note,s.next_action,'Effort: '+s.effort+'.',s.optional?'This session is optional.':'',s.conditional?'Forecast only. This session adapts as actual training is recorded.':''].filter(Boolean).join(' ')},
    steps:{label:'Steps',text:s.steps?.length?s.steps.map((v,i)=>'Step '+(i+1)+': '+v.minutes+' min '+v.kind.replaceAll('_',' ')+', '+v.effort+'.').join(' '):'No timed repetitions are prescribed. '+s.details},
    why:{label:'Why this session',text:[...(s.decision_reasons||[]),report.plan.pace_guidance,'Recovery guidance uses recorded wearable signals. Your watch cannot tell us about every symptom; stop or reduce a workout if how you feel warrants it.'].join(' ')},
  };
  const selected=sections[section]||sections.overview;
  const segments='<div class="segments">'+Object.entries(sections).map(([key,v])=>'<a href="#workout/'+date+'/'+key+'/0" aria-current="'+(selected===v?'page':'false')+'">'+v.label+'</a>').join('')+'</div>';
  textPage(s.title,dateLabel(s.date)+' · '+volume(s),selected.text,'#training',n=>'#workout/'+date+'/'+section+'/'+n,page,segments);
}
const sportMatch = (a,sport) => sport==='all' || (sport==='strength'?a.sport==='strength_training':a.kind===sport);
function recentActivities() {
  const start=new Date(report.as_of+'T00:00:00Z'); start.setUTCDate(start.getUTCDate()-27);
  return report.metrics.activities.filter(a=>a.date>=start.toISOString().slice(0,10)&&a.date<=report.as_of);
}
function recent() {
  const m=report.metrics, activities=recentActivities();
  const stats=[['Running · 28 days',m.baseline_weekly_km.toFixed(1)+' km/wk',m.recent_run_count+' recorded runs'],['5K reference',m.benchmark?time(m.benchmark.seconds):'—','Observed effort, not a prediction'],['Sleep · 7 days',m.sleep_average_hours==null?'—':m.sleep_average_hours.toFixed(1)+' h','Recorded nights only'],['Recovery',recoveryLabel(m.recovery.status),'Wearable signals, not clearance']];
  screen.innerHTML='<div class="heading"><div><span class="eyebrow">The recent picture</span><h1>Work done. Recovery earned.</h1></div></div><div class="stats">'+stats.map(([label,value,note])=>'<div class="panel stat"><span class="eyebrow">'+esc(label)+'</span><strong>'+esc(value)+'</strong><span class="note">'+esc(note)+'</span></div>').join('')+'</div><div class="stack stretch"><section class="panel"><div class="section-label"><h2>Last 28 days</h2><a class="open" href="#activities/all/0">All activities →</a></div><div class="sports">'+[['run','Running'],['strength','Strength'],['football','Football']].map(([key,label])=>{const rows=activities.filter(a=>sportMatch(a,key));return '<a class="sport" href="#activities/'+key+'/0"><strong>'+Math.round(rows.reduce((sum,a)=>sum+(a.minutes||0),0))+' min</strong><span>'+label+' · '+rows.length+' sessions ↗</span></a>';}).join('')+'</div></section><a class="panel card-link stretch" href="#insight/0/0"><span class="eyebrow">Training insights · '+m.insights.length+' findings</span><h2>'+esc(m.insights[0]?.title||'Building your training picture')+'</h2><p>'+esc(short(m.insights[0]?.detail||'Saved training will appear after the next automatic import.',150))+'</p><span class="open">Read the evidence →</span></a></div>';
}
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
async function activity(id,page=0,sport='all',listPage=0) {
  const request=++activityRequest, parent='#activities/'+sport+'/'+listPage;
  const saved=report.metrics.activities.find(a=>String(a.id)===id);
  screen.innerHTML=back(parent,'Activities')+empty('Loading saved workout detail…');
  try {
    if(!activityCache.has(id)) {
      const response=await fetch(BASE+'api/activities/'+encodeURIComponent(id));
      if(!response.ok) throw Error('No saved detail available.');
      activityCache.set(id,await response.json());
    }
    if(request!==activityRequest||!location.hash.startsWith('#activity/'+id+'/')) return;
    const detail=activityCache.get(id), summary=detail.activity||saved||{};
    const text=[summary.name||saved?.name,summary.date||saved?.date,
      'Distance: '+(summary.km==null?'not recorded':summary.km+' km')+'.',
      'Duration: '+(summary.minutes==null?'not recorded':summary.minutes+' minutes')+'.',
      'Average heart rate: '+(summary.average_hr??'not recorded')+'.',
      'Pace: '+(summary.pace_seconds_km?time(summary.pace_seconds_km)+' per km':'not recorded')+'.',
      ...(detail.laps||[]).map((lap,i)=>'Split '+(i+1)+': '+(lap.distance_km??'—')+' km in '+time(lap.seconds)+', average heart rate '+(lap.average_hr??'not recorded')+'.'),
      detail.note,
    ].filter(Boolean).join(' ');
    textPage(saved?.name||'Recorded workout','Saved Garmin activity',text,parent,n=>'#activity/'+id+'/'+n+'/'+sport+'/'+listPage,page);
  } catch(_) {
    if(request===activityRequest) textPage(saved?.name||'Workout','Saved data','Detailed readings have not been imported yet. Your recorded session remains saved. Background imports will fill in missing history.',parent,n=>'#activity/'+id+'/'+n+'/'+sport+'/'+listPage,page);
  }
}
function dailyRows(key,days) {
  const start=new Date(report.as_of+'T00:00:00Z'); start.setUTCDate(start.getUTCDate()-days+1);
  return report.metrics.daily.filter(d=>d.date>=start.toISOString().slice(0,10)&&d.date<=report.as_of);
}
function trends() {
  const weeks=(report.metrics.history_weeks||report.metrics.weeks).slice(-26), daily=dailyRows('vo2_max',90);
  screen.innerHTML='<div class="heading"><div><span class="eyebrow">Long-term trajectories</span><h1>How your fitness is moving.</h1><p class="note">Volume, recovery and Garmin estimates.</p></div></div><div class="grid trends-grid"><a class="panel chart-link" href="#metric/run_km/26/0"><div><span class="eyebrow">26 weeks</span><h2>Running consistency</h2></div><div class="chart">'+chart(weeks,'run_km','Recorded kilometres per week')+'</div><span class="open">Volume & weekly readings →</span></a><a class="panel chart-link" href="#metric/vo2_max/90/0"><div><span class="eyebrow">90 days</span><h2>Fitness & recovery</h2></div><div class="chart">'+chart(daily,'vo2_max','VO₂ max')+'</div><span class="open">VO₂ max · HRV · sleep · load →</span></a></div><p class="footer-note">Garmin estimates are signals. Missing data stays missing.</p>';
}
function metric(key='vo2_max',range=90,page=0) {
  if(key!=='run_km'&&!metrics[key]) key='vo2_max';
  const running=key==='run_km', days=running?([8,26].includes(Number(range))?Number(range):26):([14,28,90].includes(Number(range))?Number(range):90);
  const rows=running?(report.metrics.history_weeks||report.metrics.weeks).slice(-days):dailyRows(key,days);
  const label=running?'Running volume':metrics[key][0], unit=running?'km/week':metrics[key][1];
  const perPage=innerHeight<720?3:5, reversed=[...rows].reverse(), count=Math.ceil(rows.length/perPage), index=Math.max(0,Math.min(count-1,Number(page)||0));
  const values=rows.filter(r=>Number.isFinite(r[key])), latest=values.at(-1), first=values[0];
  const delta=latest&&first?Math.round((latest[key]-first[key])*10)/10:null;
  const keyIndex=metricKeys.indexOf(key);
  screen.innerHTML='<div class="detail-head">'+back('#trends','Fitness trends')+'<span class="eyebrow">'+esc(unit)+'</span><h1>'+esc(label)+'</h1></div><div class="segments">'+(running?[8,26]:[14,28,90]).map(n=>'<a href="#metric/'+key+'/'+n+'/0" aria-current="'+(n===days?'page':'false')+'">'+n+(running?' weeks':' days')+'</a>').join('')+(!running?'<a href="#metric/'+metricKeys[(keyIndex+1)%metricKeys.length]+'/'+days+'/0">Next metric →</a>':'')+'</div><div class="panel comparison"><div><span class="eyebrow">Latest recorded</span><div class="metric-value">'+esc(latest?Math.round(latest[key]*10)/10:'—')+'</div></div><p class="note">'+esc(latest?dateLabel(latest.start||latest.date):'No readings')+'<br>'+esc(delta==null?'':(delta>0?'+':'')+delta+' over window')+'</p></div><div class="rows">'+reversed.slice(index*perPage,(index+1)*perPage).map(row=>'<div class="row"><span>'+esc(dateLabel(row.start||row.date))+'</span><strong>'+esc(row[key]??'No reading')+'</strong></div>').join('')+(!rows.length?empty('No readings imported for this period.'):'')+'</div>'+pager(index,count,n=>'#metric/'+key+'/'+days+'/'+n)+'<p class="footer-note">'+(running?'Zero means no distance recorded, including possible missing recordings.':'Change compares first and last recorded values; it is not a fitness prediction.')+'</p>';
}
function render(focus=false) {
  if(!report) return;
  const [route,a,b,c,d,e]=(location.hash.slice(1)||'training').split('/');
  const tab=['workout','programme'].includes(route)?'training':['activities','activity','insight'].includes(route)?'recent':route==='metric'?'trends':route;
  document.querySelectorAll('nav a').forEach(link=>link.setAttribute('aria-current',link.dataset.tab===tab?'page':'false'));
  document.getElementById('goal-label').textContent=dateLabel(report.plan.target_date)+' '+report.plan.target_date.slice(0,4)+' · 5K';
  activityRequest++;
  if(route==='workout') workout(a,b,c);
  else if(route==='programme') programme(a);
  else if(route==='recent') recent();
  else if(route==='insight') insight(a,b);
  else if(route==='activities') activities(a,b);
  else if(route==='activity') activity(a,b,c,d);
  else if(route==='trends') trends();
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
    syncStatus.classList.toggle('error',!!state.error||state.mfa);
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
