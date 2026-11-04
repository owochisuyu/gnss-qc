import {colors,names,timeLabel,LineChart,VisibilityChart,TimeAxis,SkyChart,destroyCharts,refreshCharts} from './charts.js';

const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const icons={grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',satellite:'M8 8l8 8 M7 10l3-3 7 7-3 3z M3 3l4 4-3 3-4-4z M17 17l4 4 3-3-4-4z M5 15a4 4 0 014 4 M3 14a7 7 0 017 7',pulse:'M2 12h4l3-7 5 15 3-8h5',geometry:'M12 3L3 20h18z M12 3v12 M3 20l9-5 9 5',signal:'M4 19v-4 M9 19V9 M14 19V5 M19 19V2',waves:'M2 8c3-7 5 7 8 0s5 7 8 0 5 0 5 0 M2 16c3-7 5 7 8 0s5 7 8 0 5 0 5 0',sky:'M12 3v2 M3 12h2 M19 12h2 M12 19v2 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2 M18 12a6 6 0 11-12 0 6 6 0 0112 0',upload:'M12 16V3 M7 8l5-5 5 5 M4 15v5h16v-5',download:'M12 3v13 M7 11l5 5 5-5 M4 16v5h16v-5',sliders:'M4 4v16 M12 4v16 M20 4v16 M1 9h6 M9 15h6 M17 8h6',file:'M6 2h8l5 5v15H6z M14 2v6h5 M9 12h7 M9 16h7',play:'M7 4l14 8-14 8z',expand:'M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5',clock:'M12 7v5l3 2 M22 12a10 10 0 11-20 0 10 10 0 0120 0',check:'M5 12l4 4L19 6'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[n]||icons.grid}"/></svg>`;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(n,d=2)=>n==null?'—':Number(n).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});
const num=n=>fmt(n,0);

const views=[
  ['overview','grid','观测质量总览','检查连续性、信号强度与观测稳定性。'],
  ['batch','sliders','多站时段巡检','批量概括多个测站自定义时段的质量问题与横向对比。'],
  ['visibility','satellite','卫星时序可见性','查看有效观测弧段与缺口，点击卫星进入组合分析。'],
  ['slips','pulse','周跳检测','联合检查 GF、MW 与 LLI，追溯每个候选事件。'],
  ['dop','geometry','几何精度因子','用广播星历和实际观测卫星评估历元几何。'],
  ['snr','signal','信噪比分析','按系统、信号查看平均 C/N0 与有效样本数。'],
  ['multipath','waves','多路径分析','逐星检查双频组合与去均值后的 MP1 / MP2。'],
  ['sky','sky','卫星天空图','在站心坐标系中查看卫星的方位角与高度角。']
];

let data=null,job=null,view='overview',system='ALL',selectedSat=null,selectedPair=null,pairData=null,snrSignal='ALL',eventFilter='ALL',eventSat='ALL',eventPage=0,skyEpoch=0,busy=false,renderToken=0;
let options={elevation:10,gf_threshold:.05,mw_threshold:10,gap_factor:1.5,min_arc:5,systems:'GRECJIS',position:null};
let batchState={stationDiagnostics:[],timespan:null,start:null,end:null,activeId:null,loading:false};

const chartState={
  start:0,end:1,times:[],interval:30,
  reset(){if(!data)return;this.start=this.times[0];this.end=this.times.at(-1)||this.start+1;syncRanges();renderView()},
  zoom(factor,ratio){if(!data)return;const first=this.times[0],last=this.times.at(-1),span=Math.max(this.interval*5,Math.min(last-first,(this.end-this.start)*factor)),center=this.start+(this.end-this.start)*ratio;this.start=Math.max(first,Math.min(last-span,center-span*ratio));this.end=this.start+span;syncRanges();refreshCharts();if(view==='slips')drawEventsTable()}
};

function decorate(root=document){for(const el of root.querySelectorAll('[data-icon]')){const name=el.dataset.icon;el.insertAdjacentHTML('afterbegin',icon(name));el.removeAttribute('data-icon')}}
function toast(message,error=false){const el=$('#toast');el.textContent=message;el.classList.toggle('error',error);el.classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.add('hidden'),error?10000:4500)}
async function api(url,init){const r=await fetch(url,init);if(!r.ok){let message;try{message=(await r.json()).detail}catch{message=r.statusText}throw new Error(typeof message==='string'?message:JSON.stringify(message))}return r.json()}
const post=(url,value)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});

function setBusy(value){
  busy=value;
  for(const id of['load-sample','empty-sample','batch-load-all','import-button','settings-button']){
    const el=$("#"+id);if(el)el.disabled=value;
  }
  $('#export-button').disabled=value||!data;
  $('#run-status').textContent=value?'正在分析':data?'分析完成':'等待数据';
}

function parseTimestamp(val){
  if(val==null)return null;
  if(typeof val==='number')return val;
  const s=String(val).trim().replace(' ','T');
  return new Date(s+(s.includes('Z')?'':'Z')).getTime()/1000;
}
function isoGpst(sec){
  if(sec==null)return '';
  return new Date(sec*1000).toISOString().slice(0,19).replace('T',' ');
}
function fmtSnrCell(val,sys){
  if(val==null)return `<span>${sys}:—</span>`;
  const cls=val<36.0?'low':val<40.0?'weak':'good';
  return `<span class="${cls}" title="${names[sys]} 平均 C/N0: ${fmt(val,1)} dB-Hz">${sys}:${fmt(val,1)}</span>`;
}

async function trackJob(next){
  setBusy(true);
  $('#progress-panel').classList.remove('hidden');
  const activeId=next.id;
  try{
    let status=next;
    while(status.status!=='done'&&status.status!=='error'){
      $('#progress-message').textContent=status.message;
      $('#progress-percent').textContent=`${status.progress}%`;
      $('#progress-bar').style.width=`${status.progress}%`;
      await new Promise(resolve=>setTimeout(resolve,600));
      status=await api(`/api/jobs/${activeId}`);
    }
    if(status.status==='error')throw new Error(status.error||'分析失败');
    await openResult(activeId);
    toast('分析完成，全部原始历元已计算。');
  }catch(error){toast(error.message,true)}
  finally{setBusy(false);$('#progress-panel').classList.add('hidden');await history()}
}

async function loadSample(id){if(busy)return;try{await trackJob(await post('/api/sample',{sample:id,options}))}catch(error){setBusy(false);toast(error.message,true)}}

async function loadAllSamples(){
  if(busy)return;
  try{
    const list=await api('/api/samples');
    if(!list.length){toast('工作区未发现预置示例测站文件。',true);return}
    toast(`正在批量载入并分析 ${list.length} 个测站，请稍候...`);
    setBusy(true);
    $('#progress-panel').classList.remove('hidden');
    const jobsCreated=await post('/api/batch/load',{samples:list.map(s=>s.id),options});
    for(const j of jobsCreated){
      let st=j;
      while(st.status!=='done'&&st.status!=='error'){
        $('#progress-message').textContent=`正在解算测站 ${st.label}`;
        $('#progress-percent').textContent=`${st.progress}%`;
        $('#progress-bar').style.width=`${st.progress}%`;
        await new Promise(r=>setTimeout(r,500));
        st=await api(`/api/jobs/${j.id}`);
      }
    }
    toast('全部测站分析完成，已进入多站时段巡检大盘！');
    await history();
    if(jobsCreated.length){
      await openResult(jobsCreated[0].id);
      switchView('batch');
    }
  }catch(e){toast('批量载入失败：'+e.message,true)}
  finally{setBusy(false);$('#progress-panel').classList.add('hidden')}
}

