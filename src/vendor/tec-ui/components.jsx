// React default import is used by the Windows classic JSX compiler.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React, {useEffect, useRef, useState, useMemo, useId} from 'react';
import {ArrowUpRight, ChevronDown, FlaskConical, Pause, Play, Settings2} from 'lucide-react';
import './styles.css';
import {formatPower, temperatureScale, thermalState} from './measurements.js';
import {sampleBreak, acquisition, lastSampleTime} from './sampling.js';
import {useChartFrame, useAnimatedDomain, easeOut} from './chartMotion.js';

export const COLORS = ['#8b8e7c', '#628275', '#a6815c', '#2d5748'];
export const fmt = (n, places = 3) => Number.isFinite(n) ? n.toFixed(places) : '—';
export const clock = t => t ? new Date(t * 1000).toLocaleTimeString('zh-CN', {hour12:false}) : '—';
const unit = mode => ({T:'°C', R:'kΩ', ITE:'A'}[mode]??'');
const num = text => text.trim() === '' ? NaN : Number(text);

/** Host-owned policy switch. Changing it only calls the injected callback. */
export function ToggleSwitch({checked, onChange, disabled=false, label, id}) {
  return <button id={id} type="button" role="switch" aria-label={label} aria-checked={Boolean(checked)} disabled={disabled} className="toggle-switch" onClick={()=>onChange(!checked)}><span className="toggle-switch-thumb" aria-hidden="true"/></button>;
}

export function Status({on, current, disconnected, last=false}) {
  const {direction}=thermalState(current,null,on);
  return <span className={`output-status ${on && !disconnected ? 'is-on' : ''}`}><i/><span>{disconnected ? '读数未更新' : on == null ? '等待读取' : on ? `${last?'最后：':''}输出开启` : `${last?'最后：':''}输出关闭`}</span>{direction&&!disconnected&&<span className="thermal-direction" data-thermal={direction} title={last?'最后采样的电流方向':undefined}>{direction==='heating'?'加热':'制冷'}</span>}</span>;
}
export function Metric({label, value, suffix}) { return <div className="metric"><span>{label}</span><strong>{value}<small>{suffix}</small></strong></div>; }
export function PowerMetric({current, voltage, detail=false}) {
  const power=formatPower(Number.isFinite(current)&&Number.isFinite(voltage)?current*voltage:NaN);
  return <Metric label={detail?'电功率 P':'P'} value={power.value} suffix={power.unit}/>;
}

export function Spark({samples, channel, acquisitionPaused=false, overclockChannel=null}) {
  const points=samples.filter(s=>s.sampled_channels.includes(channel)&&Number.isFinite(s.channels[channel-1].temperature)).slice(-100);
  const values=points.map(s=>s.channels[channel-1].temperature);
  const lo=Math.min(...values)-.001,hi=Math.max(...values)+.001;
  const start=points[0]?.t??0,span=Math.max(.5,(points.at(-1)?.t??0)-start);
  const path=points.map((s,i)=>`${sampleBreak(points[i-1],s)?'M':'L'}${(s.t-start)/span*210} ${38-(values[i]-lo)/(hi-lo)*32}`).join(' ');
  return <div className={`spark-area ${acquisitionPaused?'acquisition-paused':''}`}>
    <svg className="spark" viewBox="0 0 210 44" aria-hidden="true"><path d="M0 38H210" className="spark-baseline"/><path className="measurement-trace" fill="none" stroke={COLORS[channel-1]} strokeWidth="1.5" d={path}/></svg>
    {acquisitionPaused&&<div className="acquisition-pause-label"><strong>PAUSED</strong><small>通道{overclockChannel}超频中</small></div>}
  </div>;
}

