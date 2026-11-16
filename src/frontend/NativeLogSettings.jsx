import React,{useEffect,useRef,useState} from 'react';
import {Folder,FileText,Timer} from 'lucide-react';
import {ToggleSwitch} from '../vendor/tec-ui/components.jsx';

export function NativeLogSettings({transport,compact=false,onBusyChange,draft,onDraftChange}){
  const [state,setState]=useState(null),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const [directory,setDirectory]=useState(draft?.directory??''),[prefix,setPrefix]=useState(draft?.prefix??'');
  const draftRef=useRef(draft);draftRef.current=draft;
  const dirty=useRef(false);
  const accept=next=>{
    setState(next);
    if(!dirty.current&&!draftRef.current){setDirectory(next.storage.directory);setPrefix(next.storage.prefix);onDraftChange?.({...next.storage,expected_directory:next.storage.directory,expected_prefix:next.storage.prefix});}
  };
  useEffect(()=>{
    let disposed=false,timer;const controller=new AbortController();
    const poll=async()=>{
      try{const next=await transport.state(controller.signal);if(!disposed){accept(next);setError('');}}
      catch(e){if(!disposed)setError(e.message);}
      if(!disposed)timer=setTimeout(poll,2000);
    };
    poll();return()=>{disposed=true;clearTimeout(timer);controller.abort();};
  },[transport]);
  const act=async(operation,success)=>{
    setBusy(true);onBusyChange?.(true);setError('');setMessage('');
    try{await operation();accept(await transport.state());setMessage(success);}
    catch(e){setError(e.message);}
    finally{setBusy(false);onBusyChange?.(false);}
  };
  const log=state?.logging,mirror=state?.csv_mirror;
  const content=<>
    <div className="settings-field settings-switch-row"><label htmlFor={`native-log-enabled-${compact}`}><FileText size={16}/>后台日志</label><ToggleSwitch id={`native-log-enabled-${compact}`} label="后台日志" checked={Boolean(log?.active)} disabled={!state||busy} onChange={enabled=>act(()=>transport.enabled(enabled),enabled?'已开始记录':'已关闭日志')}/></div>
    <div className="settings-field"><label htmlFor={`native-log-interval-${compact}`}><Timer size={16}/>日志记录间隔</label><select id={`native-log-interval-${compact}`} disabled={!state||busy} value={state?.log_interval.interval_seconds??0} onChange={e=>act(()=>transport.interval(Number(e.target.value)),'记录间隔已保存')}><option value="0">跟随采样率</option><option value="1">1 秒</option><option value="2">2 秒</option><option value="5">5 秒</option></select></div>
    <p className="native-log-note">仅影响日志存储，采样、实时显示和控制不变。</p>
    <div className="settings-field"><label htmlFor={`native-log-directory-${compact}`}><Folder size={16}/>CSV 日志目录</label><input id={`native-log-directory-${compact}`} value={directory} onChange={e=>{dirty.current=true;setDirectory(e.target.value);onDraftChange?.({...draftRef.current,directory:e.target.value});}} disabled={compact||!state||busy||log?.active} spellCheck="false"/></div>
    <div className="settings-field"><label htmlFor={`native-log-prefix-${compact}`}><FileText size={16}/>文件名前缀</label><input id={`native-log-prefix-${compact}`} value={prefix} onChange={e=>{dirty.current=true;setPrefix(e.target.value);onDraftChange?.({...draftRef.current,prefix:e.target.value});}} disabled={compact||!state||busy||log?.active} maxLength={80} spellCheck="false"/></div>
    <p className="native-log-note">{log?.active?'关闭日志后可更换保存位置。':'更换位置会补写数据库中已提交的记录，原文件保留。'}</p>
    {message&&<p className="native-log-note" role="status">{message}</p>}
  </>;
  return <section className={`native-log-storage ${compact?'is-compact':''}`} aria-label="本地日志存储">
    <div className="native-log-summary"><strong>{log?.error?'日志故障':log?.active?'正在记录':'日志已关闭'}</strong><span>已保存 {(mirror?.mirrored??0).toLocaleString()} 条{mirror?.pending?` · CSV 待补 ${mirror.pending.toLocaleString()} 条`:''}{log?.pending?` · 待提交 ${log.pending} 条`:''}</span></div>
    {compact?<details><summary>日志保存设置</summary><div className="native-log-fields">{content}</div></details>:content}
    {(error||log?.error||mirror?.error)&&<p className="field-error" role="alert">{error||log?.error||mirror?.error}</p>}
  </section>;
}
