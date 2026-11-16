import {useEffect,useState} from 'react';

// This browser-only display preference never reaches transports or source settings.
export const THERMAL_COLORS_KEY='qchip.tec.thermal-colors.v1';
export function useThermalColors() {
  const [enabled,setEnabled]=useState(true);
  useEffect(()=>{
    try{setEnabled(localStorage.getItem(THERMAL_COLORS_KEY)!=='false');}catch{/* Storage may be unavailable. */}
    const sync=e=>{if(e.key===THERMAL_COLORS_KEY||e.key===null)setEnabled(e.newValue!=='false');};
    window.addEventListener('storage',sync);
    return ()=>window.removeEventListener('storage',sync);
  },[]);
  const change=value=>{
    const next=Boolean(value);setEnabled(next);
    try{localStorage.setItem(THERMAL_COLORS_KEY,String(next));}catch{/* Keep the current window usable. */}
  };
  return [enabled,change];
}