export function SetpointForm({channel, disabled, send, compact=false}) {
  const actual = channel.setpoints[channel.mode];
  const displayActual=Number.isFinite(actual)?fmt(actual):'';
  const [draft, setDraft] = useState(displayActual);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (!dirty) setDraft(displayActual); }, [actual, dirty]);
  useEffect(() => {setDraft(displayActual);setDirty(false);setError('');}, [channel.id, channel.mode]);
  const submit = async e => {
    e.preventDefault();setError('');
    if (!Number.isFinite(num(draft))) {setError('请输入有效数值');return;}
    setBusy(true);
    try {await send(channel.id, 'setpoint', {value:num(draft), mode:channel.mode});setDirty(false);}
    catch (e) {setError(e.message);} finally {setBusy(false);}
  };
  return <form className={`setpoint-form ${compact ? 'compact' : ''}`} onSubmit={submit}>
    <label htmlFor={`target-${compact?'card':'detail'}-${channel.id}`}>{channel.mode==='T'?'目标温度':channel.mode==='R'?'目标电阻':channel.mode==='ITE'?'目标电流':'目标值'}<span>{!compact && `当前 ${fmt(actual)} ${unit(channel.mode)}`}</span></label>
    <div className="input-action"><div className="number-input"><input id={`target-${compact?'card':'detail'}-${channel.id}`} aria-label={`CH${channel.id} 目标值`} type="number" step="0.001" value={draft} disabled={disabled || busy} onChange={e=>{setDraft(e.target.value);setDirty(true);setError('');}}/><span>{unit(channel.mode)}</span></div><button className="set-button" disabled={disabled || busy || !dirty} type="submit">{busy?'…':'设定'}</button></div>
    {error && <p className="field-error" role="alert">{error}</p>}
  </form>;
}

export function ChannelCard({channel:c, samples, connected, open, send, busy, acquisitionState, overclockChannel, thermalColors=true}) {
  const thermal=thermalState(c.current,c.voltage,c.output);
  const tint=thermalColors&&connected&&!acquisitionState.paused&&!acquisitionState.stale&&thermal.level>0;
  const thermalStyle=tint?{'--thermal-fill':`${thermal.fill.toFixed(2)}%`,'--thermal-edge':`${thermal.edge.toFixed(2)}%`}:undefined;
  return <article className="channel-card" data-thermal={tint?thermal.direction:undefined} style={thermalStyle}>
    <div className="channel-card-top"><h2><span className="channel-dot" style={{background:COLORS[c.id-1]}}/>CH {c.id}</h2><button className="channel-open" aria-label={`查看 CH${c.id} 通道详情`} title={`查看 CH${c.id} 通道详情`} onClick={()=>open(c.id)}><ArrowUpRight size={23}/></button></div>
    <div className="temperature-row"><div className="temperature">{fmt(c.temperature)}<span>°C</span></div><Status on={c.output} current={c.current} disconnected={!connected||(!acquisitionState.paused&&acquisitionState.stale)} last={acquisitionState.paused}/></div>
    {(acquisitionState.paused||acquisitionState.stale)&&<span className="last-reading">最后读数 · {clock(acquisitionState.last)}</span>}
    <Spark samples={samples} channel={c.id} acquisitionPaused={acquisitionState.paused} overclockChannel={overclockChannel}/>
    <div className="card-readings"><Metric label="R" value={fmt(c.resistance)} suffix="kΩ"/><PowerMetric current={c.current} voltage={c.voltage}/><Metric label="I" value={fmt(c.current,3)} suffix="A"/><Metric label="V" value={fmt(c.voltage,3)} suffix="V"/></div>
    <SetpointForm compact channel={c} disabled={!c.writable || !connected || busy} send={send}/>
  </article>;
}