function exportBatchCsv(stations){
  if(!stations||!stations.length){toast('当前没有可导出的多站巡检数据。',true);return}
  const rows=[['测站','综合状态','健康评分','历元完整率','可用星数均值','可用星数最低','GPS_CN0','GLO_CN0','GAL_CN0','BDS_CN0','PDOP均值','PDOP峰值','周跳率_千星历元','物理遮挡诊断']];
  for(const s of stations){
    rows.push([
      s.station, s.status, s.score, (s.summary.completeness*100).toFixed(1)+'%',
      s.summary.mean_used?.toFixed(1)||'—', s.summary.min_used??'—',
      s.by_system?.G?.snr_mean?.toFixed(1)||'—', s.by_system?.R?.snr_mean?.toFixed(1)||'—',
      s.by_system?.E?.snr_mean?.toFixed(1)||'—', s.by_system?.C?.snr_mean?.toFixed(1)||'—',
      s.summary.pdop?.mean?.toFixed(2)||'—', s.summary.pdop?.max?.toFixed(2)||'—',
      s.summary.events?.rate_per_1000_sat_epochs?.toFixed(1)||'0',
      s.obstruction.has_obstruction ? s.obstruction.sectors.map(x=>x.label).join('; ') : '无遮挡'
    ]);
  }
  const csvContent=rows.map(r=>r.map(c=>`"${String(c).replace(/"/g,'""')}"`).join(',')).join('\n');
  const blob=new Blob(['\ufeff'+csvContent],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='gnss-qc-batch-summary.csv';
  a.click();
}

async function openResult(id){
  setBusy(true);
  try{
    const result=await api(`/api/jobs/${id}/result`);
    job=id;data=result;options={...data.options};system='ALL';
    selectedSat=data.satellites.find(s=>s.id==='G04')?.id||data.satellites[0].id;
    selectedPair=null;pairData=null;eventPage=0;
    skyEpoch=Math.floor(data.series.length/2);
    chartState.times=data.series.map(r=>r.time);
    chartState.interval=data.interval||1;
    chartState.start=chartState.times[0];
    chartState.end=chartState.times.at(-1)||chartState.start+1;
    $('#empty-state').classList.add('hidden');
    $('#dashboard').classList.remove('hidden');
    $('#export-button').disabled=false;
    renderBase();
    renderView();
    await history();
  }finally{setBusy(false)}
}

async function history(){
  try{
    const entries=await api('/api/jobs');
    $('#history').innerHTML=entries.filter(j=>j.status==='done').length?'<span>最近分析</span>'+entries.filter(j=>j.status==='done').slice(0,5).map(j=>`<button data-job="${j.id}" title="${escape(j.label)}">${j.id===job?'●':'○'} ${escape(j.label)}</button>`).join(''):'';
    for(const button of $$('#history button'))button.onclick=()=>{if(!busy)openResult(button.dataset.job).catch(e=>toast(e.message,true))}
  }catch{}
}

function switchView(next){
  view=next;
  const meta=views.find(v=>v[0]===view);
  $('#page-title').innerHTML=`${meta[2]}<span class="title-dot">.</span>`;
  $('#breadcrumb').textContent=meta[2];
  $('#page-description').textContent=meta[3];
  for(const b of $$('#navigation button'))b.classList.toggle('active',b.dataset.view===view);
  const isBatch = (view === 'batch');
  const metricsEl = $('#metrics'); if(metricsEl) metricsEl.classList.toggle('hidden', isBatch);
  const fileStrip = $('.file-strip'); if(fileStrip) fileStrip.classList.toggle('hidden', isBatch);
  const captionEl = $('.section-caption'); if(captionEl) captionEl.classList.toggle('hidden', isBatch);
  const filterEl = $('.filterbar'); if(filterEl) filterEl.classList.toggle('hidden', isBatch);
  const noticeEl = $('#data-notice'); if(noticeEl && isBatch) noticeEl.classList.add('hidden');
  if(isBatch){
    $('#empty-state').classList.add('hidden');
    $('#dashboard').classList.remove('hidden');
  } else if(!data){
    $('#empty-state').classList.remove('hidden');
    $('#dashboard').classList.add('hidden');
  }
  renderView();
}

function filteredSats(){return data.satellites.filter(s=>system==='ALL'||s.system===system).sort((a,b)=>'GRECJIS'.indexOf(a.system)-'GRECJIS'.indexOf(b.system)||a.id.localeCompare(b.id))}
function eventsInView(){return data.events.filter(e=>(system==='ALL'||e.sat[0]===system)&&data.series[e.epoch].time>=chartState.start&&data.series[e.epoch].time<=chartState.end)}
function metric(label,value,unit,foot,glyph='grid',attention=false){return`<article class="metric ${attention?'attention':''}"><div class="metric-label">${label}${icon(glyph)}</div><div class="metric-value">${value}<small>${unit}</small></div><div class="metric-foot" title="${escape(foot)}">${foot}</div></article>`}

function renderBase(){
  const s=data.summary;
  $('#dataset-name').textContent=data.files.obs.name.split(/[_.]/)[0]+' / 观测会话';
  $('#file-names').textContent=`${data.files.obs.name} + ${data.files.nav.name}`;
  $('#file-period').textContent=`${data.start.slice(0,10)} ${data.start.slice(11,19)} → ${data.end.slice(0,10)===data.start.slice(0,10)?'':data.end.slice(0,10)+' '}${data.end.slice(11,19)} GPST`;
  $('#file-meta').textContent=`RINEX ${data.header.version.toFixed(2)} · ${data.interval} s 采样 · ${(data.files.obs.bytes/1048576).toFixed(1)} MB OBS`;
  $('#scope-label').textContent=`/ ${[...data.options.systems].filter(s=>data.satellites.some(sat=>sat.system===s)).map(s=>names[s]).join(' + ')}`;
  $('#compute-time').textContent=`RTKLIB · ${fmt(data.elapsed_seconds,1)} s`;
  $('#run-status').textContent='分析完成';
  $('#metrics').innerHTML=[
    metric('有效历元',num(s.valid_epochs),'',`${num(s.epochs)} 个记录历元 · 记录完整率 ${fmt(s.epoch_completeness*100,1)}%`,'clock'),
    metric('平均观测卫星',fmt(s.mean_satellites,1),'颗',`${num(s.satellites)} 颗不同卫星`,'satellite'),
    metric('平均 PDOP',fmt(s.pdop.mean),'',`${num(s.pdop.n)} / ${num(s.epochs)} 有效历元`,'geometry'),
    metric('平均 C/N0',fmt(s.cn0.mean,1),'dB-Hz',`${num(s.cn0.n)} 个 S 字段样本`,'signal'),
    metric('疑似周跳',num(s.events),'',`按卫星＋历元去重 · ${fmt(s.event_rate_per_1000_satellite_epochs,1)} / 千条`,'pulse',true),
    metric('MP1 RMS',fmt(s.mp1.rms,3),'m',`${num(s.mp1.n)} 个有效组合样本`,'waves')
  ].join('');
  const notices=[...data.warnings];
  if(s.gaps.length)notices.push(`${s.gaps.length} 处时间缺口，共缺 ${s.gaps.reduce((a,b)=>a+(b.missing_epochs||0),0)} 个预期历元。`);
  if(s.blank_satellite_records)notices.push(`${s.blank_satellite_records} 条卫星记录全部为空，未计为实际观测卫星。`);
  $('#data-notice').classList.toggle('hidden',!notices.length);
  $('#notice-title').textContent=`数据检查 · ${notices.length} 项需要关注`;
  $('#notice-list').innerHTML=notices.map(x=>`<li>${escape(x)}</li>`).join('');
  $('#systems').innerHTML=`<button class="filter-pill active" data-system="ALL">全部</button>`+Object.keys(names).filter(k=>data.satellites.some(s=>s.system===k)).map(k=>`<button class="filter-pill" data-system="${k}"><span class="color-dot" style="background:${colors[k]}"></span>${names[k]}</button>`).join('');
  for(const b of $$('#systems button'))b.onclick=()=>{system=b.dataset.system;snrSignal='ALL';eventPage=0;eventSat='ALL';selectedSat=filteredSats()[0]?.id;selectedPair=null;for(const p of $$('#systems button'))p.classList.toggle('active',p===b);renderView()};
  $('#range-start').max=$('#range-end').max=data.series.length-1;
  syncRanges();
}

function syncRanges(){
  if(!data)return;
  const times=chartState.times;
  const find=t=>{let i=times.findIndex(x=>x>=t);return i<0?times.length-1:i};
  $('#range-start').value=find(chartState.start);
  $('#range-end').value=find(chartState.end);
  $('#range-start').title=timeLabel(chartState.start,true)+' GPST';
  $('#range-end').title=timeLabel(chartState.end,true)+' GPST';
}

function panel(id,title,subtitle='',full=false,tools='',large=false){
  return `<section class="panel ${full?'full':''}"><div class="panel-header"><div class="panel-title"><h2>${title}</h2><span class="subtitle">${subtitle}</span></div><div class="panel-tools">${tools}<button class="icon-button" data-save="${id}" title="导出图表 PNG" aria-label="导出${title}图表">${icon('download')}</button></div></div><div id="${id}-legend" class="panel-legend"></div><div class="chart-wrap ${large?'large':''}"><canvas id="${id}" role="img" aria-label="${title}"></canvas></div><div class="panel-note" id="${id}-note"></div></section>`;
}
function legend(id,lines){const el=$(`#${id}-legend`);if(el)el.innerHTML=lines.map(l=>`<span><i class="legend-mark" style="background:${l.color}"></i>${l.name}</span>`).join('')}
function linePlot(id,lines,config={}){legend(id,lines);return new LineChart($('#'+id),chartState,{lines,gap:(data?.interval||1)*(data?.options?.gap_factor||1.5),...config})}
function rowValue(r,key){return system==='ALL'?r[key]:r.by_system[system]?.[key]??null}

function dopChart(id='dop-chart',extended=false){
  const lines=[{name:'PDOP',color:'#4d8c78',points:data.series.map(r=>[r.time,rowValue(r,'pdop')])}];
  if(extended&&system==='ALL')for(const[k,label,color]of[['gdop','GDOP','#b3a172'],['hdop','HDOP','#89a3bc'],['vdop','VDOP','#ab97bf']])lines.push({name:label,color,points:data.series.map(r=>[r.time,r[k]])});
  linePlot(id,lines,{min:0,unit:'DOP'});$(`#${id}-note`).textContent=`高度角 ≥ ${data.options.elevation}° · 健康广播星历 · 四参数几何模型 · 不可用历元留空`;
}
function snrChart(id='snr-chart'){
  let lines;
  if(snrSignal!=='ALL'){
    const arr=data.snr_series[snrSignal]||[];
    lines=[{name:snrSignal.replace('_',' '),color:colors[snrSignal[0]],points:arr.map(p=>[data.series[p[0]].time,p[1]])}];
  }else{
    const systems=system==='ALL'?Object.keys(names).filter(k=>data.satellites.some(s=>s.system===k)):[system];
    lines=systems.map(k=>({name:names[k],color:colors[k],points:data.series.map(r=>[r.time,r.by_system[k]?.snr??null])}));
  }
  linePlot(id,lines,{unit:'dB-Hz'});
  $(`#${id}-note`).textContent=snrSignal==='ALL'?'各系统所有有效 S 字段的算术均值 · 不同频点混合，比较时可选择同一信号':'指定 S 字段的逐历元算术均值 · 缺测不填零';
}
function countChart(id='count-chart'){
  const lines=[{name:'有数值观测',color:'#689781',points:data.series.map(r=>[r.time,rowValue(r,'satellites')])},{name:'几何可用',color:'#b9a273',points:data.series.map(r=>[r.time,rowValue(r,'used')])}];
  linePlot(id,lines,{min:0,unit:'卫星数'});$(`#${id}-note`).textContent='观测卫星有有效数值；几何可用另要求健康星历与高度角截止。';
}
function visibilityHTML(full=true){
  return `<section class="panel ${full?'full':''}"><div class="panel-header"><div class="panel-title"><span class="panel-number">01</span><h2>卫星时序可见性</h2><span class="subtitle">SATELLITE AVAILABILITY</span></div><div class="panel-tools"><span class="filter-label">${filteredSats().length} 颗卫星</span><button class="icon-button" data-save="visibility-chart" title="导出完整卫星图 PNG" aria-label="导出卫星可见性图">${icon('download')}</button></div></div><div class="visibility-legend">${Object.keys(names).filter(k=>filteredSats().some(s=>s.system===k)).map(k=>`<span><i class="color-dot" style="background:${colors[k]}"></i>${names[k]}</span>`).join('')}<span><i class="slip-mark"></i>疑似周跳</span></div><div class="visibility-scroll"><div class="visibility-wrap"><canvas id="visibility-chart" role="img" aria-label="卫星时序可见性，横轴 GPST，纵轴卫星编号"></canvas></div></div><div class="visibility-axis"><canvas id="visibility-axis" aria-hidden="true"></canvas></div><div class="panel-note">色带为有数值观测的弧段；空白为缺测。滚动查看全部卫星，点击任一卫星查看双频组合。</div></section>`;
}
function drawVisibility(){
  new VisibilityChart($('#visibility-chart'),chartState,filteredSats(),data.events,id=>{selectedSat=id;selectedPair=null;switchView('multipath')});
  new TimeAxis($('#visibility-axis'),chartState);
}

function wireSaves(){
  for(const b of $$('[data-save]')){
    b.onclick=()=>{
      const canvas=$('#'+b.dataset.save);if(!canvas)return;
      const panelEl=canvas.closest('.panel'),scale=devicePixelRatio||1;
      const image=document.createElement('canvas'),axis=canvas.id==='visibility-chart'?$('#visibility-axis'):null;
      image.width=canvas.width;image.height=canvas.height+(axis?axis.height:0)+104*scale;
      const ctx=image.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,image.width,image.height);ctx.scale(scale,scale);
      ctx.fillStyle='#304f42';ctx.font='600 16px "Microsoft YaHei",sans-serif';
      ctx.fillText(panelEl.querySelector('h2').textContent+(view==='multipath'?` · ${selectedSat} · ${pairData?.name||''}`:''),20,27);
      ctx.font='10px Consolas,monospace';ctx.fillStyle='#738b7c';
      ctx.fillText(`${data?.files?.obs?.name||'GNSS-QC'}  |  ${system==='ALL'?'ALL GNSS':names[system]}  |  ${timeLabel(chartState.start,true)} — ${timeLabel(chartState.end,true)} GPST`,20,46);
      ctx.font='10px "Microsoft YaHei",sans-serif';
      ctx.fillText(panelEl.querySelector('.panel-legend,.visibility-legend')?.textContent||'',20,65);
      ctx.drawImage(canvas,0,78,canvas.width/scale,canvas.height/scale);
      if(axis)ctx.drawImage(axis,0,78+canvas.height/scale,axis.width/scale,axis.height/scale);
      ctx.font='9px Consolas,monospace';
      ctx.fillText(`GNSS Observatory / RTKLIB 2.4.3 b34 · Elevation ${data?.options?.elevation||10}° · GF ${data?.options?.gf_threshold||0.05} m · MW ${data?.options?.mw_threshold||10} m`,20,image.height/scale-10);
      const link=document.createElement('a');link.download=`${data?.files?.obs?.name||'gnss'}-${b.dataset.save}.png`;link.href=image.toDataURL('image/png');link.click();
    };
  }
}

