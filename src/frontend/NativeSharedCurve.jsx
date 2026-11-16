import React, {useEffect, useRef} from 'react';
import {Maximize2, Minimize2} from 'lucide-react';
import {CurvePanel} from '../vendor/tec-ui/components.jsx';

// Native capabilities surround the public chart; no copied chart implementation.
export function NativeSharedCurve({samples, selected, connected, serverTime,
  settings, simulation, acquisitionPaused, overclockChannel,
  expanded, setExpanded}) {
  const expandButton=useRef(null);
  useEffect(()=>{
    if(!expanded)return;
    const onKey=event=>{
      if(event.key==='Escape'){
        event.preventDefault();setExpanded(false);expandButton.current?.focus();
      }
    };
    window.addEventListener('keydown',onKey);
    return ()=>window.removeEventListener('keydown',onKey);
  },[expanded,setExpanded]);
  return <CurvePanel samples={samples} selected={selected} connected={connected}
    serverTime={serverTime} settings={settings}
    simulation={simulation} acquisitionPaused={acquisitionPaused}
    overclockChannel={overclockChannel}
    expandAction={<button ref={expandButton} className="icon-button native-expand"
      aria-label={expanded?'退出纯享模式':'全屏曲线'}
      title={expanded?'退出纯享模式（Esc）':'全屏曲线'} aria-pressed={expanded}
      onClick={()=>setExpanded(!expanded)}>
      {expanded?<Minimize2 size={18}/>:<Maximize2 size={18}/>}</button>}/>;
}
