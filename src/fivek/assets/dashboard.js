const BASE = document.querySelector('meta[name="fivek-base-path"]').content;
const screen = document.getElementById('screen');
const syncStatus = document.getElementById('sync-status');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateLabel = value => new Date(value + 'T12:00:00Z').toLocaleDateString('en-GB', {day:'numeric',month:'short',timeZone:'UTC'});
const time = seconds => seconds == null ? '—' : Math.floor(Math.round(seconds) / 60) + ':' + String(Math.round(seconds) % 60).padStart(2,'0');
const short = (text, length=100) => text?.length > length ? text.slice(0,length).replace(/\s+\S*$/,'') + '…' : text || '';
const volume = s => s.gym && s.kind!=='strength' ? (s.estimated_minutes||s.minutes||'Run')+' + '+s.gym.minutes+' min' : s.km ? s.km + ' km' : s.minutes ? s.minutes + ' min' : s.kind === 'football' ? 'Evening' : 'Recovery';
let report = null, etag = null, busy = false, loading = false, timer = null;
const recoveryLabel = status => ({normal:'No flags',unknown:'Unknown',reduce:'Take it easy',pause:'Pause impact'}[status] || 'Unknown');
const empty = text => '<p class="empty">' + esc(text) + '</p>';
function pager(index, count, href) {
  return '<div class="pager"><a aria-label="Previous page" aria-disabled="' + (index<=0) + '" href="' + href(Math.max(0,index-1)) + '">← Previous</a><span>' + (count ? index+1 : 0) + ' / ' + count + '</span><a aria-label="Next page" aria-disabled="' + (index>=count-1) + '" href="' + href(Math.min(count-1,index+1)) + '">Next →</a></div>';
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
  if(!s.gym||!s.steps?.length)return '';
  return '<div class="segments">'+[['overview','Run'],['gym','Gym '+(s.gym.programme_key||'')]].map(([key,label])=>'<a href="#workout/'+s.date+'/'+key+'/0" aria-current="'+((key==='gym'?section==='gym':section!=='gym')?'page':'false')+'">'+label+'</a>').join('')+'</div>';
}
function plot(rows,key,label,{bar=false,unit='',seconds=false,second=null,timeline=false,longRange=false,compact=false}={}) {
  const data=rows.filter(r=>Number.isFinite(r[key]));
  if(!data.length)return '<div class="plot">'+empty('No recorded '+label.toLowerCase()+' in this window.')+'</div>';
  const values=data.map(r=>r[key]).concat(second?rows.filter(r=>Number.isFinite(r[second])).map(r=>r[second]):[]);
  let low=bar?0:Math.min(...values),high=Math.max(...values);
  const pad=bar?Math.max(1,high*.08):Math.max((high-low)*.18,key.includes('weight')?.2:.5);
  high+=pad;if(!bar)low=Math.max(0,low-pad);if(high===low)high=low+1;
  if(['readiness','stress','body_battery_last'].includes(key)){low=0;high=100;}
  const stamps=rows.map((r,i)=>timeline?r.minutes:Date.parse((r.date||r.start)+'T12:00:00Z')||i),first=Math.min(...stamps),last=Math.max(...stamps);
  const mobile=innerWidth<=900;
  const width=compact?Math.max(120,mobile?(innerWidth-40)/2-20:(Math.min(innerWidth-216,1700)-80)/3-32):480;
  const height=compact?(mobile?Math.max(60,Math.min(180,(innerHeight-180)/3-90)):Math.max(110,Math.min(280,(innerHeight-160)/2-110))):164;
  const left=compact?30:50,right=width-(compact?6:20),top=compact?9:24,bottom=height-(compact?17:36),font=compact?(mobile?8:10):15;
  const x=i=>left+4+((stamps[i]-first)/Math.max(1,last-first))*(right-left-8),y=value=>bottom-(value-low)/(high-low)*(bottom-top);
  const format=n=>seconds?time(n):String(Number(n.toFixed(1)));
  let graphics='';
  if(bar) {
    const barWidth=Math.min(42,(right-left)*.8/rows.length);
    graphics=rows.map((r,i)=>Number.isFinite(r[key])?'<rect x="'+(x(i)-barWidth/2)+'" y="'+y(r[key])+'" width="'+barWidth+'" height="'+(bottom-y(r[key]))+'" rx="3" fill="'+(i===rows.length-1?'#264f3a':'#b1c68e')+'"><title>'+esc((r.date||r.start)+': '+format(r[key])+' '+unit)+'</title></rect>':'').join('');
  }else {
    for(const [channel,color] of [[key,'#264f3a'],...(second?[[second,'#6897c5']]:[])]) {
      let path='',connected=false,dots='';
      rows.forEach((r,i)=>{if(r.gap_before?.includes(channel))connected=false;if(!Number.isFinite(r[channel])){connected=false;return;}
        path+=(connected?'L':'M')+x(i).toFixed(1)+','+y(r[channel]).toFixed(1)+' ';connected=true;
        dots+='<circle cx="'+x(i)+'" cy="'+y(r[channel])+'" r="'+(compact?1:(rows.length<20?3:1.7))+'" fill="'+color+'"><title>'+esc((r.date||r.start)+': '+format(r[channel])+' '+unit)+'</title></circle>';
      });
      graphics+='<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="'+(compact?1.7:3)+'" stroke-linejoin="round"/>'+dots;
    }
  }
  const startLabel=timeline?val(rows[0].minutes)+' min':dateLabel(rows[0].date||rows[0].start)+(longRange?' '+(rows[0].date||rows[0].start).slice(0,4):'');
  const endLabel=timeline?val(rows.at(-1).minutes)+' min':dateLabel(rows.at(-1).date||rows.at(-1).start)+(longRange?' '+(rows.at(-1).date||rows.at(-1).start).slice(0,4):'');
  return '<div class="plot"><svg viewBox="0 0 '+width+' '+height+'" preserveAspectRatio="xMidYMid meet" role="img" aria-label="'+esc(label)+'">'+[top,(top+bottom)/2,bottom].map(v=>'<line x1="'+left+'" y1="'+v+'" x2="'+right+'" y2="'+v+'" stroke="#e6ebe2"/>').join('')+'<text x="'+(left-4)+'" y="'+(top+3)+'" text-anchor="end" font-size="'+font+'" fill="#66756c">'+esc(format(high))+'</text><text x="'+(left-4)+'" y="'+(bottom+3)+'" text-anchor="end" font-size="'+font+'" fill="#66756c">'+esc(format(low))+'</text>'+graphics+'<text x="'+left+'" y="'+(height-3)+'" font-size="'+font+'" fill="#66756c">'+esc(startLabel)+'</text><text x="'+right+'" y="'+(height-3)+'" text-anchor="end" font-size="'+font+'" fill="#66756c">'+esc(endLabel)+'</text></svg></div>';
}
function windowRows(rows,days) {
  const start=new Date(report.as_of+'T00:00:00Z');
  if(days!=='all')start.setUTCDate(start.getUTCDate()-Number(days)+1);
  return (rows||[]).filter(r=>(days==='all'||(r.date||r.start)>=start.toISOString().slice(0,10))&&(r.date||r.start)<=report.as_of);
}
function lastValue(rows,key){return [...rows].reverse().find(r=>Number.isFinite(r[key]))?.[key];}

