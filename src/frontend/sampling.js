// Native view-model compatibility only. Acquisition rules live in the public package.
import {acquisition, samplingStale, lastSampleTime, samplingLabel, sampleBreak} from '../vendor/tec-ui/sampling.js';
export * from '../vendor/tec-ui/sampling.js';
export const sameSegment=(previous,current)=>!sampleBreak(previous,current);
export function samplingView(frame,view,selected,transportError=false){
  const policy=frame?.sampling_policy;
  const overclock=policy?.mode==='single_channel_overclock';
  const stale=Boolean(transportError||samplingStale(frame,view,selected));
  const isPaused=id=>!transportError&&!stale&&acquisition(frame,id).paused;
  const selectedPaused=isPaused(selected);
  return {policy,overclock,stale,isPaused,selectedPaused,
    lastSample:lastSampleTime(frame,view,selected),
    caption:frame?samplingLabel(frame):'采样模式未知',
    lastSampleLabel:stale||view==='detail'&&selectedPaused?'最后采样':'更新于',
    pauseCaption:overclock&&!stale?`通道${policy.channel}超频中`:''};
}