function satOptions(allowAll=false,value=selectedSat){return(allowAll?'<option value="ALL">全部卫星</option>':'')+filteredSats().map(s=>`<option value="${s.id}" ${s.id===value?'selected':''}>${s.id} · ${names[s.system]}</option>`).join('')}

function renderView(){
  destroyCharts();renderToken++;if(!data && view!=='batch')return;const root=$('#view-content');eventPage=Math.max(0,eventPage);
  if(view==='overview'){
    root.innerHTML=`<div class="chart-grid">${visibilityHTML()}${panel('dop-chart','几何精度因子','PDOP',false,`<button class="mini-link" data-go="dop">详细分析 ↗</button>`)}${panel('snr-chart','平均信噪比','C/N0',false,`<button class="mini-link" data-go="snr">按信号查看 ↗</button>`)}${panel('count-chart','观测卫星数量','NSAT')}${panel('event-chart','疑似周跳时序','EVENTS')}</div>`;
    drawVisibility();dopChart();snrChart();countChart();eventChart();
  }
  else if(view==='batch'){
    renderBatchView(root, renderToken);
  }
  else if(view==='visibility'){
    root.innerHTML=`<div class="chart-grid">${visibilityHTML()}${panel('count-chart','观测卫星数量','NSAT',true,'',true)}<section class="panel full"><div class="panel-header"><div class="panel-title"><h2>卫星观测统计</h2><span class="subtitle">点击行查看组合</span></div></div><div class="table-scroll"><table><thead><tr><th>卫星</th><th>有效历元</th><th>文件覆盖率</th><th>平均 C/N0</th><th>观测弧段</th><th>候选事件</th></tr></thead><tbody>${filteredSats().map(s=>`<tr class="hover-row" data-sat="${s.id}"><td><span class="color-dot" style="background:${colors[s.system]}"></span>${s.id}</td><td>${num(s.epochs)}</td><td>${fmt(s.coverage*100,1)}%</td><td>${fmt(s.snr.mean,1)} dB-Hz</td><td>${num(s.segments.length)}</td><td>${num(s.events)}</td></tr>`).join('')}</tbody></table></div><div class="panel-note">表格为全时段统计；覆盖率分母为文件内记录历元数。C/N0 先作该星历元均值，再对历元求均值。</div></section></div>`;
    drawVisibility();countChart();for(const row of $$('[data-sat]'))row.onclick=()=>{selectedSat=row.dataset.sat;selectedPair=null;switchView('multipath')}
  }
  else if(view==='dop'){
    root.innerHTML=`<div class="chart-grid">${panel('dop-chart','DOP 时序曲线',system==='ALL'?'GDOP / PDOP / HDOP / VDOP':names[system]+' PDOP',true,'',true)}${panel('count-chart','观测与几何可用卫星数','NSAT')}${stationInfo()}</div>`;
    dopChart('dop-chart',true);countChart();
  }
  else if(view==='snr'){
    const keys=Object.keys(data.snr_series).filter(k=>system==='ALL'||k[0]===system).sort();
    root.innerHTML=`<div class="view-toolbar"><span>信号字段</span><select class="toolbar-select" id="snr-signal"><option value="ALL">按系统 · 全部 S 字段</option>${keys.map(k=>`<option ${k===snrSignal?'selected':''}>${k}</option>`).join('')}</select><span>各历元算术均值 · dB-Hz</span></div><div class="chart-grid">${panel('snr-chart','平均信噪比时序','C/N0',true,'',true)}<section class="panel full"><div class="panel-header"><div class="panel-title"><h2>逐信号统计</h2><span class="subtitle">FULL SESSION</span></div></div><div class="table-scroll"><table><thead><tr><th>系统 / 信号</th><th>C/N0 样本数</th><th>均值</th><th>最小 / 最大</th><th>有效载波数</th><th>相位 LLI bit0</th><th>空相位 LLI bit0</th></tr></thead><tbody>${data.signals.filter(s=>system==='ALL'||s.id[0]===system).map(s=>`<tr><td><span class="color-dot" style="background:${colors[s.id[0]]}"></span>${s.id}</td><td>${num(s.snr.n)}</td><td>${fmt(s.snr.mean,2)}</td><td>${fmt(s.snr.min,1)} / ${fmt(s.snr.max,1)}</td><td>${num(s.phase)}</td><td>${num(s.lli_valid)}</td><td>${num(s.lli_blank)}</td></tr>`).join('')}</tbody></table></div><div class="panel-note">全时段、逐信号统计；极少样本的均值不能代表全天信号水平。</div></section></div>`;
    snrChart();$('#snr-signal').onchange=e=>{snrSignal=e.target.value;renderView()};
  }
  else if(view==='slips'){
    root.innerHTML=`<div class="chart-grid">${panel('event-chart','各判据候选事件时序','GF / MW / LLI',true,'',true)}</div><div style="height:18px"></div><div class="view-toolbar"><span>卫星</span><select class="toolbar-select" id="event-sat">${satOptions(true,eventSat)}</select><span>判据</span><select class="toolbar-select" id="event-filter">${[['ALL','全部判据'],['GF','GF 超限'],['MW','MW 超限'],['LLI','LLI 标记'],['MULTI','至少两种判据']].map(([v,n])=>`<option value="${v}" ${eventFilter===v?'selected':''}>${n}</option>`).join('')}</select><span>按卫星＋历元去重；点击事件查看组合</span></div><div id="events-table"></div>`;
    eventChart();drawEventsTable();$('#event-sat').onchange=e=>{eventSat=e.target.value;eventPage=0;drawEventsTable()};$('#event-filter').onchange=e=>{eventFilter=e.target.value;eventPage=0;drawEventsTable()};
  }
  else if(view==='multipath'){renderMultipath(root,renderToken)}
  else if(view==='sky'){
    root.innerHTML=`<div class="sky-layout"><section class="panel"><div class="panel-header"><div class="panel-title"><h2>卫星天空分布</h2><span class="subtitle">AZIMUTH / ELEVATION</span></div><button class="icon-button" data-save="sky-chart" aria-label="导出天空图">${icon('download')}</button></div><div class="chart-wrap sky-canvas"><canvas id="sky-chart" role="img" aria-label="卫星天空图"></canvas></div><div class="epoch-control"><label for="sky-epoch">历元</label><input type="range" id="sky-epoch" min="0" max="${data.series.length-1}" value="${skyEpoch}"><span id="sky-time"></span></div><div class="panel-note">外圈 0°，中心 90°；虚线为 ${data.options.elevation}° 截止。低于截止或星历不健康的卫星淡显。</div></section>${stationInfo()}</div>`;
    const sky=new SkyChart($('#sky-chart'),chartState,filteredSats(),skyEpoch,data.options.elevation);
    $('#sky-time').textContent=timeLabel(data.series[skyEpoch].time)+' GPST';
    $('#sky-epoch').oninput=e=>{skyEpoch=+e.target.value;sky.index=skyEpoch;sky.draw();$('#sky-time').textContent=timeLabel(data.series[skyEpoch].time)+' GPST'};
  }
  for(const b of $$('[data-go]'))b.onclick=()=>switchView(b.dataset.go);
  wireSaves();
}

