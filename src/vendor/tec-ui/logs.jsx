// React default import is required by the Windows classic JSX compiler.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React,{useEffect,useMemo,useRef,useState} from 'react';
import {ArrowDownToLine} from 'lucide-react';
import {COLORS} from './components.jsx';
import {CHANNELS,METRICS,TABLE_COLUMNS,makeRange,isoInput,axisClock,fmt,stamp,nearestPoint,tableCell} from './log_model.js';
import {TableColumnPicker,useTableColumns} from './log_columns.jsx';
import {DateTimeField} from './datetime.jsx';

/** Read-only log display. The host owns authentication, storage, queries and export URLs. */
export function LogSeriesChart({data,range,metric,loading,error}){
  const [hover,setHover]=useState(null);
  const plotRef=useRef(null),[plotWidth,setPlotWidth]=useState(960);
  useEffect(()=>{const observer=new ResizeObserver(entries=>setPlotWidth(Math.max(300,entries[0].contentRect.width)));observer.observe(plotRef.current);return()=>observer.disconnect();},[]);
  const {label,unit}=METRICS[metric];
  const series=data?.series||{};
  const all=Object.values(series).flat(2);
  const values=all.map(p=>p[metric]).filter(Number.isFinite);
  const minimum=values.length?Math.min(...values):0,maximum=values.length?Math.max(...values):1;
  const pad=Math.max((maximum-minimum)*.12,metric==='temperature_c'?.015:.008);
  const lo=minimum-pad,hi=maximum+pad;
  const width=plotWidth,height=width<500?320:420,left=width<500?56:76,right=18,top=30,bottom=48;
  const x=t=>left+(t-range.start)/(range.end-range.start)*(width-left-right);
  const y=v=>top+(hi-v)/(hi-lo)*(height-top-bottom);
  const nearest=hover==null?null:nearestPoint(series,range.start+hover*(range.end-range.start));
  return <div className="history-plot" ref={plotRef} aria-busy={loading}>
    <svg role="img" aria-label={`${label}历史曲线，单位${unit}，${Object.keys(series).map(ch=>'CH'+ch).join('、')}；暂停和缺测处断开`} viewBox={`0 0 ${width} ${height}`} onPointerLeave={()=>setHover(null)} onPointerMove={event=>{
      const rect=event.currentTarget.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*width;
      setHover(Math.max(0,Math.min(1,(px-left)/(width-left-right))));
    }}>
      <text x={left} y="14" className="axis-unit">{label} ({unit})</text>
      {[0,1,2,3,4].map(i=>{const value=lo+(hi-lo)*i/4;return <g key={i}><line x1={left} x2={width-right} y1={y(value)} y2={y(value)} className="grid-line"/><text x={left-12} y={y(value)+4} textAnchor="end">{value.toFixed(3)}</text></g>;})}
      {(width<500?[0,2,4]:[0,1,2,3,4]).map(i=>{const t=range.start+(range.end-range.start)*i/4,long=range.end-range.start>=86400000;return <text key={i} x={x(t)} y={height-(long?25:18)} textAnchor={i===0?'start':i===4?'end':'middle'}>{long?<><tspan x={x(t)}>{isoInput(t).slice(5,10)}</tspan><tspan x={x(t)} dy="13">{axisClock(t).slice(0,5)}</tspan></>:axisClock(t)}</text>;})}
      {Object.entries(series).map(([ch,paths])=>paths.map((points,index)=><g key={`${ch}-${index}`}>
        <path className="history-line" data-channel={ch} d={points.map((p,i)=>`${i?'L':'M'}${x(p.ts_ms).toFixed(2)},${y(p[metric]).toFixed(2)}`).join(' ')} stroke={COLORS[ch-1]}/>
        {points.length===1&&<circle cx={x(points[0].ts_ms)} cy={y(points[0][metric])} r="2.6" fill={COLORS[ch-1]}/>}
      </g>))}
      {nearest&&<g><line x1={x(nearest.point.ts_ms)} x2={x(nearest.point.ts_ms)} y1={top} y2={height-bottom} className="hover-line"/><circle cx={x(nearest.point.ts_ms)} cy={y(nearest.point[metric])} r="4" fill={COLORS[nearest.channel-1]}/></g>}
    </svg>
    {error?<div className="plot-empty" role="alert">{error}</div>:!all.length&&!loading?<div className="plot-empty">这个时间范围没有有效采样点</div>:null}
    <div className="plot-caption" aria-live="polite">{nearest?<><span className="mono">{stamp(nearest.point.ts_ms)} UTC+8</span><span>CH{nearest.channel} · {fmt(nearest.point[metric])} {unit}</span></>:<><span>横轴 UTC+8 · 暂停 / 缺测留断点</span><span>{loading?'正在读取…':`${data?.raw_count?.toLocaleString()||0} 条记录 · 保留峰谷`}</span></>}</div>
  </div>;
}

