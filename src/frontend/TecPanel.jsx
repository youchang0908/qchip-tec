import React, {useEffect, useRef, useState, useMemo} from 'react';
import {Activity, Check, FileText, Cpu, FlaskConical, LayoutGrid, LockKeyhole, PlugZap, RefreshCw, Settings, X} from 'lucide-react';
import {ChannelCard, ChannelControls, ChannelReadings, ChannelTabs, SamplingFooter} from '../vendor/tec-ui/components.jsx';
import './styles.css';
import {SettingsDialog} from './SettingsDialog.jsx';
import mascot from './assets/tec-mascot-animated.svg';
import appIcon from './assets/tec-app-icon.svg';
import {NativeSharedCurve} from './NativeSharedCurve.jsx';
import {NativeLogPage} from './NativeLogPage.jsx';
import {LocalLogTransport} from './local-log-transport.js';
import {samplingView, acquisition, holdPausedHistory} from './sampling.js';
import {CloudBridgeStatus} from './CloudBridgeStatus.jsx';
const NATIVE_VERSION=typeof __QCHIP_DESKTOP_VERSION__==='undefined'?'0.12.0':__QCHIP_DESKTOP_VERSION__;
function Mark(){return <span className="brand-mark"><img src={appIcon} width="36" height="36" alt=""/></span>;}