async function renderBatchView(root, token){
  root.innerHTML=`<div class="empty-chart"><span class="spinner"></span> 正在读取多测站巡检数据…</div>`;
  try{
    const ts=await api('/api/batch/timespan');
    if(token!==renderToken)return;
    batchState.timespan=ts;
    if(!ts.has_jobs){
      root.innerHTML=`<div class="empty-chart"><h3>暂无已完成的测站分析数据</h3><p>请点击左侧“打开本地示例”或顶部“导入 OBS / NAV”载入测站数据，<br>或点击下方按钮批量载入工作区示例测站。</p><button id="batch-empty-load" class="btn primary" style="margin-top:15px">一键载入工作区测站 ↗</button></div>`;
      const btn=$('#batch-empty-load');if(btn)btn.onclick=()=>loadAllSamples();
      return;
    }
    if(!batchState.start){
      batchState.start=ts.common_start||ts.earliest_start;
      batchState.end=ts.common_end||ts.latest_end;
    }
    const diagData=await post('/api/batch/diagnostics',{start_time:batchState.start,end_time:batchState.end});
    if(token!==renderToken)return;
    batchState.stationDiagnostics=diagData.stations;
    if(!batchState.activeId||!diagData.stations.some(s=>s.job_id===batchState.activeId)){
      batchState.activeId=diagData.stations[0]?.job_id;
    }
    root.innerHTML=`
      <section class="batch-toolbar">
        <div class="batch-time-group">
          <span class="filter-label">巡检分析时段 (GPST)</span>
          <div class="time-input-wrap"><span>起</span><input type="text" id="batch-time-start" value="${escape(batchState.start||'')}"></div>
          <div class="time-input-wrap"><span>止</span><input type="text" id="batch-time-end" value="${escape(batchState.end||'')}"></div>
          <button id="batch-apply-time" class="btn primary" style="padding:6px 12px">应用时段</button>
          <div class="batch-presets">
            ${ts.has_overlap?'<button id="preset-common" title="对齐全部测站共同观测重叠时段">公共时段</button>':''}
            <button id="preset-full" title="涵盖所有测站的最早与最晚历元">全跨度</button>
            <button id="preset-1h" title="以起始时间起算 1 小时">前 1h</button>
            <button id="preset-last1h" title="以结束时间倒推 1 小时">后 1h</button>
          </div>
        </div>
        <div class="batch-actions">
          <button id="batch-export-csv" class="small-button">导出对比总表 CSV</button>
          <button id="batch-reload-all" class="small-button">一键载入所有测站 ⚡</button>
        </div>
      </section>
      <section class="panel full" style="margin-bottom:18px">
        <div class="panel-header">
          <div class="panel-title">
            <span class="panel-number">01</span>
            <h2>多测站质量问题横向比对总表</h2>
            <span class="subtitle">${diagData.stations.length} STATIONS · ${diagData.overall_status==='PASS'?'🟢 总体良好':diagData.overall_status==='WARN'?'🟡 存在关注项':'🔴 存在异常测站'}</span>
          </div>
          <div class="panel-tools"><span style="font-size:10px;color:#859688">点击测站行即可在下方联动查看该站在当前时段的星空轨迹图与四系统详细指标</span></div>
        </div>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>测站</th><th>综合评级</th><th>健康评分</th><th>历元完整率</th><th>可用卫星数</th>
                <th>四系统平均 C/N0 (G / R / E / C)</th><th>PDOP 均值 / 峰值</th><th>候选周跳率</th><th>低空遮挡诊断</th><th>操作</th>
              </tr>
            </thead>
            <tbody id="batch-table-body">
              ${diagData.stations.map(st=>{
                const badgeClass=st.status==='PASS'?'badge-pass':st.status==='WARN'?'badge-warn':'badge-critical';
                const badgeText=st.status==='PASS'?'🟢 优/正常':st.status==='WARN'?'🟡 需关注':'🔴 异常';
                const gSnr=fmtSnrCell(st.by_system?.G?.snr_mean,'G');
                const rSnr=fmtSnrCell(st.by_system?.R?.snr_mean,'R');
                const eSnr=fmtSnrCell(st.by_system?.E?.snr_mean,'E');
                const cSnr=fmtSnrCell(st.by_system?.C?.snr_mean,'C');
                const obstrText=st.obstruction.has_obstruction?`<span style="color:#ba403b;font-weight:600">🔴 ${escape(st.obstruction.sectors.map(x=>x.label).join('; '))}</span>`:'<span style="color:#4a7d60">🟢 视场开阔</span>';
                const isActive=st.job_id===batchState.activeId;
                return `
                  <tr class="hover-row ${isActive?'active-station-row':''}" data-batch-job="${st.job_id}">
                    <td><strong>${escape(st.station)}</strong></td>
                    <td><span class="${badgeClass}">${badgeText}</span></td>
                    <td><strong>${st.score}</strong> / 100</td>
                    <td>${fmt(st.summary.completeness*100,1)}% (${num(st.summary.epochs)}历元)</td>
                    <td>均 ${fmt(st.summary.mean_used,1)} 颗 (最少 ${num(st.summary.min_used)})</td>
                    <td><div class="snr-cell">${gSnr}${rSnr}${eSnr}${cSnr}</div></td>
                    <td>${fmt(st.summary.pdop?.mean,2)} / ${fmt(st.summary.pdop?.max,2)}</td>
                    <td>${fmt(st.summary.events?.rate_per_1000_sat_epochs,1)} 次/千星历元</td>
                    <td>${obstrText}</td>
                    <td><button class="small-button" data-drilldown="${st.job_id}">时段详情 ↘</button></td>
                  </tr>`;
              }).join('')}
            </tbody>
          </table>
        </div>
      </section>
      <div id="batch-drilldown-area"><div class="empty-chart"><span class="spinner"></span> 正在载入该时段星空图与详细时序…</div></div>
    `;

    $('#batch-apply-time').onclick=()=>{batchState.start=$('#batch-time-start').value.trim();batchState.end=$('#batch-time-end').value.trim();renderView()};
    if($('#preset-common'))$('#preset-common').onclick=()=>{batchState.start=ts.common_start;batchState.end=ts.common_end;renderView()};
    $('#preset-full').onclick=()=>{batchState.start=ts.earliest_start;batchState.end=ts.latest_end;renderView()};
    $('#preset-1h').onclick=()=>{const st=parseTimestamp(batchState.start||ts.earliest_start);batchState.start=isoGpst(st);batchState.end=isoGpst(st+3600);renderView()};
    $('#preset-last1h').onclick=()=>{const et=parseTimestamp(batchState.end||ts.latest_end);batchState.start=isoGpst(Math.max(ts.earliest_start_time,et-3600));batchState.end=isoGpst(et);renderView()};
    $('#batch-export-csv').onclick=()=>exportBatchCsv(diagData.stations);
    $('#batch-reload-all').onclick=()=>loadAllSamples();

    for(const row of $$('#batch-table-body tr'))row.onclick=()=>{batchState.activeId=row.dataset.batchJob;renderView()};

    await renderActiveStationDrilldown($('#batch-drilldown-area'), batchState.activeId, token);
  }catch(e){if(token===renderToken)root.innerHTML=`<div class="empty-chart" style="color:#b9423c">载入多站巡检失败：${escape(e.message)}</div>`}
}