function training() {
  const p=report.plan,sessions=p.sessions.slice(0,7),today=sessions.find(s=>s.date===report.as_of)||sessions[0],tomorrow=sessions[1],a=report.metrics.athlete||{},latest=a.recovery?.latest||{};
  if(!today){screen.innerHTML=head('No upcoming workout',null,'Your saved goal may have passed.')+empty('There are no future sessions in the saved plan.');return;}
  const headline=today.kind==='rest'?'Recovery is today’s workout.':today.title;
  const ticker='<div class="mobile-week"><div class="section-label"><h3>Your week</h3><a class="button" href="#weeks/0">Full plan →</a></div><div class="week-ticker">'+sessions.map(s=>'<a class="'+(s.date===today.date?'today':'')+'" href="'+sessionLink(s)+'" aria-label="'+esc(s.weekday+' '+s.title)+'"><span>'+esc(s.weekday.slice(0,3))+'</span><span class="day-mark '+sessionTone(s)+'"></span></a>').join('')+'</div></div>';
  const component=today.gym&&today.kind!=='strength'?'<div class="component-actions"><a href="#workout/'+today.date+'/steps/0">Run plan <strong>'+esc(today.estimated_minutes||today.minutes||volume(today))+' min ↗</strong></a><a href="#workout/'+today.date+'/gym/0">Gym '+esc(today.gym.programme_key||'')+' <strong>'+today.gym.minutes+' min ↗</strong></a></div>':'';
  const preview=plot(windowRows(report.metrics.daily,28),'readiness','Training readiness');
  screen.innerHTML='<div class="heading"><div><span class="eyebrow">'+esc(today.weekday+' · '+dateLabel(today.date))+'</span><h1>Today, made simple.</h1></div><span class="pill">'+Math.max(0,p.days_remaining)+' days to go</span></div><div class="today-layout"><div class="today-main"><section class="panel today-hero"><div class="hero-top"><span class="eyebrow">'+(today.status==='recorded'?'RECORDED TODAY':'YOUR NEXT SESSION')+'</span><span class="hero-icon" aria-hidden="true">'+sessionIcon(today)+'</span></div><h2>'+esc(headline)+'</h2><div class="hero-facts"><span>'+esc(volume(today))+'</span><span>'+esc(today.effort)+'</span>'+(today.gym?'<span>'+today.gym.exercises.length+' exercises</span>':'')+'</div><p class="note">'+esc(short(today.next_action||today.details,120))+'</p><a class="button primary today-cta" data-today-workout href="'+sessionLink(today)+'">Open today’s workout <span aria-hidden="true">→</span></a>'+component+'</section><div class="panel readiness-strip"><div><span>Readiness</span><strong>'+esc(latest.readiness??'—')+'<small> /100</small></strong></div><div><span>Last sleep</span><strong>'+esc(val(latest.sleep_hours))+'<small> h</small></strong></div><div><span>Your recovery</span><strong>'+esc(recoveryLabel(report.metrics.recovery.status))+'</strong></div></div><section class="panel today-preview"><div class="section-label"><h2>Readiness over 28 days</h2><a class="button" href="#trends/90/hrv">Recovery trend →</a></div>'+preview+'</section></div><div class="today-side">'+(tomorrow?'<a class="panel next-up mobile-next" href="'+sessionLink(tomorrow)+'"><div class="date-box">'+esc(tomorrow.weekday.slice(0,3))+'<strong>'+tomorrow.date.slice(8)+'</strong></div><div><span class="eyebrow">Up next · tomorrow</span><h3>'+esc(tomorrow.title)+'</h3><span class="note">'+esc(volume(tomorrow))+' · '+esc(tomorrow.effort)+'</span></div><span aria-hidden="true">→</span></a>':'')+'<section class="plan-mini"><div class="section-label"><h2>Your seven days</h2><a class="button" href="#weeks/0">Plan →</a></div>'+sessions.map(dayRow).join('')+'</section>'+ticker+gymShortcuts()+'</div></div>';
}

