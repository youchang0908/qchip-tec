import React, {useEffect, useState} from 'react';

export function cloudBridgeLabel(state, unavailable=false) {
  if(unavailable)return {text:'云桥接：不可用',tone:'offline'};
  if(!state)return {text:'云桥接：读取中',tone:'pending'};
  if(state.connection_state==='disconnected')return {text:'云桥接：已断开',tone:'offline'};
  if(state.error||state.connection_state==='blocked')return {text:'云桥接：异常',tone:'offline'};
  if(state.connected&&state.enabled&&(!state.connection_state||state.connection_state==='online'))return {text:'云桥接：正常',tone:'online'};
  if(!state.enabled)return {text:'云桥接：已断开',tone:'offline'};
  return {text:state.connection_state==='reconnecting'?'云桥接：重连中':'云桥接：连接中',tone:'pending'};
}

/** Loopback-only observation. Never opens the gate, pairs or controls the TEC. */
export function CloudBridgeStatus({endpoint}) {
  const [state,setState]=useState(null),[unavailable,setUnavailable]=useState(false);
  const local=typeof window!=='undefined'&&['127.0.0.1','localhost'].includes(window.location.hostname)&&window.location.port==='8765';
  const base=endpoint??(local?`http://${window.location.hostname}:28766`:null);
  useEffect(()=>{
    if(base===null)return;
    setState(null);setUnavailable(false);
    let active=true,timer,controller;
    const refresh=async()=>{
      controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),4000);
      try{
        const response=await fetch(base+'/api/cloud/state',{cache:'no-store',signal:controller.signal});
        if(!response.ok)throw Error('cloud status unavailable');
        const current=await response.json();
        if(typeof current.enabled!=='boolean'||typeof current.connected!=='boolean')throw Error('invalid cloud status');
        if(active){setState(current);setUnavailable(false);}
      }catch{if(active){setState(null);setUnavailable(true);}}
      finally{clearTimeout(timeout);if(active)timer=setTimeout(refresh,1500);}
    };
    refresh();return()=>{active=false;clearTimeout(timer);controller?.abort();};
  },[base]);
  if(base===null)return null;
  const label=cloudBridgeLabel(state,unavailable);
  return <span className={`connection-status cloud-bridge-status ${label.tone}`} role="status" aria-label="云桥接状态" aria-atomic="true"><i/>{label.text}</span>;
}