async function renderActiveStationDrilldown(container, jobId, token){
  if(!jobId){container.innerHTML='<div class="empty-chart">未选择测站。</div>';return}
  try{
    const stUrl=`/api/jobs/${jobId}/window?start_time=${encodeURIComponent(batchState.start||'')}&end_time=${encodeURIComponent(batchState.end||'')}`;
    const winData=await api(stUrl);
    if(token!==renderToken)return;
    const diagClass=winData.status==='PASS'?'pass':winData.status==='WARN'?'warn':'critical';
    const diagBadge=winData.status==='PASS'?'badge-pass':winData.status==='WARN'?'badge-warn':'badge-critical';
    const diagText=winData.status==='PASS'?'🟢 质量优良':winData.status==='WARN'?'🟡 存在关注项':'🔴 质量异常';

    container.innerHTML=`
      <div class="diag-card ${diagClass}">
        <div class="diag-head">
          <div class="diag-title"><span class="panel-number">02</span> 测站 <strong>${escape(winData.station)}</strong> · 时段质量智能诊断报告 <span class="${diagBadge}">${diagText}</span></div>
          <div style="font-size:11px;font-family:var(--mono);color:#738a7c">健康评分: <strong>${winData.score}</strong> / 100 · 时段历元: ${num(winData.summary.epochs)}</div>
        </div>
        <div class="diag-report-text">${escape(winData.diagnostics?.summary_text||winData.report_text||'')}</div>
        <div class="diag-tags">
          ${winData.diagnostics?.issues?.map(iss=>`<span class="diag-tag ${iss.level.toLowerCase()}">${iss.level==='CRITICAL'?'[严重缺陷] ':'[关注] '}${escape(iss.message)}</span>`).join('')||''}
        </div>
      </div>

      <div class="chart-grid">
        <section class="panel">
          <div class="panel-header">
            <div class="panel-title"><h2>时段卫星天空轨迹图</h2><span class="subtitle">SKYPLOT / OBSTRUCTION</span></div>
            <button class="icon-button" data-save="batch-sky-chart" title="导出星空图">${icon('download')}</button>
          </div>
          <div class="chart-wrap large"><canvas id="batch-sky-chart" role="img" aria-label="时段卫星天空轨迹图"></canvas></div>
          <div class="panel-note" id="batch-sky-note">${escape(winData.obstruction.description)}</div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div class="panel-title"><h2>四系统平均载噪比时序图</h2><span class="subtitle">4-SYSTEM C/N0 (dB-Hz)</span></div>
            <button class="icon-button" data-save="batch-snr-chart" title="导出信噪比时序图">${icon('download')}</button>
          </div>
          <div id="batch-snr-chart-legend" class="panel-legend"></div>
          <div class="chart-wrap large"><canvas id="batch-snr-chart" role="img" aria-label="四系统信噪比时序图"></canvas></div>
          <div class="panel-note" id="batch-snr-chart-note">各系统逐历元所有有效信号算术均值 · 虚线为 40 dB-Hz 质量参考基准线</div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div class="panel-title"><h2>空间几何构型 (PDOP) 与可用卫星数</h2><span class="subtitle">PDOP & NSAT</span></div>
            <button class="icon-button" data-save="batch-dop-chart" title="导出PDOP图">${icon('download')}</button>
          </div>
          <div id="batch-dop-chart-legend" class="panel-legend"></div>
          <div class="chart-wrap"><canvas id="batch-dop-chart" role="img" aria-label="PDOP与卫星数时序图"></canvas></div>
          <div class="panel-note" id="batch-dop-chart-note">健康广播星历四参数解算几何精度因子 · 虚线为 PDOP 2.5 优良基准线</div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div class="panel-title"><h2>候选周跳事件时序分布</h2><span class="subtitle">CYCLE SLIPS (GF / MW / LLI)</span></div>
            <button class="icon-button" data-save="batch-slips-chart" title="导出周跳图">${icon('download')}</button>
          </div>
          <div id="batch-slips-chart-legend" class="panel-legend"></div>
          <div class="chart-wrap"><canvas id="batch-slips-chart" role="img" aria-label="周跳时序图"></canvas></div>
          <div class="panel-note" id="batch-slips-chart-note">该时段共发生 ${num(winData.summary.events?.count||0)} 次候选周跳，去重发生率 ${fmt(winData.summary.events?.rate_per_1000_sat_epochs||0, 1)} 次/千星历元</div>
        </section>
      </div>
    `;

    const winTimes=winData.series.map(r=>r.time);
    const winState={
      start:winTimes[0]||0,
      end:winTimes.at(-1)||1,
      times:winTimes,
      interval:data?.interval||30,
      reset(){},
      zoom(){}
    };

    new SkyChart($('#batch-sky-chart'),winState,winData.satellites,0,data?.options?.elevation||10,{
      trajectory:true,
      obstruction:winData.obstruction,
      timeRange:[winData.window.start_time,winData.window.end_time]
    });

    const snrLines=[];
    for(const s of['G','R','E','C']){
      if(winData.by_system&&winData.by_system[s]){
        snrLines.push({
          name:names[s],
          color:colors[s],
          points:winData.series.map(r=>[r.time,r.by_system[s]?.snr??null])
        });
      }
    }
    legend('batch-snr-chart',snrLines);
    new LineChart($('#batch-snr-chart'),winState,{
      lines:snrLines,
      unit:'dB-Hz',
      min:20,
      max:52,
      threshold:40,
      gap:(data?.interval||30)*1.8
    });

    const dopLines=[
      {name:'PDOP',color:'#4d8c78',points:winData.series.map(r=>[r.time,r.pdop])},
      {name:'可用卫星数',color:'#b9a273',points:winData.series.map(r=>[r.time,r.used])}
    ];
    legend('batch-dop-chart',dopLines);
    new LineChart($('#batch-dop-chart'),winState,{
      lines:dopLines,
      unit:'值',
      min:0,
      threshold:2.5,
      gap:(data?.interval||30)*1.8
    });

    const slipBins=winData.series.map(r=>[r.time,{GF:0,MW:0,LLI:0}]);
    const timeIdxMap=new Map();
    winData.series.forEach((r,idx)=>timeIdxMap.set(r.time,idx));
    for(const ev of winData.events){
      const t=parseTimestamp(ev.time);
      const ep=timeIdxMap.get(t);
      if(ep!=null&&slipBins[ep]){
        for(const d of ev.detectors)slipBins[ep][1][d]++;
      }
    }
    const slipLines=[
      {name:'GF 几何无关',color:'#599a83',points:slipBins.map(([t,v])=>[t,v.GF])},
      {name:'MW 组合',color:'#ac91b8',points:slipBins.map(([t,v])=>[t,v.MW])},
      {name:'LLI 失锁标志',color:'#c5a06d',points:slipBins.map(([t,v])=>[t,v.LLI])}
    ];
    legend('batch-slips-chart',slipLines);
    new LineChart($('#batch-slips-chart'),winState,{
      lines:slipLines,
      unit:'次',
      min:0,
      gap:(data?.interval||30)*1.8
    });

    wireSaves();
  }catch(e){if(token===renderToken)container.innerHTML=`<div class="empty-chart" style="color:#b9423c">载入测站时段详情失败：${escape(e.message)}</div>`}
}

