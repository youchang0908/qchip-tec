import React, {useEffect, useRef, useState} from 'react';
import {Globe, Link2} from 'lucide-react';

export function CloudLinkSetting({endpoint,onBusyChange}) {
  const localPage=['127.0.0.1','localhost'].includes(window.location.hostname)&&window.location.port==='8765';
  if(endpoint!==undefined)return <CloudLinkControl endpoint={endpoint} onBusyChange={onBusyChange}/>;
  if(!localPage)return null;
  return <CloudLinkControl endpoint={`http://${window.location.hostname}:28766`} onBusyChange={onBusyChange}/>;
}

function CloudLinkControl({endpoint,onBusyChange}) {
  const [state,setState]=useState(null);
  const [token,setToken]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [statusError,setStatusError]=useState('');
  const [pairing,setPairing]=useState(null);
  const [code,setCode]=useState('');
  const [pairedMessage,setPairedMessage]=useState('');
  const busyRef=useRef(false);
  const markBusy=value=>{busyRef.current=value;setBusy(value);onBusyChange?.(value);};
  const errors={PAIRING_REQUIRES_DISCONNECT:'请先断开网站连接，再更换配对。',PAIRING_BUSY:'配对正在进行，请稍候。',PAIRING_INVALID_CODE:'请输入网站生成的 48 位配对码。',PAIRING_STORAGE_UNAVAILABLE:'本机无法保存凭据，尚未兑换配对码。',PAIRING_REDEEM_FAILED:'配对码无效或已过期，请在网站重新生成。',PAIRING_RESULT_UNCERTAIN:'未能确认兑换结果。请在网站重新生成配对码后再操作。',PAIRING_SAVE_FAILED:'兑换后未能保存凭据。请检查本机保存权限，再生成新配对码。'};
  const request=async(path,body,sessionToken=token)=>{
    const res=await fetch(endpoint+path,{method:body?'POST':'GET',cache:'no-store',
      headers:body?{'Content-Type':'application/json','X-Qchip-Cloud-Token':sessionToken}:{},
      body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(path==='/api/cloud/pairing'&&body?15000:4000)});
    const result=await res.json();if(!res.ok)throw new Error(errors[result.error]||result.error||'网站连接操作失败');return result;
  };
  useEffect(()=>{
    let active=true,pending=false;
    const refresh=async()=>{if(pending||busyRef.current)return;pending=true;try{
      const session=await request('/api/cloud/session');
      const current=await request('/api/cloud/state');
      const pairState=await request('/api/cloud/pairing').catch(()=>({supported:false}));
      if(active){setToken(session.token);setState(current);setPairing(pairState);setStatusError('');}
    }catch{if(active){setState(null);setToken('');setStatusError('网站连接组件不可达');}}finally{pending=false;}};
    refresh();
    const timer=setInterval(refresh,1500);
    return ()=>{active=false;clearInterval(timer);};
  },[endpoint]);
  const toggle=async()=>{
    markBusy(true);setError('');
    try{setState(await request('/api/cloud/connection',{enabled:!state.enabled}));}
    catch(e){setError(e.message);}
    finally{markBusy(false);}
  };
  const pair=async()=>{
    if(busyRef.current)return;
    if(!state||state.enabled||!token||!pairing?.supported){setError(errors.PAIRING_REQUIRES_DISCONNECT);return;}
    const normalized=code.trim().toLowerCase();
    if(!/^(?:[a-f0-9]{48}|tec-[a-f0-9]{24}\.[a-f0-9]{48})$/.test(normalized)){setError(errors.PAIRING_INVALID_CODE);return;}
    markBusy(true);setError('');setPairedMessage('');
    let committed=false;
    try{
      const request_id=crypto.randomUUID().replaceAll('-','');
      const result=await request('/api/cloud/pairing',{code:normalized,request_id});
      committed=true;setCode('');setPairing({...pairing,...result,supported:true});
      setPairedMessage('配对已保存');
      setState(await request('/api/cloud/connection',{enabled:true}));
      setPairedMessage('配对已保存，正在连接');
    }catch(e){
      setCode('');
      setError(committed?'配对已保存，但未能连接。可点击“连接”重试，无需重新兑换。':e.name==='TimeoutError'?'未能确认配对结果，请先查看连接状态；不要重复提交旧配对码。':e.message);
    }finally{markBusy(false);}
  };
  const label=!state?(statusError?'状态未知':'读取中'):state.enabled?(state.connected?'已连接':'连接中'):'已断开';
  const historyLabel=({dormant:'未同步',waiting:'等待连接',waiting_for_log:'暂无可同步日志',uploading:'同步中',catching_up:'正在补传历史',caught_up:'已同步',paused:'已暂停'})[state?.history?.state];
  return <section className="settings-field cloud-link-setting" aria-label="云桥接">
    <div className="cloud-link-row"><strong><Globe size={16}/>网站连接</strong>
      <div className="cloud-link-actions"><span role="status">{label}</span><button type="button"
        className={`button ${state?.enabled?'secondary':'primary'}`} disabled={busy||!state||!token}
        onClick={toggle}>{busy?'处理中…':state?.enabled?'断开':'连接'}</button></div>
    </div>
    {state?.history&&<p className={state.history.error?'field-error':'settings-inline-status'} role="status">历史同步 · {historyLabel||'等待连接'}{state.history.error?` · ${state.history.error}`:''}</p>}
    {pairing?.supported&&<div className="bridge-pairing">
      <label htmlFor="bridge-pairing-code"><Link2 size={16}/>{pairing.paired?'更换配对':'桥接配对'}</label>
      <input id="bridge-pairing-code" type="password" autoComplete="off" spellCheck="false" maxLength={77} value={code} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();pair();}}} onChange={e=>{setCode(e.target.value);setError('');setPairedMessage('');}} placeholder="输入网站生成的配对码" disabled={busy||!state||state.enabled}/>
      <button type="button" className="button primary" onClick={pair} disabled={busy||!state||state.enabled||!token||!code.trim()}>{busy?'处理中…':'配对并连接'}</button>
      {state?.enabled&&<p className="settings-inline-status">更换配对前，请先断开网站连接。</p>}
      {pairedMessage&&<p className="settings-inline-status" role="status">{state?.connected?'配对已保存':pairedMessage}</p>}
    </div>}
    {pairing?.supported===false&&<p className="settings-inline-status">当前桥接版本不支持在此配对。</p>}
    {(statusError||error||state?.error)&&<p className="field-error" role="alert">{statusError||error||state.error}</p>}
  </section>;
}
