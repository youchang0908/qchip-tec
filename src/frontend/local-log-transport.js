// Native-only authenticated storage/history adapter. Never attach to CloudTransport.
export class LocalLogTransport {
  constructor(local){this.local=local;}
  async fetch(path,{body,signal,csv=false}={}){
    const response=await fetch(this.local.base+'/api/local-log/'+path,{
      method:body===undefined?'GET':'POST',cache:'no-store',signal:signal??AbortSignal.timeout(20000),
      headers:{'X-Qchip-Token':this.local.token,...(body===undefined?{}:{'Content-Type':'application/json'})},
      body:body===undefined?undefined:JSON.stringify(body),
    });
    if(!response.ok){const error=await response.json();throw Error(error.error||'日志请求失败');}
    return csv?response.blob():response.json();
  }
  state(signal){return this.fetch('state',{signal});}
  interval(interval_seconds){return this.fetch('interval',{body:{interval_seconds}});}
  enabled(enabled){return this.fetch('enabled',{body:{enabled}});}
  storage(directory,prefix){return this.fetch('storage',{body:{directory,prefix}});}
  series({start,end,channels,metric,signal}){
    return this.fetch('series?'+new URLSearchParams({start,end,channels:channels.join(','),metric}),{signal});
  }
  table({start,end,channel,page,signal}){
    return this.fetch('table?'+new URLSearchParams({start,end,channel,page}),{signal});
  }
  async exportCsv({start,end,channel}){
    const blob=await this.fetch('export.csv?'+new URLSearchParams({start,end,channel}),{csv:true});
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');link.href=url;link.download=`TEC_CH${channel}_${start}_${end}.csv`;link.hidden=true;
    document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
}
