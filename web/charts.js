/* Dependency-free canvas plots. All statistics are computed from full data. */
export const colors={G:'#458c7b',R:'#bd925d',E:'#839dc0',C:'#a18cbb',J:'#6eaaa3',I:'#bcb15d',S:'#bd8b9c'};
export const names={G:'GPS',R:'GLONASS',E:'Galileo',C:'BeiDou',J:'QZSS',I:'NavIC',S:'SBAS'};
export function timeLabel(t,full=false){return new Date(t*1000).toISOString().slice(full?0:11,full?19:19).replace('T',' ')}
const font='10px "Cascadia Code", Consolas, monospace';
const nf=(x,d=2)=>x==null?'—':Number(x).toLocaleString('en-US',{maximumFractionDigits:d});
let plots=[];
export function destroyCharts(){for(const p of plots)p.destroy();plots=[];document.querySelector('#chart-tooltip').classList.add('hidden')}
export function refreshCharts(){for(const p of plots)p.draw()}
function tooltip(event,html){const el=document.querySelector('#chart-tooltip');el.innerHTML=html;el.classList.remove('hidden');el.style.left=`${Math.min(event.clientX+15,innerWidth-el.offsetWidth-12)}px`;el.style.top=`${Math.min(event.clientY+15,innerHeight-el.offsetHeight-15)}px`}
function hideTip(){document.querySelector('#chart-tooltip').classList.add('hidden')}
function nearest(a,x){let lo=0,hi=a.length-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(a[mid][0]<x)lo=mid+1;else hi=mid}return lo>0&&Math.abs(a[lo-1][0]-x)<Math.abs(a[lo][0]-x)?lo-1:lo}
function niceStep(span,n=5){const raw=span/n,base=10**Math.floor(Math.log10(raw||1)),r=raw/base;return(r<1.5?1:r<3?2:r<7?5:10)*base}
class Base{
  constructor(canvas,state){this.canvas=canvas;this.state=state;this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(canvas);plots.push(this);canvas.addEventListener('dblclick',()=>state.reset());canvas.addEventListener('wheel',e=>{e.preventDefault();const rect=canvas.getBoundingClientRect();const ratio=Math.max(0,Math.min(1,(e.clientX-rect.left-50)/(rect.width-70)));state.zoom(e.deltaY<0?.76:1.32,ratio)},{passive:false});canvas.addEventListener('mouseleave',()=>{this.cursor=null;hideTip();this.draw()});}
  prepare(){const r=this.canvas.getBoundingClientRect(),dpr=devicePixelRatio||1;const w=r.width,h=r.height;if(!w||!h)return null;if(this.canvas.width!==Math.round(w*dpr)||this.canvas.height!==Math.round(h*dpr)){this.canvas.width=Math.round(w*dpr);this.canvas.height=Math.round(h*dpr)}const ctx=this.canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#fff';ctx.fillRect(0,0,w,h);ctx.font=font;return{ctx,w,h}}
  destroy(){this.observer.disconnect()}
}
export class LineChart extends Base{
  constructor(canvas,state,config){super(canvas,state);this.config=config;canvas.addEventListener('mousemove',e=>this.hover(e));this.draw()}
  draw(){if(!this.config)return;const setup=this.prepare();if(!setup)return;const{ctx,w,h}=setup,c=this.config,{start,end}=this.state;const left=49,right=w-19,top=22,bottom=h-33;
    this.bounds={left,right,top,bottom};const values=[];for(const line of c.lines)for(const p of line.points)if(p[0]>=start&&p[0]<=end&&p[1]!=null&&Number.isFinite(p[1]))values.push(p[1]);
    let min=c.min??(values.length?Math.min(...(values.length<40000?values:[...values.filter((_,i)=>i%Math.ceil(values.length/30000)===0),values.reduce((a,b)=>Math.min(a,b),Infinity)])):0);
    let max=c.max??(values.length?values.reduce((a,b)=>Math.max(a,b),-Infinity):1);
    if(c.threshold){min=Math.min(min,-c.threshold*1.2);max=Math.max(max,c.threshold*1.2)}
    if(min===max){min-=1;max+=1}const span=max-min;if(c.min==null)min-=span*.07;if(c.max==null)max+=span*.1;const step=niceStep(max-min);min=c.min??Math.floor(min/step)*step;max=c.max??Math.ceil(max/step)*step;
    const x=t=>left+(t-start)/(end-start||1)*(right-left),y=v=>bottom-(v-min)/(max-min||1)*(bottom-top);this.xy={x,y,min,max};
    ctx.strokeStyle='#edf1ec';ctx.lineWidth=1;ctx.fillStyle='#a5b2a2';ctx.textAlign='right';for(let v=Math.ceil(min/step)*step;v<=max+step*.01;v+=step){const yy=y(v);ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(right,yy);ctx.stroke();ctx.fillText(nf(v,Math.max(0,-Math.floor(Math.log10(step))+1)),left-10,yy+3)}
    ctx.textAlign='left';ctx.fillStyle='#a1af9e';ctx.font='9px "Segoe UI", sans-serif';ctx.fillText(c.unit||'',left,12);ctx.font=font;
    const ticks=w<400?3:5;for(let i=0;i<=ticks;i++){const t=start+(end-start)*i/ticks,xx=x(t);ctx.strokeStyle='#f0f3ee';ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,bottom);ctx.stroke();ctx.fillStyle='#a2b19e';ctx.textAlign=i===0?'left':i===ticks?'right':'center';ctx.fillText(timeLabel(t).slice(0,8),xx,bottom+20)}
    if(c.threshold){ctx.save();ctx.setLineDash([4,4]);ctx.strokeStyle='#d7b9a0';for(const v of[c.threshold,-c.threshold]){ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(right,y(v));ctx.stroke()}ctx.restore()}
    ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
    for(const line of c.lines){ctx.strokeStyle=line.color;ctx.lineWidth=line.width||1.5;ctx.globalAlpha=line.opacity||1;ctx.beginPath();let pen=false,last=null;
      // Draw all samples; missing values, arc boundaries and acquisition gaps split lines.
      for(const p of line.points){if(p[0]<start||p[0]>end){pen=false;last=null;continue}if(p[1]==null||!Number.isFinite(p[1])){pen=false;last=null;continue}const xx=x(p[0]),yy=y(p[1]);if(last&&(p[0]-last[0]>(c.gap||Infinity)||(p.length>2&&last.length>2&&p[2]!==last[2])))pen=false;if(!pen)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);pen=true;last=p}ctx.stroke();
      if(line.points.filter(p=>p[1]!=null).length<3){for(const p of line.points){if(p[1]==null||p[0]<start||p[0]>end)continue;ctx.beginPath();ctx.arc(x(p[0]),y(p[1]),2.5,0,Math.PI*2);ctx.fillStyle=line.color;ctx.fill()}}
    }ctx.restore();ctx.globalAlpha=1;
    if(!values.length){ctx.fillStyle='#a2af9a';ctx.textAlign='center';ctx.font='12px "Microsoft YaHei", sans-serif';ctx.fillText(c.empty||'此时间范围没有有效样本',(left+right)/2,(top+bottom)/2)}
    if(this.cursor!=null){ctx.strokeStyle='#a7bda7';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(this.cursor,top);ctx.lineTo(this.cursor,bottom);ctx.stroke();ctx.setLineDash([])}
  }
  hover(e){if(!this.bounds)return;const rect=this.canvas.getBoundingClientRect(),px=e.clientX-rect.left,{left,right}=this.bounds;if(px<left||px>right)return;this.cursor=px;const t=this.state.start+(px-left)/(right-left)*(this.state.end-this.state.start);let label=t;const items=[];for(const line of this.config.lines){if(!line.points.length)continue;const p=line.points[nearest(line.points,t)];if(Math.abs(p[0]-t)>(this.config.gap||Infinity))continue;label=p[0];items.push(`<div><span style="color:${line.color}">●</span> ${line.name} <b>${nf(p[1],3)}</b> ${this.config.unit||''}</div>`)}tooltip(e,`<strong>${timeLabel(label,true)} GPST</strong>${items.join('')||'此处缺测'}`);this.draw()}
}
export class VisibilityChart extends Base{
  constructor(canvas,state,satellites,events,onSelect){super(canvas,state);this.satellites=satellites;this.events=events;this.rowHeight=18;canvas.style.height=`${satellites.length*this.rowHeight+15}px`;canvas.addEventListener('mousemove',e=>this.hover(e));canvas.addEventListener('click',e=>{const row=this.row(e);if(row)onSelect(row.id)});this.draw()}
  row(e){const rect=this.canvas.getBoundingClientRect();return this.satellites[Math.floor((e.clientY-rect.top-7)/this.rowHeight)]}
  draw(){if(!this.satellites)return;const setup=this.prepare();if(!setup)return;const{ctx,w,h}=setup,{start,end,times,interval}=this.state,left=49,right=w-19;const x=t=>left+(t-start)/(end-start||1)*(right-left),rows=new Map();
    for(let i=0;i<6;i++){const xx=left+(right-left)*i/5;ctx.strokeStyle='#eef2e9';ctx.beginPath();ctx.moveTo(xx,0);ctx.lineTo(xx,h);ctx.stroke()}
    ctx.save();for(let i=0;i<this.satellites.length;i++){const s=this.satellites[i],cy=7+i*this.rowHeight+this.rowHeight/2;rows.set(s.id,cy);if(i%2===0){ctx.fillStyle='#fafcf8';ctx.fillRect(0,cy-this.rowHeight/2,w,this.rowHeight)}ctx.font='9px Consolas,monospace';ctx.textAlign='right';ctx.fillStyle=colors[s.system];ctx.fillText(s.id,left-11,cy+3);ctx.fillStyle=colors[s.system];ctx.globalAlpha=.72;for(const[a,b]of s.segments){const t1=times[a],t2=times[b]+interval*.7;if(t2<start||t1>end)continue;const xx=Math.max(left,x(t1)),xx2=Math.min(right,x(t2));ctx.beginPath();ctx.roundRect(xx,cy-3,Math.max(1,xx2-xx),6,2);ctx.fill()}ctx.globalAlpha=1}
    ctx.strokeStyle='#cc805e';ctx.lineWidth=1;ctx.beginPath();for(const e of this.events){const cy=rows.get(e.sat),t=times[e.epoch];if(cy==null||t<start||t>end)continue;const xx=x(t);ctx.moveTo(xx,cy-5);ctx.lineTo(xx,cy+5)}ctx.stroke();ctx.restore();
  }
  hover(e){const row=this.row(e);if(!row)return;tooltip(e,`<strong>${row.id} · ${names[row.system]}</strong>有效卫星历元 ${nf(row.epochs,0)}<br>文件覆盖 ${(row.coverage*100).toFixed(1)}%<br>疑似周跳 ${nf(row.events,0)} 个卫星历元<br>点击查看该星 GF / MW / MP`)}
}
export class TimeAxis extends Base{
  constructor(canvas,state){super(canvas,state);this.draw()}
  draw(){const setup=this.prepare();if(!setup)return;const{ctx,w}=setup;ctx.font='9px Consolas,monospace';ctx.fillStyle='#a2b19e';for(let i=0;i<6;i++){ctx.textAlign=i===0?'left':i===5?'right':'center';ctx.fillText(timeLabel(this.state.start+(this.state.end-this.state.start)*i/5),49+(w-68)*i/5,17)}}
}
export class SkyChart extends Base{
  constructor(canvas,state,satellites,index,mask){super(canvas,state);this.satellites=satellites;this.index=index;this.mask=mask;this.draw()}
  draw(){if(!this.satellites)return;const setup=this.prepare();if(!setup)return;const{ctx,w,h}=setup,cx=w/2,cy=h/2,r=Math.min(w,h)/2-38;
    ctx.strokeStyle='#e2eade';ctx.lineWidth=1;for(const el of[0,30,60]){ctx.beginPath();ctx.arc(cx,cy,r*(90-el)/90,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#a9b7a2';ctx.font='9px Consolas';ctx.textAlign='left';ctx.fillText(`${el}°`,cx+4,cy-r*(90-el)/90+12)}
    ctx.strokeStyle='#eaf0e5';for(let a=0;a<Math.PI*2;a+=Math.PI/4){ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.sin(a)*r,cy-Math.cos(a)*r);ctx.stroke()}
    ctx.fillStyle='#7e9671';ctx.font='10px Consolas';ctx.textAlign='center';ctx.fillText('N',cx,cy-r-14);ctx.fillText('S',cx,cy+r+20);ctx.fillText('E',cx+r+19,cy+4);ctx.fillText('W',cx-r-19,cy+4);
    ctx.strokeStyle='#d7c5a6';ctx.setLineDash([4,4]);ctx.beginPath();ctx.arc(cx,cy,r*(90-this.mask)/90,0,Math.PI*2);ctx.stroke();ctx.setLineDash([]);
    let count=0;for(const s of this.satellites){const row=s.track.find(p=>p[0]===this.index);if(!row||row[1]==null||row[2]<0)continue;const az=row[1]*Math.PI/180,rr=r*(90-row[2])/90,x=cx+Math.sin(az)*rr,y=cy-Math.cos(az)*rr;ctx.fillStyle=colors[s.system];ctx.globalAlpha=row[2]<this.mask||row[4]!==0?.3:1;ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();ctx.font='9px Consolas';ctx.fillText(s.id,x,y-8);ctx.globalAlpha=1;count++}
    if(!count){ctx.fillStyle='#9aaa90';ctx.font='12px "Microsoft YaHei"';ctx.fillText('此历元没有可用天空位置',cx,cy+17)}
  }
}