function stationInfo(){
  const pos=data.position,geo=pos.geodetic;
  return `<section class="panel"><div class="panel-header"><div class="panel-title"><h2>站址与星历信息</h2><span class="subtitle">GEOMETRY CONTEXT</span></div></div><div class="info-list">${[['站址来源',pos.source],['纬度 / 经度',geo?`${fmt(geo[0],6)}° / ${fmt(geo[1],6)}°`:'不可用'],['椭球高',geo?fmt(geo[2],3)+' m':'不可用'],['Kepler 星历条数',num(data.navigation.kepler)],['GLONASS 星历条数',num(data.navigation.glonass)],['高度角截止',data.options.elevation+'°'],['缺失几何的卫星历元',num(data.summary.missing_geometry)],['非健康星历的卫星历元',num(data.summary.unhealthy_geometry)],['计算时间系统','GPST']].map(([k,v])=>`<div><span>${k}</span><strong>${escape(v)}</strong></div>`).join('')}</div><div class="panel-note">固定站址、广播星历。缺失或不健康星历不参与 PDOP。</div></section>`;
}

function eventChart(){
  const bins=data.series.map(r=>[r.time,{GF:0,MW:0,LLI:0}]);
  for(const ev of data.events)if(system==='ALL'||ev.sat[0]===system)for(const d of ev.detectors)bins[ev.epoch][1][d]++;
  const lines=[['GF','#599a83'],['MW','#ac91b8'],['LLI','#c5a06d']].map(([name,color])=>({name,color,points:bins.map(([t,v])=>[t,v[name]])}));
  linePlot('event-chart',lines,{min:0,unit:'卫星历元数'});
  $('#event-chart-note').textContent=`GF > ${data.options.gf_threshold} m · MW > ${data.options.mw_threshold} m · LLI bit0/bit1 · 各判据可重叠，不能直接相加。`;
}

