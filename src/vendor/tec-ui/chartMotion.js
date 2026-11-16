import {useEffect, useRef, useState} from 'react';

// Only the plot rerenders on animation frames; acquisition, statistics and CSV
// remain driven by the original samples. No rendering clock reaches the server.
export function useChartFrame(container, enabled) {
  const [now,setNow]=useState(()=>performance.now());
  const [visible,setVisible]=useState(()=>!document.hidden);
  const [onScreen,setOnScreen]=useState(true);
  useEffect(()=>{
    const visibility=()=>setVisible(!document.hidden);
    document.addEventListener('visibilitychange',visibility);
    const observer=new IntersectionObserver(([entry])=>setOnScreen(entry.isIntersecting));
    if(container.current)observer.observe(container.current);
    return()=>{document.removeEventListener('visibilitychange',visibility);observer.disconnect();};
  },[]);
  // Explicit per-app opt-in overrides the OS motion preference for this plot
  // only. The user can disable it; other UI motion still follows system CSS.
  const animate=enabled&&visible&&onScreen;
  useEffect(()=>{
    if(!animate)return;
    let request,previous=0;
    const frame=stamp=>{
      // Cap at the 60 Hz range even on high-refresh displays.
      if(stamp-previous>=15){previous=stamp;setNow(stamp);}
      request=requestAnimationFrame(frame);
    };
    request=requestAnimationFrame(frame);
    return()=>cancelAnimationFrame(request);
  },[animate]);
  return {now:animate?now:performance.now(),animate};
}

export const easeOut = amount => 1-(1-Math.min(1,Math.max(0,amount)))**3;

export function useAnimatedDomain(target, now, animate, identity) {
  const tween=useRef(null);
  const blend=t=>{
    const p=easeOut((now-t.start)/t.duration);
    return {min:t.from.min+(t.to.min-t.from.min)*p,max:t.from.max+(t.to.max-t.from.max)*p};
  };
  if(!tween.current||tween.current.identity!==identity||!animate){
    tween.current={identity,from:target,to:target,start:now,duration:1};
  }else if(tween.current.to.min!==target.min||tween.current.to.max!==target.max){
    const current=blend(tween.current);
    const expanding=target.min<current.min||target.max>current.max;
    tween.current={identity,from:current,to:target,start:now,duration:expanding?180:320};
  }
  return blend(tween.current);
}