function workout(date,section='overview',page=0) {
  const s=report.plan.sessions.find(s=>s.date===date);
  if(!s){screen.innerHTML=head('Session unavailable')+empty('Return to Today for your current workout.');return;}
  const tabs=workoutTabs(s,section),header=head(s.title,'#training',dateLabel(s.date)+' · '+volume(s));
  if(s.gym&&section==='gym')return gymPage(s,page,tabs);
  if(s.gym&&section==='gym-exercise')return gymExercise(s,page);
  const isRun=!!s.steps?.length;
  screen.dataset.workoutKind=isRun?'run':s.kind;
  const primary=isRun?'<div class="section-label"><h2>Your run plan</h2><span class="pill">'+esc(s.estimated_minutes||s.minutes||'—')+' min total</span></div>'+timeline(s):instruction(s.kind==='rest'?'Today’s priority':'The session',s.details,'action');
  const gymLink=s.gym?'<a class="button dark" href="#workout/'+date+'/gym/0">'+esc(s.gym.name)+' · '+s.gym.exercises.length+' exercises →</a>':'';
  screen.innerHTML=header+tabs+'<div class="workout-layout"><section class="workout-primary">'+facts([['Duration',s.total_minutes?s.total_minutes+' min':volume(s)],['Effort',s.kind==='easy'?'RPE 2–3':s.effort],['Status',s.status==='recorded'?'Recorded':'Planned']])+primary+gymLink+'</section></div>';
}