export function TemperatureChart({samples, channel, seconds, paused, connected, smooth, serverTime, acquisitionPaused=false, overclockChannel=null, mode='absolute'}) {
  const container = useRef(null);
  const clipId=useId();
  const [width,setWidth] = useState(900);
  const [height,setHeight] = useState(480);
  const [hover,setHover] = useState(null);
  useEffect(()=>{const obs = new ResizeObserver(entries=>{setWidth(Math.max(280,entries[0].contentRect.width));setHeight(Math.max(280,entries[0].contentRect.height));});obs.observe(container.current);return ()=>obs.disconnect();},[]);
  const {now,animate}=useChartFrame(container,smooth&&connected&&!paused&&!acquisitionPaused);
  const latest=samples.at(-1);
  const anchor=useRef(null), lastEnd=useRef(null), tail=useRef(null);
  if(!anchor.current)anchor.current={time:serverTime??latest?.t??Date.now()/1000,at:performance.now(),observed:serverTime};
  if(anchor.current.observed!==serverTime){
    const predicted=anchor.current.time+(performance.now()-anchor.current.at)/1000;
    if(serverTime!=null&&Math.abs(predicted-serverTime)>.25)anchor.current={time:serverTime,at:performance.now()};
    anchor.current.observed=serverTime;
  }
  const sampledEnd=latest?.t??anchor.current.time;
  const end=paused&&lastEnd.current!=null?lastEnd.current:animate?Math.max(sampledEnd,anchor.current.time+(now-anchor.current.at)/1000):sampledEnd;
  lastEnd.current=end;
  if(tail.current?.seq!==latest?.seq)tail.current={seq:latest?.seq,start:now,initial:tail.current==null};
  const visible = useMemo(()=>samples.filter(s=>s.t>=end-seconds),[samples,end,seconds]);
  const values = visible.map(s=> {const c=s.channels[channel-1];return mode==='relative'?(c.temperature-c.setpoint)*1000:c.temperature;});
  const setpoint = visible.at(-1)?.channels[channel-1].setpoint;
  const hasTarget = Number.isFinite(setpoint);
  const base = mode==='relative'?0:setpoint;
  const targetScale=temperatureScale(values,mode==='relative');
  const {precision,ticks}=targetScale;
  const {min,max}=useAnimatedDomain(targetScale,now,animate,`${channel}:${mode}:${seconds}`);
  const targetVisible=base>=min && base<=max;
  const h=height, left=width<500?61:70, right=16, top=30, bottom=34;
  const x=t=>left+(t-(end-seconds))/seconds*(width-left-right), y=v=>top+(max-v)/(max-min)*(h-top-bottom);
  let path='';
  visible.forEach((s,i)=>{
    let stamp=s.t,value=values[i];
    // Reveal the newest line segment; never create or alter stored samples.
    if(animate&&!tail.current.initial&&i===visible.length-1&&i>0&&!sampleBreak(visible[i-1],s)){
      const previous=visible[i-1],duration=Math.min(180,(s.t-previous.t)*700);
      const progress=easeOut((now-tail.current.start)/Math.max(1,duration));
      stamp=previous.t+(s.t-previous.t)*progress;
      value=values[i-1]+(values[i]-values[i-1])*progress;
    }
    path += `${sampleBreak(visible[i-1],s)?'M':'L'}${x(stamp).toFixed(2)} ${y(value).toFixed(2)} `;
  });
  const hoverTime=end-seconds+(hover||0)*seconds;
  const point=hover==null?null:visible.reduce((nearest,s)=>!nearest||Math.abs(s.t-hoverTime)<Math.abs(nearest.t-hoverTime)?s:nearest,null);
  const pv=point && (mode==='relative'?(point.channels[channel-1].temperature-point.channels[channel-1].setpoint)*1000:point.channels[channel-1].temperature);
  return <div className={`chart ${acquisitionPaused?'acquisition-paused':''}`} ref={container} onMouseLeave={()=>setHover(null)}>
    <svg width="100%" height={h} viewBox={`0 0 ${width} ${h}`} role="img" aria-label={`CH${channel} 最近 ${seconds} 秒${mode==='relative'?'温差':'温度'}曲线${paused?'，显示已暂停':''}`} onMouseMove={e=>{const b=e.currentTarget.getBoundingClientRect();setHover(Math.max(0,Math.min(1,(e.clientX-b.left-left)/(width-left-right))));}}>
      <defs><clipPath id={clipId}><rect x={left} y={top} width={width-left-right} height={h-top-bottom}/></clipPath></defs>
      <text x={left} y={14} className="axis-unit">{mode==='relative'?'ΔT / mK':'温度 / °C'}</text>
      {ticks.filter(val=>val>=min-1e-9&&val<=max+1e-9).map(val=><g key={val}><line x1={left} x2={width-right} y1={y(val)} y2={y(val)} className="grid-line"/><text x={left-12} y={y(val)+4} textAnchor="end" className="axis-label">{fmt(val,precision)}</text></g>)}
      {[0,1,2,3,4].map(i=><text key={i} x={x(end-seconds+seconds*i/4)} y={h-8} textAnchor={i===0?'start':i===4?'end':'middle'} className="axis-label">{i===4?(acquisitionPaused?'最后读数':'现在'):`−${seconds-seconds*i/4}s`}</text>)}
      {hasTarget && (targetVisible ? <line x1={left} x2={width-right} y1={y(base)} y2={y(base)} className="setpoint-line"/> : <text x={width-right-4} y={base>max?top+15:h-bottom-10} textAnchor="end" className="target-outside">目标 {fmt(base,mode==='relative'?1:3)} {mode==='relative'?'mK':'°C'} · {base>max?'高于':'低于'}当前范围</text>)}
      <path className="measurement-trace" d={path} clipPath={`url(#${clipId})`} fill="none" stroke={COLORS[channel-1]} strokeWidth="1.8" strokeLinejoin="round"/>
      {point && <><line x1={x(point.t)} x2={x(point.t)} y1={top} y2={h-bottom} className="cursor-line"/><circle cx={x(point.t)} cy={y(pv)} r="4" fill={COLORS[channel-1]} stroke="var(--surface)" strokeWidth="2"/></>}
    </svg>
    {point && <div className="chart-tooltip" style={{left:Math.min(width-170,Math.max(12,x(point.t)-75))}}><span>{clock(point.t)}</span><strong>{fmt(pv,mode==='relative'?2:4)} {mode==='relative'?'mK':'°C'}</strong></div>}
    {acquisitionPaused?<div className="acquisition-pause-label"><strong>PAUSED</strong><small>通道{overclockChannel}超频中</small></div>:!visible.length&&<div className="chart-empty">等待采样数据</div>}
  </div>;
}