function drawEventsTable(){
  let events=eventsInView().filter(e=>(eventSat==='ALL'||e.sat===eventSat)&&(eventFilter==='ALL'||eventFilter==='MULTI'&&e.detectors.length>=2||e.detectors.includes(eventFilter)));
  const pageSize=40,pages=Math.max(1,Math.ceil(events.length/pageSize));
  eventPage=Math.min(eventPage,pages-1);
  const page=events.slice(eventPage*pageSize,(eventPage+1)*pageSize);
  $('#events-table').innerHTML=`<section class="panel"><div class="panel-header"><div class="panel-title"><h2>候选事件明细</h2><span class="subtitle">${num(events.length)} EVENTS</span></div></div><div class="table-scroll"><table><thead><tr><th>时间 GPST</th><th>卫星</th><th>触发判据</th><th>最大 |ΔGF| 对应差值 / m</th><th>最大 |ΔMW| 对应差值 / m</th><th>LLI 标志 / 信号对</th></tr></thead><tbody>${page.map((e,i)=>`<tr class="hover-row" data-event="${i}"><td>${escape(e.time.slice(0,19).replace('T',' '))}</td><td>${e.sat}</td><td>${e.detectors.map(d=>`<span class="detector-badge ${d.toLowerCase()}">${d}</span>`).join('')}</td><td>${fmt(e.dgf,4)}</td><td>${fmt(e.dmw,3)}</td><td class="event-info">${escape(e.signals.map(s=>s.signal+'='+s.lli).join(', ')||e.pairs.map(p=>p.pair).join('; '))}</td></tr>`).join('')||'<tr><td colspan="6">当前筛选范围没有候选事件。</td></tr>'}</tbody></table></div><div class="table-footer"><span>当前时间范围 · 第 ${eventPage+1} / ${pages} 页 · 每页 ${pageSize} 条</span><div><button id="events-prev" class="small-button" ${eventPage===0?'disabled':''}>上一页</button><button id="events-next" class="small-button" ${eventPage===pages-1?'disabled':''}>下一页</button></div></div></section>`;
  $('#events-prev').onclick=()=>{eventPage--;drawEventsTable()};
  $('#events-next').onclick=()=>{eventPage++;drawEventsTable()};
  for(const row of $$('[data-event]'))row.onclick=()=>{
    const ev=page[+row.dataset.event];selectedSat=ev.sat;selectedPair=null;
    const t=data.series[ev.epoch].time;chartState.start=Math.max(chartState.times[0],t-900);chartState.end=Math.min(chartState.times.at(-1),t+900);syncRanges();switchView('multipath');
  };
}

async function renderMultipath(root,token){
  const sats=filteredSats();
  if(!sats.find(s=>s.id===selectedSat))selectedSat=sats[0]?.id;
  const sat=sats.find(s=>s.id===selectedSat);
  if(!sat){root.innerHTML='<div class="empty-chart">当前系统没有卫星。</div>';return}
  if(!sat.pairs.find(p=>p.key===selectedPair))selectedPair=sat.pairs[0]?.key;
  root.innerHTML=`<div class="view-toolbar"><span>卫星</span><select id="detail-sat" class="toolbar-select">${satOptions()}</select><span>信号组合</span><select id="detail-pair" class="toolbar-select">${sat.pairs.map(p=>`<option value="${p.key}" ${p.key===selectedPair?'selected':''}>${p.name}</option>`).join('')||'<option>没有双频载波</option>'}</select><button id="pair-export" class="btn" ${!selectedPair?'disabled':''}>导出组合 CSV ${icon('download')}</button></div><div id="pair-body"><div class="empty-chart">${selectedPair?'读取逐历元组合…':'该卫星没有可计算的双频载波组合。'}</div></div>`;
  $('#detail-sat').onchange=e=>{selectedSat=e.target.value;selectedPair=null;renderView()};
  $('#detail-pair').onchange=e=>{selectedPair=e.target.value;renderView()};
  $('#pair-export').onclick=()=>download('pair',selectedPair);
  if(!selectedPair)return;
  try{
    const d=await api(`/api/jobs/${job}/pair?key=${encodeURIComponent(selectedPair)}`);
    if(token!==renderToken)return;
    pairData=d;
    $('#pair-body').innerHTML=`<div class="detail-stats"><span>MP1 RMS<strong>${fmt(d.mp1.rms,3)} m</strong></span><span>MP2 RMS<strong>${fmt(d.mp2.rms,3)} m</strong></span><span>有效 MP1 样本<strong>${num(d.mp1.n)}</strong></span><span>GF / MW 可比较历元<strong>${num(d.gf_comparisons)} / ${num(d.mw_comparisons)}</strong></span></div><div class="chart-grid">${panel('mp-chart','多路径组合残差','MP1 / MP2',true,'',true)}${panel('gf-chart','几何无关组合历元差','ΔGF')}${panel('mw-chart','MW 组合历元差','ΔMW')}</div>`;
    const points=col=>d.rows.map(r=>[data.series[r[0]].time,r[col],col===5||col===6?r[8]:0]);
    linePlot('mp-chart',[{name:`MP1 · ${d.name.split(' / ')[0]}`,color:'#528f78',points:points(5)},{name:`MP2 · ${d.name.split(' / ')[1]}`,color:'#a897bb',points:points(6)}],{unit:'m'});
    $('#mp-chart-note').textContent=`各弧独立去均值 · 最短 ${data.options.min_arc} 历元 · ${num(d.short_mp_samples)} 个短弧组合值未进入 RMS · 空白为缺测/断弧。`;
    linePlot('gf-chart',[{name:'ΔGF',color:'#669a82',points:points(2)}],{unit:'m',threshold:data.options.gf_threshold});
    $('#gf-chart-note').textContent=`虚线 ±${data.options.gf_threshold} m · 相邻连续历元作差 · 载频 ${fmt(d.f1/1e6,3)} / ${fmt(d.f2/1e6,3)} MHz`;
    linePlot('mw-chart',[{name:'ΔMW',color:'#ac92b6',points:points(4)}],{unit:'m',threshold:data.options.mw_threshold});
    $('#mw-chart-note').textContent=`虚线 ±${data.options.mw_threshold} m · 单位为米 · 伪距噪声也可能造成超限。`;
    wireSaves();
  }catch(e){if(token===renderToken)$('#pair-body').innerHTML=`<div class="empty-chart">${escape(e.message)}</div>`}
}

