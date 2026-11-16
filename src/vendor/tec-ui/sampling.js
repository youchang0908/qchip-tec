// Source-owned acquisition state. A browser never infers PAUSED from zero Hz.
function channelStale(frame, channel) {
  const c=frame?.channels.find(c=>c.id===channel),hz=frame?.channel_sample_hz?.[channel];
  const ageLimit=frame?.sampling_policy&&hz>0?Math.max(2,Math.min(30,3/hz)):2;
  return !frame?.connected||!c?.sampled_at||frame.server_time-c.sampled_at>ageLimit;
}
export function acquisition(frame, channel) {
  const c=frame?.channels.find(c=>c.id===channel),policy=frame?.sampling_policy;
  const paused=Boolean(frame?.connected&&policy?.channels[channel]?.state==='paused'&&
    !channelStale(frame,policy.channel));
  return {paused,stale:channelStale(frame,channel),last:c?.sampled_at??null};
}

export function lastSampleTime(frame, view, selected) {
  if(!frame)return null;
  if(view==='detail')return frame.channels.find(c=>c.id===selected)?.sampled_at??null;
  const policy=frame.sampling_policy;
  if(policy?.mode==='single_channel_overclock')return frame.channels.find(c=>c.id===policy.channel)?.sampled_at??null;
  const times=frame.channels.map(c=>c.sampled_at);
  return times.length>0&&times.every(t=>Number.isFinite(t)&&t>0)?Math.min(...times):null;
}
export function samplingStale(frame, view, selected) {
  if(!frame?.connected)return true;
  if(view==='detail')return acquisition(frame,selected).stale&&!acquisition(frame,selected).paused;
  const ids=frame.sampling_policy?.mode==='single_channel_overclock'?[frame.sampling_policy.channel]:frame.channels.map(c=>c.id);
  return ids.some(ch=>acquisition(frame,ch).stale);
}
export function samplingLabel(frame) {
  if(!frame.connected)return '等待设备连接';
  const p=frame.sampling_policy;
  if(!p)return '采样模式未知（旧版）';
  if(p.mode==='normal')return '连续采样';
  if(p.mode==='four_channel')return '四通道 · 2 Hz';
  const measured=p.measured_hz;
  return `CH${p.channel}超频 · ${p.target.kind==='max'?'MAX':`目标${p.target.hz} Hz`} · 实测${Number.isFinite(measured)?`${measured.toFixed(1)} Hz`:'待测'}`;
}
// revision travels with each historical sample, so a brief pause remains a gap
// even when the browser missed both policy changes or reconnects afterwards.
export function sampleBreak(previous, current) {
  return !previous||current.t-previous.t>1.6||current.t<=previous.t||
    (previous.sampling_revision!==current.sampling_revision&&
      (previous.sampling_revision!=null||current.sampling_revision!=null));
}

// Keep already-seen paused curves independently of the active channel's ring
// buffer. Only genuine acquired points are retained; unseen history stays empty.
export function holdPausedHistory(previous, frame, samples) {
  const next={stream:frame?.stream,channels:{}};
  const policy=frame?.sampling_policy;
  if(policy?.mode!=='single_channel_overclock')return next;
  for(const id of frame.channels.map(c=>c.id)){
    if(id===policy.channel)continue;
    const saved=previous?.stream===frame.stream?previous.channels[id]??[]:[];
    const points=new Map(saved.map(s=>[s.seq,s]));
    for(const s of samples)if(s.sampled_channels.includes(id))points.set(s.seq,s);
    const all=[...points.values()].sort((a,b)=>a.seq-b.seq),end=all.at(-1)?.t??0;
    next.channels[id]=all.filter(s=>s.t>=end-120).slice(-8000);
  }
  return next;
}