export function CurvePanel({samples, selected, connected, serverTime, settings, simulation, acquisitionPaused=false, overclockChannel=null, recordingBadge=null, recordingContent=null, expandAction=null}) {
  const seconds=settings.display_seconds;
  const [paused,setPaused]=useState(false);
  const [relative,setRelative]=useState(false);
  const frozen=useRef([]);
  const data=(paused?frozen.current:samples).filter(s=>s.sampled_channels.includes(selected)&&Number.isFinite(s.channels[selected-1].temperature));
  const filtered=data.filter(s=>s.t>=(data.at(-1)?.t||0)-seconds).map(s=>s.channels[selected-1].temperature);
  const mean=filtered.reduce((a,b)=>a+b,0)/(filtered.length||1);
  const sigma=Math.sqrt(filtered.reduce((a,b)=>a+(b-mean)**2,0)/(filtered.length||1))*1000;
  const pp=(Math.max(...filtered)-Math.min(...filtered))*1000;
  const togglePause=()=>{if(!paused)frozen.current=samples.slice();setPaused(!paused);};
  return <section className="curve-panel">
    <div className="panel-title">
      <div className="curve-title"><h2 className="plot-channel-title">CH{selected}{simulation&&<FlaskConical size={16} aria-label="模拟数据"/>}</h2>{recordingBadge}</div>
      <div className="chart-actions">{expandAction}
        <span className="window-duration" title="曲线显示时长">{seconds} s</span>
        <button className="axis-toggle" onClick={()=>setRelative(!relative)} aria-pressed={relative} title="切换温度 / 温差">{relative?'ΔT / mK':'T / °C'}<ChevronDown size={12}/></button>
        <button className={`icon-button ${paused?'selected':''}`} aria-label={paused?'继续曲线':'暂停曲线'} title={paused?'继续曲线':'暂停曲线（后台采集继续）'} aria-pressed={paused} onClick={togglePause}>{paused?<Play size={16}/>:<Pause size={16}/>}</button>
      </div>
    </div>
    <TemperatureChart samples={data} channel={selected} seconds={seconds} paused={paused} connected={connected} smooth={settings.smooth_plot} serverTime={serverTime} acquisitionPaused={acquisitionPaused} overclockChannel={overclockChannel} mode={relative?'relative':'absolute'}/>
    <div className="chart-stats"><span>σT <strong>{fmt(filtered.length?sigma:NaN,2)}</strong> mK</span><span>峰峰值 <strong>{fmt(pp,2)}</strong> mK</span><span className={`plot-status ${connected&&!paused&&!acquisitionPaused?'running':''}`} role="status" title={acquisitionPaused?'本机已暂停该通道采样，TEC输出状态未改变':paused?'曲线显示已暂停；后台采集继续':connected?'曲线正在更新':'采样数据未更新'}><i/>{acquisitionPaused?'PAUSED':connected&&!paused?'sampling':'stopped'}</span></div>
    {recordingContent}
  </section>;
}

