import React, {useEffect, useRef, useState} from 'react';
import {Circle, Square, Clock3, ShieldCheck} from 'lucide-react';

export const durationText = seconds => {
  const n=Math.max(0,Math.floor(seconds||0));
  return [Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(v=>String(v).padStart(2,'0')).join(':');
};
export function useRecordingClock(recording) {
  const [,tick]=useState(0);
  const anchor=useRef({key:null,value:0,at:performance.now()});
  if(recording && (anchor.current.key!==`${recording.channel}:${recording.started_at}`||anchor.current.value!==recording.elapsed_seconds)){
    anchor.current={key:`${recording.channel}:${recording.started_at}`,value:recording.elapsed_seconds,at:performance.now()};
  }
  useEffect(()=>{if(!recording?.active)return;const id=setInterval(()=>tick(t=>t+1),250);return ()=>clearInterval(id);},[recording?.active]);
  return (recording?.elapsed_seconds||0)+(recording?.active?(performance.now()-anchor.current.at)/1000:0);
}
export function RecordingPill({elapsed}) {
  return <span className="recording-pill"><i/>录制中 <time>{durationText(elapsed)}</time></span>;
}
export function RecordingBar({recording:r,elapsed,connected,sampleHz,settings,action}) {
  const [busy,setBusy]=useState(false);
  return <div className={`recording-bar ${r.active?'is-recording':''}`}>
    <div className="recording-toolbar"><div className="recording-title"><span className={`record-indicator ${r.active?'recording':''}`}/><div><strong>{r.active?`正在录制 CH${r.channel}`:`CH${r.channel} 本地录制`}</strong><span>仅当前通道 · CSV · {r.active?r.sample_hz:sampleHz} Hz</span></div></div><div className="recording-clock"><Clock3 size={15}/><div><span>录制时间</span><time aria-label="录制时间">{durationText(elapsed)}</time></div></div><button className={r.active?'button record-stop':'button primary'} disabled={busy||(!connected&&!r.active)} onClick={async()=>{setBusy(true);try{await action(!r.active);}finally{setBusy(false);}}}>{r.active?<Square size={14}/>:<Circle size={14}/>} {busy?'处理中…':r.active?'停止录制':'开始录制'}</button></div>
    <div className="recording-meta"><span title={settings.recording_dir}>{settings.recording_dir}</span><span><ShieldCheck size={13}/>关闭页面不停止录制</span></div>
    {r.filename && <div className="recording-files"><span>{r.error?'文件可能不完整':r.active?'当前文件':'已保存'} · {r.rows} 行</span><span>{r.filename}</span></div>}
    {r.error && <p className="field-error" role="alert">{r.error}</p>}
  </div>;
}