function download(kind,key=''){
  if(!job)return;
  const a=document.createElement('a');
  a.href=`/api/jobs/${job}/export/${kind}`+(key?`?key=${encodeURIComponent(key)}`:'');
  a.download='';
  a.click();
  $('#export-menu').classList.add('hidden');
}

// Navigation and controls.
$('#navigation').innerHTML=views.map(([id,glyph,label],i)=>`<button class="nav-button ${id===view?'active':''}" data-view="${id}" title="${label}" aria-label="${label}">${icon(glyph)}<span>${label}</span><span class="nav-index">0${i+1}</span></button>`).join('');
for(const b of $$('#navigation button'))b.onclick=()=>switchView(b.dataset.view);
decorate();

$('#settings-systems').innerHTML=Object.keys(names).map(k=>`<label><input type="checkbox" name="system" value="${k}" checked> ${names[k]}</label>`).join('');
for(const b of $$('.close-dialog'))b.onclick=()=>b.closest('dialog').close();
for(const d of $$('dialog'))d.addEventListener('click',e=>{const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close()});

$('#import-button').onclick=$('#empty-import').onclick=()=>$('#import-dialog').showModal();
$('#method-button').onclick=()=>$('#method-dialog').showModal();

$('#settings-button').onclick=()=>{
  const form=$('#settings-form');
  for(const key of['elevation','gf_threshold','mw_threshold','gap_factor','min_arc'])form.elements[key].value=options[key];
  for(const c of $$('#settings-systems input'))c.checked=options.systems.includes(c.value);
  for(const[k,i]of[['x',0],['y',1],['z',2]])form.elements[k].value=options.position?.[i]??'';
  $('#settings-dialog').showModal();
};

$('#settings-form').onsubmit=async e=>{
  e.preventDefault();
  const f=new FormData(e.currentTarget),systems=f.getAll('system').join('');
  if(!systems){toast('至少选择一个卫星系统。',true);return}
  const positions=['x','y','z'].map(k=>f.get(k));
  if(positions.some(Boolean)&&!positions.every(Boolean)){toast('请完整填写 ECEF X、Y、Z，或全部留空。',true);return}
  const next={systems,position:positions.every(Boolean)?positions.map(Number):null};
  for(const k of['elevation','gf_threshold','mw_threshold','gap_factor','min_arc'])next[k]=Number(f.get(k));
  try{
    if(data){
      const submitted=await post(`/api/jobs/${job}/reanalyze`,{options:next});
      options=next;$('#settings-dialog').close();await trackJob(submitted);
    }else{
      options=next;$('#settings-dialog').close();toast('设置已保存，将用于下一次分析。');
    }
  }catch(error){toast(error.message,true)}
};

for(const kind of['obs','nav']){
  const input=$(`#${kind}-file`),zone=$(`#${kind}-drop`);
  const update=()=>{$(`#${kind}-name`).textContent=input.files[0]?`${input.files[0].name} · ${(input.files[0].size/1048576).toFixed(2)} MB`:''};
  input.onchange=update;
  zone.ondragover=e=>{e.preventDefault();zone.classList.add('drag')};
  zone.ondragleave=()=>zone.classList.remove('drag');
  zone.ondrop=e=>{
    e.preventDefault();zone.classList.remove('drag');
    if(e.dataTransfer.files.length){
      const dt=new DataTransfer();dt.items.add(e.dataTransfer.files[0]);input.files=dt.files;update();
    }
  };
}

$('#import-form').onsubmit=async e=>{
  e.preventDefault();if(busy)return;
  const form=new FormData(e.currentTarget);
  for(const name of['obs','nav'])if(form.get(name).size>256*1024*1024){toast('每个文件最大 256 MB。',true);return}
  form.append('options',JSON.stringify(options));
  setBusy(true);
  try{
    const next=await api('/api/analyze',{method:'POST',body:form});
    $('#import-dialog').close();await trackJob(next);
  }catch(error){setBusy(false);toast(error.message,true)}
};

$('#load-sample').onclick=()=>loadSample($('#sample-select').value);
const batchLoadBtn=$('#batch-load-all');if(batchLoadBtn)batchLoadBtn.onclick=()=>loadAllSamples();
$('#empty-sample').onclick=()=>loadSample('Z061');
$('#reset-range').onclick=()=>chartState.reset();

for(const id of['range-start','range-end'])$('#'+id).oninput=e=>{
  let a=+$('#range-start').value,b=+$('#range-end').value;
  if(a>=b){if(id==='range-start')a=Math.max(0,b-1);else b=Math.min(data.series.length-1,a+1)}
  chartState.start=chartState.times[a];chartState.end=chartState.times[b];
  syncRanges();refreshCharts();if(view==='slips')drawEventsTable();
};

$('#export-button').onclick=()=>$('#export-menu').classList.toggle('hidden');
for(const b of $$('[data-export]'))b.onclick=()=>download(b.dataset.export);
document.addEventListener('click',e=>{if(!e.target.closest('.export-wrap'))$('#export-menu').classList.add('hidden')});

async function initialize(){
  try{
    await api('/api/health');
    const samples=await api('/api/samples');
    $('#sample-select').innerHTML=samples.map(s=>`<option value="${s.id}">${s.id} · ${(s.size/1048576).toFixed(1)} MB</option>`).join('');
    if(!samples.length){
      $('#load-sample').disabled=true;
      if($('#batch-load-all'))$('#batch-load-all').disabled=true;
      $('#sample-select').innerHTML='<option>没有本地示例</option>';
      $('#empty-sample').classList.add('hidden');
    }
    const entries=await api('/api/jobs');
    const done=entries.find(j=>j.status==='done');
    if(done){
      await openResult(done.id);
    }else{
      const active=entries.find(j=>['running','queued'].includes(j.status));
      if(active)await trackJob(active);
      else if(new URLSearchParams(location.search).get('sample')==='Z061')await loadSample('Z061');
    }
    await history();
  }catch(error){toast('连接本地分析服务失败：'+error.message,true)}
}

initialize();