function gymPage(s,page,segments) {
  const gym=s.gym,per=innerWidth<=900?(innerHeight<700?5:6):8,count=Math.ceil(gym.exercises.length/per),i=Math.max(0,Math.min(count-1,Number(page)||0));
  screen.innerHTML=head(gym.name,'#training',dateLabel(s.date)+' · '+gym.minutes+' min'+(gym.optional?' · optional third session':''))+segments+'<section class="gym-body"><div class="session-banner"><p>'+esc(gym.effort)+'</p></div><div class="exercise-list">'+gym.exercises.slice(i*per,(i+1)*per).map((e,n)=>'<a class="exercise-row" href="#workout/'+s.date+'/gym-exercise/'+(i*per+n)+'"><span class="exercise-number">'+(i*per+n+1)+'</span><div><strong>'+esc(e.name)+'</strong><span class="exercise-dose">'+e.sets+' sets × '+esc(e.reps)+' · rest '+(e.rest_seconds%60?e.rest_seconds+' sec':e.rest_seconds/60+' min')+'</span></div><span class="exercise-arrow" aria-hidden="true">→</span></a>').join('')+'</div><p class="footer-note">Working sets shown. Warm-up / ramp-up sets are additional.</p><div class="gym-footer">'+(count>1?pager(i,count,n=>'#workout/'+s.date+'/gym/'+n):'')+'</div></section>';
}

function gymExercise(s,index=0) {
  const g=s.gym,i=Math.max(0,Math.min(g.exercises.length-1,Number(index)||0)),e=g.exercises[i],per=innerWidth<=900?(innerHeight<700?5:6):8;
  screen.innerHTML=head(e.name,'#workout/'+s.date+'/gym/'+Math.floor(i/per),g.name)+'<div class="session-banner"><strong>'+esc(e.reps)+' per set</strong><span class="pill">Rest '+e.rest_seconds/60+' min</span></div><div><span class="eyebrow">Planned working sets</span><div class="set-strip">'+Array.from({length:e.sets},(_,n)=>'<div class="set-box"><span>SET '+(n+1)+'</span><strong>'+esc(e.reps)+'</strong><span>reps'+(e.reps.includes('sec')?' / timed hold':'')+'</span></div>').join('')+'</div></div><div class="two-column"><div class="instruction action"><h3>Technique focus</h3><p>'+esc(e.cue)+'</p><div class="cue-visual" aria-label="Use ramp-up sets before working sets"><i class="rep-block"></i><i class="rep-block"></i><i class="rep-block"></i><span>Ramp up first.<br>These are work sets, not warm-ups.</span></div></div><div class="instruction"><h3>Choose your working weight</h3><p>'+esc(g.effort)+'. Use a current submaximal load, not your old personal record.</p></div></div><div class="pager"><a href="#workout/'+s.date+'/gym-exercise/'+Math.max(0,i-1)+'" aria-disabled="'+(i===0)+'">← Exercise</a><span>'+(i+1)+' / '+g.exercises.length+'</span><a href="#workout/'+s.date+'/gym-exercise/'+Math.min(g.exercises.length-1,i+1)+'" aria-disabled="'+(i===g.exercises.length-1)+'">Exercise →</a></div>';
}