export function ChannelControls({channel:c, connected, send, simulation}) {
  const pidText=()=>c.pid.map(v=>Number.isFinite(v)?String(v):'');
  const [pid,setPid]=useState(pidText);
  const [dirty,setDirty]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{if(!dirty)setPid(pidText());},[c.pid.join(','),dirty]);
  useEffect(()=>{setPid(pidText());setDirty(false);setError('');},[c.id]);
  const locked=!connected||!c.writable||busy;
  const execute=async(type,params)=>{setBusy(true);setError('');try{await send(c.id,type,params);return true;}catch(e){setError(e.message);return false;}finally{setBusy(false);}};
  return <aside className="controls-panel"><div className="panel-title"><h2><Settings2 size={18}/>通道控制</h2></div>
    <div className="control-output"><div><span>TEC 输出</span><strong>{c.output==null?'等待读取':c.output?'已开启':'已关闭'}</strong></div><button className={`output-switch ${c.output?'on':''}`} role="switch" aria-label={`CH${c.id} ${simulation?'模拟':'TEC'}输出`} aria-checked={Boolean(c.output)} disabled={locked} onClick={()=>execute('output',{enabled:!c.output})}><span/></button></div>
    <SetpointForm channel={c} disabled={locked} send={send}/>
    <div className="control-group"><span className="control-mode-label">控制模式</span><div className="segmented mode-selector">{['T','R','ITE'].map(mode=><button key={mode} aria-pressed={c.mode===mode} disabled={locked||c.output} className={mode===c.mode?'active':''} onClick={()=>execute('mode',{mode})}>{mode}</button>)}</div>{c.output && c.writable && <span className="input-hint">关闭输出后可切换模式</span>}</div>
    <form className="control-group pid-form" onSubmit={async e=>{e.preventDefault();if(pid.some(v=>!Number.isFinite(num(v)))){setError('PID 请输入有效数值');return;}if(await execute('pid',{values:pid.map(num)}))setDirty(false);}}><div className="group-title"><h3>PID 参数</h3><span>设备内环</span></div><div className="pid-inputs">{['P','I','D'].map((label,i)=><label key={label}>{label}<input type="number" step="0.1" aria-label={`PID ${label}`} value={pid[i]} disabled={locked} onChange={e=>{setPid(pid.map((v,j)=>i===j?e.target.value:v));setDirty(true);}}/></label>)}</div><button className="button secondary" type="submit" disabled={locked||!dirty}>{busy?'写入中…':'应用 PID'}</button></form>
    {error && <p className="field-error" role="alert">{error}</p>}
  </aside>;
}


export function ChannelReadings({channel, acquisitionState, connected, thermalColors=true}) {
  const thermal=thermalState(channel.current,channel.voltage,channel.output);
  const tint=thermalColors&&connected&&!acquisitionState.paused&&!acquisitionState.stale&&thermal.level>0;
  const thermalStyle=tint?{'--thermal-fill':`${thermal.fill.toFixed(2)}%`,'--thermal-edge':`${thermal.edge.toFixed(2)}%`}:undefined;
  return <section className="live-strip thermal-readings" data-thermal={tint?thermal.direction:undefined} style={thermalStyle}><div className="detail-temperature"><span>{acquisitionState.paused||acquisitionState.stale?'最后读数':'当前温度'}</span>{(acquisitionState.paused||acquisitionState.stale)&&<small className="last-reading">{clock(acquisitionState.last)}</small>}<strong>{fmt(channel.temperature,4)}<small>°C</small></strong><Status on={channel.output} current={channel.current} disconnected={!connected||(!acquisitionState.paused&&acquisitionState.stale)} last={acquisitionState.paused}/></div><div className="detail-metrics"><Metric label="温度偏差 ΔT" value={channel.mode==='T'&&Number.isFinite(channel.temperature)&&Number.isFinite(channel.setpoints.T)?fmt((channel.temperature-channel.setpoints.T)*1000,2):'—'} suffix="mK"/><Metric label="电阻 R" value={fmt(channel.resistance)} suffix="kΩ"/><Metric label="电流 I" value={fmt(channel.current,3)} suffix="A"/><Metric label="电压 V" value={fmt(channel.voltage,3)} suffix="V"/><PowerMetric detail current={channel.current} voltage={channel.voltage}/></div></section>;
}
export function ChannelTabs({channels,selected,onSelect}) {
  return <div className="detail-nav"><div className="channel-tabs" aria-label="选择通道">{channels.map(c=><button key={c.id} className={selected===c.id?'active':''} aria-pressed={selected===c.id} onClick={()=>onSelect(c.id)}><i style={{background:COLORS[c.id-1]}}/>CH {c.id}</button>)}</div></div>;
}
export function SamplingFooter({state,view,selected,stale,end}) {
  const current=acquisition(state,selected),last=lastSampleTime(state,view,selected);
  return <footer><span><i className={`footer-dot ${stale?'offline':''}`}/>{current.paused&&view==='detail'?'最后读数':stale?'最后采样':'更新于'} {clock(last)}{state.logging?.active&&<span className="footer-log-status"><span className="footer-separator">/</span>日志已开启</span>}</span><span>{end}</span></footer>;
}
