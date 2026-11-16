import React, {useEffect, useRef, useState} from 'react';
import {Settings, X, Folder, Gauge, FileText, Clock3, Waves, PlugZap, LockKeyhole, Globe, Palette,Download} from 'lucide-react';
import {ToggleSwitch} from '../vendor/tec-ui/components.jsx';
import {CloudLinkSetting} from './CloudLinkSetting.jsx';
import {NativeLogSettings} from './NativeLogSettings.jsx';
import {UpdatesPanel} from './UpdatesPanel.jsx';
const sections=[['sampling','采样与显示',Gauge],['device','设备',PlugZap],['logging','日志',Folder],['bridge','云桥接',Globe],['updates','版本与更新',Download]];
export function SettingsDialog({settings, state, channelIds=[], logging, save, close, saveName, connection, cloudEndpoint, thermalColors=true,logTransport}) {
  const dialog=useRef(null),tabs=useRef(null);
  const [section,setSection]=useState('sampling');
  const [draft,setDraft]=useState({read_only:false,device_port:'COM8',baud_rate:38400,update_interval_ms:1,log_enabled:false,overclock_enabled:false,overclock_channel:1,overclock_target:2,...settings});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [cloudBusy,setCloudBusy]=useState(false);
  const [logDraft,setLogDraft]=useState(null);
  const [draftThermalColors,setDraftThermalColors]=useState(thermalColors);
  useEffect(()=>{const d=dialog.current;d.showModal();return ()=>d.close();},[]);
  const update=(key,value)=>{setDraft(s=>({...s,[key]:value}));setError('');};
  const connectionPending=['connecting','disconnecting'].includes(state?.connection_state);
  const applyConnection=async()=>{
    setBusy(true);setError('');
    try{await connection(!state.connected,draft.device_port,draft.baud_rate);}
    catch(e){setError(e.message);}
    finally{setBusy(false);}
  };
  const applyName=async()=>{
    setBusy(true);setError('');
    try{const name=await saveName(draft.device_name);update('device_name',name);}
    catch(e){setError(e.message);}
    finally{setBusy(false);}
  };
  const tabKey=e=>{
    if(cloudBusy)return;
    if(!['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();const index=sections.findIndex(([id])=>id===section);
    const next=e.key==='Home'?0:e.key==='End'?sections.length-1:(index+(['ArrowUp','ArrowLeft'].includes(e.key)?-1:1)+sections.length)%sections.length;
    setSection(sections[next][0]);tabs.current.querySelectorAll('[role=tab]')[next].focus();
  };
  return <dialog ref={dialog} className="settings-dialog settings-workspace" data-section={section} aria-labelledby="settings-title" onClose={event=>{if(event.target===event.currentTarget)close();}} onCancel={e=>{if(e.target===e.currentTarget&&(busy||cloudBusy))e.preventDefault();}}>
    <form onSubmit={async e=>{e.preventDefault();if(cloudBusy)return;setBusy(true);setError('');try{await save({...draft,log_enabled:state.settings.log_enabled,recording_dir:state.settings.recording_dir,filename_prefix:state.settings.filename_prefix},{thermalColors:draftThermalColors,nativeStorage:logDraft});close();}catch(e){setError(e.message);}finally{setBusy(false);}}}>
      <div className="settings-heading"><h2 id="settings-title"><Settings size={19}/>设置</h2><button type="button" className="icon-button" aria-label="关闭设置" onClick={close} disabled={busy||cloudBusy}><X size={17}/></button></div>
      <div className="settings-layout">
        <div className="settings-categories" role="tablist" aria-label="设置类别" ref={tabs} onKeyDown={tabKey}>{sections.map(([id,label,Icon])=><button type="button" role="tab" id={`settings-tab-${id}`} aria-controls="settings-panel" aria-selected={section===id} tabIndex={section===id?0:-1} onClick={()=>setSection(id)} disabled={cloudBusy} key={id}><Icon size={16}/>{label}</button>)}</div>
        <section className="settings-content" role="tabpanel" id="settings-panel" aria-labelledby={`settings-tab-${section}`} tabIndex={0}>
          <h3>{sections.find(([id])=>id===section)[1]}</h3>
          {section==='sampling'&&<>
            {!draft.overclock_enabled&&<div className="settings-field"><label htmlFor="minimum-sleep"><Clock3 size={16}/>读后最小休眠</label><div className="duration-input"><input id="minimum-sleep" type="number" min="1" max="5000" step="1" value={draft.update_interval_ms} onChange={e=>update('update_interval_ms',e.target.value===''?'':Number(e.target.value))} disabled={busy} required/><span>ms</span></div></div>}
            <div className="settings-field settings-switch-row"><label htmlFor="overclock-enabled"><Gauge size={16}/>单通道超频</label><ToggleSwitch id="overclock-enabled" label="单通道超频" checked={draft.overclock_enabled} onChange={value=>update('overclock_enabled',value)} disabled={busy}/></div>
            {draft.overclock_enabled&&<div className="overclock-options">
              <label htmlFor="overclock-channel">超频通道</label><select id="overclock-channel" value={draft.overclock_channel} onChange={e=>update('overclock_channel',Number(e.target.value))} disabled={busy}>{channelIds.map(ch=><option key={ch} value={ch}>CH{ch}</option>)}</select>
              <div className="settings-label"><label htmlFor="sample-rate">采样目标</label><output htmlFor="sample-rate">{draft.overclock_target==='max'?'MAX':<>{draft.overclock_target}<small> Hz</small></>}</output></div>
              <input id="sample-rate" type="range" min="2" max="11" step="1" value={draft.overclock_target==='max'?11:draft.overclock_target} onChange={e=>update('overclock_target',Number(e.target.value)===11?'max':Number(e.target.value))} aria-valuetext={draft.overclock_target==='max'?'MAX，自适应安全速率':`${draft.overclock_target} Hz`} disabled={busy}/>
              <div className="range-ticks">{[2,3,4,5,6,7,8,9,10,'MAX'].map(n=><span key={n}>{n}</span>)}</div>
            </div>}
            <div className="settings-field"><label htmlFor="display-seconds"><Clock3 size={16}/>曲线显示时长</label><div className="duration-input"><input id="display-seconds" type="number" min="10" max="300" step="1" value={draft.display_seconds} onChange={e=>update('display_seconds',e.target.value===''?'':Number(e.target.value))} disabled={busy} required title="10–300 s"/><span>s</span></div></div>
            <div className="settings-field settings-switch-row"><label htmlFor="smooth-plot"><Waves size={16}/>平滑曲线</label><ToggleSwitch id="smooth-plot" label="平滑曲线" checked={draft.smooth_plot} onChange={value=>update('smooth_plot',value)} disabled={busy}/></div>
            <div className="settings-field settings-switch-row"><label htmlFor="thermal-colors"><Palette size={16}/>温度多彩显示</label><ToggleSwitch id="thermal-colors" label="温度多彩显示" checked={draftThermalColors} onChange={setDraftThermalColors} disabled={busy}/></div>
          </>}
          {section==='device'&&<>
            <div className="settings-field"><label htmlFor="device-name">本机名称</label><div className="device-name-row"><input id="device-name" type="text" value={draft.device_name??''} onChange={e=>update('device_name',e.target.value)} maxLength={96} disabled={busy} spellCheck="false"/><button type="button" className="button secondary" onClick={applyName} disabled={busy||cloudBusy||draft.device_name?.trim()===settings.device_name}>保存名称</button></div></div>
            <div className="device-connection-row"><div className="settings-field"><label htmlFor="device-port">设备端口</label><input id="device-port" type="text" value={draft.device_port} onChange={e=>update('device_port',e.target.value.toUpperCase())} pattern="COM[1-9][0-9]{0,3}" maxLength={7} placeholder="COM8" disabled={busy||connectionPending} required spellCheck="false"/></div><div className="settings-field"><label htmlFor="host-baud">波特率</label><select id="host-baud" value={draft.baud_rate} onChange={e=>update('baud_rate',Number(e.target.value))} disabled={busy||connectionPending}>{[300,1200,2400,4800,9600,19200,38400,57600,115200].map(n=><option key={n} value={n}>{n}</option>)}</select></div><button type="button" className={`button ${state?.connected?'secondary':'primary'}`} onClick={applyConnection} disabled={busy||cloudBusy||connectionPending}><PlugZap size={16}/>{connectionPending?'处理中…':state?.connected?'断开':'连接'}</button></div>
            <div className="settings-field settings-switch-row"><label htmlFor="read-only"><LockKeyhole size={16}/>全部通道只读</label><ToggleSwitch id="read-only" label="全部通道只读" checked={draft.read_only} onChange={value=>update('read_only',value)} disabled={busy}/></div>
            <dl className="device-information"><div><dt>型号</dt><dd>{state?.device?.model??'尚未读取'}</dd></div><div><dt>通道</dt><dd>{state?.channel_count||'—'}</dd></div><div><dt>连接状态</dt><dd>{connectionPending?'处理中':state?.connected?'已连接':'未连接'}</dd></div><div><dt>实际端口</dt><dd>{state?.connected?`${state.device?.port??'—'} · ${state.device?.baud_rate??'—'}`:'—'}</dd></div></dl>
          </>}
          {section==='logging'&&(logTransport?<NativeLogSettings transport={logTransport} draft={logDraft} onDraftChange={setLogDraft} onBusyChange={setCloudBusy}/>:<>
            <div className="settings-field settings-switch-row"><label htmlFor="log-enabled"><FileText size={16}/>后台日志</label><ToggleSwitch id="log-enabled" label="后台日志" checked={draft.log_enabled} onChange={value=>update('log_enabled',value)} disabled={busy}/></div>
            <div className="settings-field"><label htmlFor="recording-dir"><Folder size={16}/>日志保存目录</label><input id="recording-dir" type="text" value={draft.recording_dir} onChange={e=>update('recording_dir',e.target.value)} disabled={busy} required spellCheck="false"/></div>
            <div className="settings-field"><label htmlFor="filename-prefix"><FileText size={16}/>文件名前缀</label><input id="filename-prefix" type="text" value={draft.filename_prefix} onChange={e=>update('filename_prefix',e.target.value)} disabled={busy} required maxLength={80} spellCheck="false"/></div>
            {logging?.error&&<p className="field-error" role="alert">{logging.error}</p>}
          </>)}
          {section==='bridge'&&<CloudLinkSetting endpoint={cloudEndpoint} onBusyChange={setCloudBusy}/>}
          {section==='updates'&&logTransport?.local&&<UpdatesPanel transport={logTransport.local}/>}
        </section>
      </div>
      {error&&<p className="field-error" role="alert">{error}</p>}
      <div className="settings-footer"><button type="button" className="button secondary" onClick={close} disabled={busy||cloudBusy}>取消</button><button className="button primary" type="submit" disabled={busy||cloudBusy}>{busy?'保存中…':'保存设置'}</button></div>
    </form>
  </dialog>;
}