function insightRange(value) {return ['28','90','365','all'].includes(String(value))?String(value):'90';}
function rangeSwitch(range,key='') {
  return '<div class="range-switch" aria-label="Chart time range">'+[['28','28D'],['90','90D'],['365','1Y'],['all','All']].map(([n,label])=>'<a href="#trends/'+n+(key?'/'+key:'')+'" aria-current="'+(n===range?'page':'false')+'">'+label+'</a>').join('')+'</div>';
}
function insightCharts(range) {
  const a=report.metrics.athlete||{},f=a.performance||{},body=a.body||{},daily=windowRows(report.metrics.daily,range),weights=windowRows(body.readings,range),races=windowRows(f.history,range);
  // Aggregate the entire saved activity history, rather than the report's 26-week summary.
  const buckets=new Map(),asOf=Date.parse(report.as_of+'T12:00:00Z'),activities=windowRows(report.metrics.activities,range);
  const oldest=range==='all'?activities.reduce((old,v)=>v.date<old?v.date:old,report.as_of):new Date(asOf-(Number(range)-1)*86400000).toISOString().slice(0,10);
  const count=Math.max(1,Math.ceil((asOf-Date.parse(oldest+'T12:00:00Z'))/604800000)+1);
  for(let offset=count-1;offset>=0;offset--){const end=new Date(asOf-offset*604800000).toISOString().slice(0,10),start=new Date(asOf-offset*604800000-518400000).toISOString().slice(0,10);buckets.set(offset,{date:end,start,end,run_km:0,run_count:0});}
  for(const v of activities){const offset=Math.floor((asOf-Date.parse(v.date+'T12:00:00Z'))/604800000),bucket=buckets.get(offset);if(bucket&&v.kind==='run'){bucket.run_km+=v.km||0;bucket.run_count++;}}
  const weeks=windowRows([...buckets.values()],range);
  return [
    {id:'fitness',title:'5K estimate',rows:races,key:'seconds',unit:'min',seconds:true,extra:'VO₂ '+val(lastValue(daily,'vo2_max')),note:f.available?'January scenario '+time(f.january_seconds)+' · conditional':'Garmin estimate'},
    {id:'running',title:'Running volume',rows:weeks,key:'run_km',unit:'km/week',bar:true,extra:val(lastValue(weeks,'run_count'),0)+' runs in latest week'},
    {id:'load',title:'Training load',rows:daily,key:'acute_load',unit:'units',second:'chronic_load',extra:'Acute / chronic'},
    {id:'weight',title:'Body mass',rows:weights,key:'weight_kg',unit:'kg',extra:'Body fat '+val(lastValue(weights,'body_fat_pct'))+'%'},
    {id:'hrv',title:'Overnight HRV',rows:daily,key:'hrv_ms',unit:'ms',extra:'Resting HR '+val(lastValue(daily,'resting_hr'),0)+' bpm'},
    {id:'sleep',title:'Sleep',rows:daily,key:'sleep_hours',unit:'h',bar:true,extra:'Readiness '+val(lastValue(daily,'readiness'),0)+'/100'},
  ];
}
function trends(range='90',selected='') {
  // Existing bookmarks for the old categories open the consolidated dashboard.
  if(['performance','recovery','body','workload'].includes(range)){range=selected;selected='';}
  range=insightRange(range);
  const charts=insightCharts(range),focused=charts.find(c=>c.id===selected);
  function chart(c,expanded=false) {
    const last=lastValue(c.rows,c.key),value=Number.isFinite(last)?(c.seconds?time(last):val(last)):'—';
    const values=c.rows.filter(r=>Number.isFinite(r[c.key])),first=values[0],final=values.at(-1),delta=first&&final?final[c.key]-first[c.key]:null;
    const change=delta==null?'':(delta>0?'+':delta<0?'−':'')+(c.seconds?time(Math.abs(delta)):val(Math.abs(delta)))+(c.seconds?'':' '+c.unit);
    const tag=expanded?'section':'a',attrs=expanded?'':' href="#trends/'+range+'/'+c.id+'" aria-label="Expand '+c.title+' chart"';
    return '<'+tag+attrs+' class="panel infographic '+(expanded?'expanded':'')+'"><div class="graphic-heading"><h2>'+esc(c.title)+'</h2><span class="graphic-context">'+esc(c.extra)+'</span></div><div class="graphic-numbers"><strong>'+esc(value)+'<small> '+esc(c.seconds?'':c.unit)+'</small></strong><span class="graphic-change">'+esc(change)+'</span></div>'+plot(c.rows,c.key,c.title,{...c,compact:!expanded,longRange:range==='365'||range==='all'})+(c.second?'<div class="graphic-legend"><span>● Acute</span><span>● Chronic</span></div>':'')+(expanded?'<p class="chart-caption">'+esc((first?dateLabel(first.date||first.start)+' '+(first.date||first.start).slice(0,4)+' – '+dateLabel(final.date||final.start)+' '+(final.date||final.start).slice(0,4)+'. ':'')+(c.note||'')+(range==='all'?' All saved history.':''))+'</p>':'')+'</'+tag+'>';
  }
  screen.innerHTML='<div class="insight-top">'+(focused?'<a class="back" href="#trends/'+range+'" aria-label="Back to Insights">←</a>':'')+'<h1>'+(focused?esc(focused.title):'Insights')+'</h1>'+rangeSwitch(range,focused?.id||'')+'</div>'+(focused?chart(focused,true):'<div class="infographic-grid">'+charts.map(c=>chart(c)).join('')+'</div>');
}

