import React,{useEffect,useRef,useState} from 'react';
import {Download,RefreshCw} from 'lucide-react';

export function UpdatesPanel({transport}){
  const [state,setState]=useState(null),[error,setError]=useState('');
  const [confirmOpen,setConfirmOpen]=useState(false);
  const confirmation=useRef(null),cancelButton=useRef(null),installButton=useRef(null);
  useEffect(()=>{
    if(!confirmOpen)return;
    const dialog=confirmation.current;
    dialog.showModal();cancelButton.current?.focus();
    return()=>{if(dialog.open)dialog.close();};
  },[confirmOpen]);
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
    try{setError('');setState(await transport.request('/api/updates/'+kind,{}));}
    catch(e){setError(e.message);}
  };
  const closeConfirmation=()=>{
    setConfirmOpen(false);
    installButton.current?.focus();
  };
  const working=['checking','downloading','installing'].includes(state?.state);
  return <section className="native-updates" aria-label="软件更新">
    <p>Qchip TEC <strong>{state?.current??__QCHIP_DESKTOP_VERSION__}</strong></p>
    <button type="button" className="button secondary" disabled={!state||working} onClick={()=>action('check')}><RefreshCw size={16}/>检查更新</button>
    {state?.state==='available'&&<button type="button" className="button primary" onClick={()=>action('download')}><Download size={16}/>下载更新</button>}
    {state?.state==='ready'&&<button ref={installButton} type="button" className="button primary" disabled={!state.can_install} onClick={()=>setConfirmOpen(true)}>更新并重启</button>}
    <p role="status">{state?.message}</p>
    {error&&<p role="alert" className="field-error">{error}</p>}
    <dialog ref={confirmation} className="update-confirm-dialog" aria-labelledby="update-confirm-title" aria-describedby="update-confirm-message"
      onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeConfirmation();}}}
      onCancel={event=>{event.preventDefault();event.stopPropagation();closeConfirmation();}}
      onClose={event=>{event.stopPropagation();closeConfirmation();}}>
      <h3 id="update-confirm-title"><RefreshCw size={20}/>确认更新</h3>
      <p id="update-confirm-message">更新的时候会暂时中断采集，勿谓言之不预！</p>
      <div className="update-confirm-actions">
        <button ref={cancelButton} type="button" className="button secondary" onClick={closeConfirmation}>取消</button>
        <button type="button" className="button primary" onClick={()=>{closeConfirmation();action('install');}}>确认</button>
      </div>
    </dialog>
  </section>;
}
