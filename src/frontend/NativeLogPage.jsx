import React,{useEffect,useState} from 'react';
import {LogPage} from '../vendor/tec-ui/logs.jsx';
import {NativeLogSettings} from './NativeLogSettings.jsx';

export function NativeLogPage({transport,serverTime,channelIds}){
  const [initial,setInitial]=useState(null),[error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    transport.state(controller.signal).then(setInitial).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();
  },[transport]);
  if(error)return <section className="error-banner" role="alert">{error}</section>;
  if(!initial)return <section className="loading-state" role="status">正在读取本机日志…</section>;
  return <LogPage transport={transport} serverMs={Number.isFinite(serverTime)?Math.trunc(serverTime*1000):initial.server_ms}
    channelIds={channelIds.length?channelIds:initial.channel_ids} storageSlot={<NativeLogSettings transport={transport} compact/>}/>;
}
