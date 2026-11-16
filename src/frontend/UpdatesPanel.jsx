import React,{useEffect,useState} from 'react';
import {Download,RefreshCw} from 'lucide-react';

export function UpdatesPanel({transport}){
  const [state,setState]=useState(null),[error,setError]=useState('');
  useEffect(()=>{
    let gone=false,timer;
    const poll=async()=>{
      try{const next=await transport.request('/api/updates/state');if(!gone){setState(next);setError('');}}
      catch(e){if(!gone)setError(e.message);}
      if(!gone)timer=setTimeout(poll,1500);
    };
    poll();return()=>{gone=true;clearTimeout(timer);};
  },[transport]);
  const action=async kind=>{
    if(kind==='install'&&!window.confirm('更新并重启？本次采集与日志将正常结束，重启后请重新连接设备。'))return;
    try{setError('');setState(await transport.request('/api/updates/'+kind,{}));}
    catch(e){setError(e.message);}
  };
  const working=['checking','downloading','installing'].includes(state?.state);
  return <section className="native-updates" aria-label="软件更新">
    <p>Qchip TEC <strong>{state?.current??__QCHIP_DESKTOP_VERSION__}</strong></p>
    <button type="button" className="button secondary" disabled={!state||working} onClick={()=>action('check')}><RefreshCw size={16}/>检查更新</button>
    {state?.state==='available'&&<button type="button" className="button primary" onClick={()=>action('download')}><Download size={16}/>下载更新</button>}
    {state?.state==='ready'&&<button type="button" className="button primary" disabled={!state.can_install} onClick={()=>action('install')}>更新并重启</button>}
    <p role="status">{state?.message}</p>
    {error&&<p role="alert" className="field-error">{error}</p>}
  </section>;
}