function weeks(page=0) {
  const values=report.plan.weeks,i=Math.max(0,Math.min(values.length-1,Number(page)||0)),w=values[i];
  if(!w)return training();
  const end=new Date(w.start+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+6);
  const rows=report.plan.sessions.filter(s=>s.date>=w.start&&s.date<=end.toISOString().slice(0,10));
  screen.innerHTML=head('Week of '+dateLabel(w.start),'#training',w.phase+' · '+w.training_days+' training days · '+w.gym_sessions+' gym exposures')+'<div class="plan-layout"><div class="week-desktop">'+rows.map(s=>'<article class="week-column"><span class="eyebrow">'+esc(s.weekday)+'</span><strong class="week-kind" aria-hidden="true">'+sessionIcon(s)+'</strong><h3>'+esc(s.title)+'</h3><span class="pill">'+esc(volume(s))+'</span><p class="note">'+esc(short(s.details,150))+'</p>'+(s.gym?'<p class="week-meta">'+s.gym.exercises.length+' gym exercises</p>':'')+'<a class="button" href="'+sessionLink(s)+'">Open workout →</a></article>').join('')+'</div><div class="forecast-week">'+rows.map(dayRow).join('')+'</div>'+pager(i,values.length,n=>'#weeks/'+n)+'<div class="plan-note"><p>'+esc(w.conditional?'Future sessions adapt to recorded training and recovery.':'Current week · follow the stated session effort, not catch-up mileage.')+'</p></div></div>';
}

function render(focus=false) {
  if(!report)return;
  const [route,a,b,c]=(location.hash.slice(1)||'training').split('/');
  const tab=route==='weeks'?'plan':route==='workout'?(a===report.as_of?'training':'plan'):route==='training'?'training':'trends';
  document.querySelectorAll('nav a').forEach(link=>link.setAttribute('aria-current',link.dataset.tab===tab?'page':'false'));
  document.getElementById('goal-label').textContent=dateLabel(report.plan.target_date)+' '+report.plan.target_date.slice(0,4)+' · 5K';
  screen.dataset.route=route;delete screen.dataset.workoutKind;
  const context=document.querySelector('.desktop-context');if(context)context.textContent=tab==='plan'?'TRAINING PLAN':tab==='trends'?'INSIGHTS':'TODAY';
  if(route==='workout')workout(a,b,c);
  else if(route==='weeks'||route==='programme')weeks(a);
  else if(route==='training')training();
  else if(route==='trends')trends(a,b);
  else trends();
  if(focus)screen.focus({preventScroll:true});
}
async function loadReport() {
  const response=await fetch(BASE+'api/report',{headers:etag?{'If-None-Match':etag}:{}});
  if(response.status===304) return false;
  if(!response.ok) throw Error('Saved training is temporarily unavailable.');
  report=await response.json(); etag=response.headers.get('ETag'); render(); return true;
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