export function TecPanel({transport,cloudEndpoint}) {
  const logTransport=useMemo(()=>new LocalLogTransport(transport),[transport]);
  const [state,setState]=useState(null);
  const [samples,setSamples]=useState([]);
  const [requestedSelected,setSelected]=useState(1);
  const selected=state?.channels.some(c=>c.id===requestedSelected)?requestedSelected:(state?.channels[0]?.id??1);
  const [view,setView]=useState('overview');
  const [chartOnly,setChartOnly]=useState(false);
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [thermalColors,setThermalColors]=useState(true);
  const [uiPreferencesReady,setUiPreferencesReady]=useState(false);
  const [uiPreferencesError,setUiPreferencesError]=useState('');
  const [error,setError]=useState('');
  const [toast,setToast]=useState(null);
  const [busy,setBusy]=useState(false);
  const [takingControl,setTakingControl]=useState(false);
  const toastTimer=useRef(null);
  useEffect(()=>{let disposed=false;transport.getUiPreferences().then(p=>{if(typeof p.thermal_colors!=='boolean')throw Error('Invalid display preference');if(!disposed){setThermalColors(p.thermal_colors);setUiPreferencesReady(true);}}).catch(()=>{if(!disposed)setUiPreferencesError('本机显示偏好读取失败，请重新打开应用。');});return ()=>{disposed=true;};},[transport]);
  useEffect(()=>{let stop,disposed=false;transport.initialize().then(initial=>{if(disposed)return;setState(initial);setSamples(initial.samples);stop=transport.subscribe(initial.seq,frame=>{setState(frame);setError('');setSamples(old=>{if(frame.stream!==initial.stream){initial.stream=frame.stream;return frame.samples;}const last=old.at(-1)?.seq??0;const all=[...old,...frame.samples.filter(x=>x.seq>last)];const cutoff=frame.server_time-305;return all.filter(x=>x.t>=cutoff).slice(-16000);});},e=>setError('连接中断，正在重试。保留最后读数。'));}).catch(e=>setError('本地服务不可用，请启动 Qchip demo 后刷新。'));return ()=>{disposed=true;stop?.();clearTimeout(toastTimer.current);};},[]);
  useEffect(()=>{transport.setView(view,selected);},[transport,view,selected]);
  useEffect(()=>{
    if(!chartOnly||view!=='detail')return;
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    return ()=>{document.body.style.overflow=previous;};
  },[chartOnly,view]);
  const notify=(message,failed=false)=>{setToast({message,failed});clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setToast(null),3600);};
  const send=async(id,type,params)=>{try{const result=await transport.sendCommand(id,type,params);setState(old=>({...old,channels:old.channels.map(c=>c.id===id?{...c,...result.channel}:c)}));notify(`CH${id} ${type==='pid'?'PID':type==='setpoint'?'目标值':type==='output'?'输出状态':'控制模式'}已更新${state.simulation?' · 模拟':''}`);return result;}catch(e){throw e;}};
  const open=id=>{setSelected(id);setView('detail');};
  const saveSettings=async(settings,preferences)=>{
    const globalChanged=Object.entries(settings).some(([key,value])=>value!==state.settings[key]);
    const saved=await transport.commitSettings(settings,preferences.nativeStorage??null,{thermal_colors:preferences.thermalColors});
    const applied=saved.settings;
    setState(await transport.getSnapshot());setThermalColors(saved.preferences.thermal_colors);
    notify(globalChanged&&state.connected&&applied.device_port!==state.device?.port&&!state.simulation?'设置已保存，设备端口在下次连接时生效':'设置已保存');
  };
  const connection=async(connected,port,baud)=>{setBusy(true);try{const next=await transport.setDeviceConnection(connected,port,baud);setState(next);notify(connected?'正在连接设备…':'正在释放通信连接…');return next;}finally{setBusy(false);}};
  const saveName=async value=>{const applied=await transport.saveDeviceName(value);setState(await transport.getSnapshot());notify('本机名称已保存');return applied.device_name;};
  const channel=state?.channels.find(c=>c.id===selected);
  const heldHistory=useRef(null);
  if(state)heldHistory.current=holdPausedHistory(heldHistory.current,state,samples);
  const displaySamples=useMemo(()=>{
    const points=new Map(samples.map(s=>[s.seq,s]));
    for(const list of Object.values(heldHistory.current?.channels??{}))for(const point of list)points.set(point.seq,point);
    return [...points.values()].sort((a,b)=>a.seq-b.seq);
  },[samples,state?.stream,state?.sampling_policy?.revision]);
  const acquisitionState=id=>{const status=acquisition(state,id);return error?{...status,paused:false,stale:true}:status;};
  const {policy,stale,selectedPaused}=samplingView(state,view,selected,Boolean(error));
  const connectionPending=['connecting','disconnecting'].includes(state?.connection_state);
  const takeQchip=async()=>{if(takingControl)return;setTakingControl(true);try{await transport.takeQchip();setState(await transport.getSnapshot());notify('Qchip 已接管');}catch(e){notify(e.message,true);}finally{setTakingControl(false);}};
  const controllerName=error?'暂不可用':state?.control.mode==='Remote'?(state.control.holder?.display_name||'暂不可用'):'Qchip';
  const localHasControl=state?.control.mode==='Qchip'&&!error;
  const hasAuthority=Boolean(state?.cloudsim||state?.hardware_control);
  return <div className={`qchip-tec app-shell ${chartOnly&&view==='detail'?'chart-only':''}`}>
    <header className="app-header"><div className="brand"><Mark/><span>Qchip<span className="brand-divider"/><strong>TEC</strong></span></div><div className="header-end">{state?.settings.read_only&&<span className="readonly-badge"><LockKeyhole size={14}/>全部通道只读</span>}{state?.simulation&&<span className="demo-badge"><FlaskConical size={14}/>模拟设备</span>}<span className="host-name"><Cpu size={14}/>实验室本机</span></div></header>
    <main><div className="page-heading"><div><h1>温度，尽在掌握。</h1></div><img className="heading-mascot" src={mascot} width="100" height="76" alt="TEC mascot"/><div className="device-actions"><span className={`connection-status ${stale?'offline':''}`}><i/>{!state?'读取中':connectionPending?'连接处理中':!state.connected?'未连接':stale?'数据未更新':'采集正常'}</span><CloudBridgeStatus endpoint={cloudEndpoint}/></div></div>
      <div className="workspace-bar"><div className="workspace-start"><span className="workspace-device">{state?.device_name||'Qchip TEC'}</span><nav aria-label="视图"><button className={view==='overview'?'active':''} onClick={()=>setView('overview')}><LayoutGrid size={16}/>总览</button><button className={view==='detail'?'active':''} onClick={()=>setView('detail')}><Activity size={16}/>详情</button><button className={view==='logs'?'active':''} onClick={()=>setView('logs')}><FileText size={16}/>日志</button></nav></div><div className="workspace-end"><div className={`authority ${hasAuthority?'authority-cloudsim':''}`} role="group" aria-label="控制权"><span className="authority-label">控制权</span><span className="authority-local" title={controllerName} aria-live="polite"><i/><span>{hasAuthority?controllerName:'Qchip'}</span></span>{hasAuthority?<button className={`button secondary authority-action ${localHasControl?'is-connected':''}`} disabled={localHasControl||takingControl||Boolean(error)||state.control.mode!=='Remote'} aria-label={localHasControl?'Qchip 已连接':takingControl?'正在接管':'让我来，接管为 Qchip'} onClick={takeQchip}>{localHasControl?'已连接':takingControl?'接管中…':'让我来'}</button>:<span className="remote-pending" title="远程接入将在本地版验收后启用">Remote 未接入</span>}</div><button className="button secondary global-settings-button" disabled={!state||!uiPreferencesReady} onClick={()=>setSettingsOpen(true)} aria-label="全局设置" title="全局设置"><Settings size={19}/></button></div></div>
      {state?.simulation&&<div className="demo-notice"><FlaskConical size={15}/><span>交互演示 · 模拟数据</span></div>}
      {state?.logging?.error&&<div className="error-banner" role="alert">{state.logging.error}</div>}
      {state?.connection_error && <div className="error-banner" role="alert">{state.connection_error}</div>}
      {uiPreferencesError&&<div className="error-banner" role="alert">{uiPreferencesError}<button className="text-button" onClick={()=>location.reload()}><RefreshCw size={14}/>重新读取</button></div>}
      {error && <div className="error-banner" role="alert">{error}<button className="text-button" onClick={()=>location.reload()}><RefreshCw size={14}/>重新连接</button></div>}
      {!state?<div className="loading-state"><Activity size={28}/><h2>正在连接本地工作台</h2><p>读取设备状态…</p></div>:<>
        {view==='logs'?<NativeLogPage key={state.stream} transport={logTransport} serverTime={state.server_time} channelIds={state.channel_ids??[]}/>:!state.channels.length?<div className="loading-state"><PlugZap size={28}/><h2>请连接设备</h2><p>在“设置 → 设备”中选择端口并连接。</p></div>:view==='overview'?<div className="channel-grid">{state.channels.map(c=><ChannelCard key={c.id} channel={c} samples={displaySamples} connected={!stale} acquisitionState={acquisitionState(c.id)} overclockChannel={policy?.channel} send={send} open={open} busy={busy} thermalColors={thermalColors}/>)}</div>:<>
          <ChannelTabs channels={state.channels} selected={selected} onSelect={setSelected}/>
          <div className="detail-layout"><NativeSharedCurve key={`detail-${selected}`} simulation={state.simulation} samples={displaySamples} selected={selected} connected={!stale} acquisitionPaused={selectedPaused} overclockChannel={policy?.channel} serverTime={state.server_time} settings={state.settings} expanded={chartOnly} setExpanded={setChartOnly}/><div className="detail-sidebar"><ChannelReadings channel={channel} acquisitionState={acquisitionState(selected)} connected={!stale} thermalColors={thermalColors}/><ChannelControls key={selected} simulation={state.simulation} channel={channel} connected={!stale} send={send}/></div></div>
        </>}
        <SamplingFooter state={error?{...state,connected:false}:state} view={view} selected={selected} stale={stale} end={<span className="footer-end">v{NATIVE_VERSION} YCtech</span>}/>
      </>}
    </main>{settingsOpen && state && <SettingsDialog logTransport={logTransport} state={state} saveName={saveName} connection={connection} settings={state.settings} thermalColors={thermalColors} channelIds={state.channels.map(c=>c.id)} logging={state.logging} save={saveSettings} close={()=>setSettingsOpen(false)}/>}
    {toast&&<div className={`toast ${toast.failed?'failed':''}`} role="status">{toast.failed?<X size={16}/>:<Check size={16}/>}<span>{toast.message}</span><button aria-label="关闭提示" onClick={()=>setToast(null)}><X size={14}/></button></div>}
  </div>;
}