export function LogPage({transport,serverMs,channelIds=CHANNELS,storageSlot=null,retentionHours=null,refreshKey=0}){
  const available=channelIds.filter((id,i,all)=>Number.isInteger(id)&&id>=1&&id<=4&&all.indexOf(id)===i);
  const availableKey=available.join(',');
  const [preset,setPreset]=useState('10');
  const [anchor,setAnchor]=useState(Math.trunc(serverMs)+1);
  const [start,setStart]=useState(isoInput(Math.trunc(serverMs)-600000));
  const [end,setEnd]=useState(isoInput(Math.trunc(serverMs)+1));
  const [customRange,setCustomRange]=useState(null);
  const [metric,setMetric]=useState('temperature_c');
  const [chosenChannels,setChannels]=useState(available);
  const [chosenTableChannel,setTableChannel]=useState(available[0]??1);
  const channels=available.filter(id=>chosenChannels.includes(id));
  const selectedChannels=channels.length?channels:available;
  const tableChannel=available.includes(chosenTableChannel)?chosenTableChannel:available[0]??1;
  const [page,setPage]=useState(0);
  const [data,setData]=useState(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [formError,setFormError]=useState('');
  const [exportBusy,setExportBusy]=useState(false),[exportError,setExportError]=useState('');
  const {columns,setColumns,preferenceError}=useTableColumns();
  const visibleColumns=TABLE_COLUMNS.filter(column=>columns.includes(column.key));
  const range=useMemo(()=>preset==='custom'&&customRange?customRange:makeRange(preset==='custom'?'10':preset,anchor),[preset,customRange,anchor]);
  const channelKey=selectedChannels.join(',');
  useEffect(()=>{
    const controller=new AbortController();
    setLoading(true);setError('');setData(null);
    if(!availableKey){setLoading(false);return ()=>controller.abort();}
    Promise.all([
      transport.series({start:range.start,end:range.end,channels:channelKey.split(',').map(Number),metric,signal:controller.signal}),
      transport.table({start:range.start,end:range.end,channel:tableChannel,page,signal:controller.signal})
    ]).then(([chart,table])=>{if(controller.signal.aborted)return;if([chart,table].some(result=>result.start_ms!==range.start||result.end_ms!==range.end))throw Error('图表与表格返回的时间范围不一致');setData({chart,table});}).catch(e=>{if(!controller.signal.aborted&&e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[transport,range.start,range.end,metric,channelKey,tableChannel,page,availableKey,refreshKey]);
  const total=data?.table.total||0,pages=Math.max(1,Math.ceil(total/50));
  const changePreset=value=>{
    setAnchor(Math.trunc(serverMs)+1);setPage(0);setPreset(value);setFormError('');
    if(value==='custom'){setStart(isoInput(range.start));setEnd(isoInput(range.end));setCustomRange({...range});}
  };
  const apply=event=>{event.preventDefault();try{const range=makeRange('custom',0,start,end);if(retentionHours&&range.end-range.start>retentionHours*3600000)throw Error(`一次最多查看 ${retentionHours} 小时，请缩小时间范围。`);setCustomRange(range);setPage(0);setFormError('');}catch(e){setFormError(e.message);}};
  const exportHref=transport.exportUrl?.({...range,channel:tableChannel});
  const exportCsv=async()=>{if(exportBusy)return;setExportBusy(true);setExportError('');try{await transport.exportCsv({...range,channel:tableChannel});}catch(error){setExportError(error.message);}finally{setExportBusy(false);}};
  return <section className="logs-page tec-log-page" aria-label="日志查询">
    {storageSlot}
    <div className="range-toolbar">
      <label className="field-inline">显示时间<select aria-label="显示时间" value={preset} onChange={e=>changePreset(e.target.value)}><option value="2">最近 2 分钟</option><option value="10">最近 10 分钟</option><option value="60">最近 1 小时</option><option value="1440">最近 24 小时</option>{retentionHours===72&&<option value="4320">最近 72 小时</option>}<option value="custom">自定义起止</option></select></label>
      <span className="range-text mono">{stamp(range.start)} — {stamp(range.end)} <b>UTC+8</b></span>
    </div>
    {preset==='custom'&&<form className="custom-range" onSubmit={apply}><DateTimeField label="开始时间" value={start} onChange={setStart}/><DateTimeField label="结束时间" value={end} onChange={setEnd}/><button className="button primary" type="submit">应用时间范围</button></form>}
    {formError&&<div className="error-banner" role="alert">{formError}</div>}
    {error&&<div className="error-banner" role="alert">读取日志失败：{error}。调整时间范围或刷新重试。</div>}
    <div className="log-layout">
      <section className="log-chart-panel" aria-label="多通道曲线">
        <div className="log-panel-heading"><h2>历史曲线</h2><div className="metric-switch" role="group" aria-label="曲线量">{Object.entries(METRICS).map(([key,value])=><button key={key} aria-pressed={metric===key} onClick={()=>setMetric(key)}>{value.label} <span>{value.unit}</span></button>)}</div></div>
        <fieldset className="curve-channels"><legend>图表通道</legend>{available.map(ch=><label key={ch} style={{'--channel-color':COLORS[ch-1]}}><input type="checkbox" checked={selectedChannels.includes(ch)} disabled={selectedChannels.length===1&&selectedChannels[0]===ch} onChange={()=>setChannels(()=>selectedChannels.includes(ch)?selectedChannels.filter(v=>v!==ch):[...selectedChannels,ch].sort())}/><i/>CH{ch}</label>)}</fieldset>
        <LogSeriesChart data={data?.chart} range={range} metric={metric} loading={loading} error={error}/>
      </section>
      <section className="log-table-panel" aria-label="原始记录">
        <div className="log-panel-heading"><div><h2>原始记录</h2><span className="section-note">单通道 · 每页 50 条</span></div><label className="table-channel"><span className="sr-only">表格通道</span><select aria-label="表格通道" value={tableChannel} onChange={e=>{setTableChannel(Number(e.target.value));setPage(0);}}>{available.map(ch=><option key={ch} value={ch}>CH{ch}</option>)}</select></label></div>
        <div className="table-meta"><span>{loading?'正在读取…':`${total.toLocaleString()} 条 · 时间升序`}</span><div className="table-actions"><>{transport.exportCsv?<button type="button" className="export-link" disabled={exportBusy||!available.length} aria-busy={exportBusy} onClick={exportCsv}><ArrowDownToLine size={14}/>{exportBusy?'导出中…':'导出 CSV'}</button>:<a className="export-link" href={exportHref} title="导出该通道完整原始字段，保留 UTC 时间，不受显示列影响" download><ArrowDownToLine size={14}/>导出 CSV</a>}</><TableColumnPicker columns={columns} setColumns={setColumns} preferenceError={preferenceError}/></div></div>
        {exportError&&<p role="alert" className="field-error">导出失败：{exportError}</p>}
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Keyboard users need native overflow scrolling. */}
        <div className="log-table-scroll" role="region" tabIndex="0" aria-label="原始记录滚动区域" aria-busy={loading}>
          <table style={{minWidth:132+visibleColumns.reduce((sum,column)=>sum+column.width,0)}}><colgroup><col style={{width:132}}/>{visibleColumns.map(column=><col key={column.key} style={{width:column.width}}/>)}</colgroup><thead><tr><th scope="col">时间 <span>UTC+8</span></th>{visibleColumns.map(column=><th key={column.key} scope="col" title={column.note}>{column.label} <span>{column.unit}</span></th>)}</tr></thead><tbody>
            {data?.table.rows.map(row=><tr key={`${row.session_id}-${row.seq}-${row.channel}`} data-quality={row.quality} title={`会话 ${row.session_id} · 序号 ${row.seq} · ${row.quality}`}><td><time dateTime={row.utc}><span className="row-date">{stamp(row.ts_ms).slice(0,10)}</span>{stamp(row.ts_ms).slice(11)}</time>{row.quality!=='ok'&&<span className="quality-label">{row.quality==='paused'?'暂停':'缺测'}</span>}</td>{visibleColumns.map(column=><td key={column.key}>{tableCell(row,column)}</td>)}</tr>)}
            {!data?.table.rows.length&&<tr><td colSpan={1+visibleColumns.length} className="table-empty">{loading?'正在读取原始记录…':error?'读取失败':'这个范围没有记录'}</td></tr>}
          </tbody></table>
        </div>
        <div className="pagination"><button className="button secondary" disabled={page===0||loading} onClick={()=>setPage(old=>old-1)}>上一页</button><span>{page+1} / {pages}</span><button className="button secondary" disabled={page+1>=pages||loading} onClick={()=>setPage(old=>old+1)}>下一页</button></div>
      </section>
    </div>
  </section>;
}

