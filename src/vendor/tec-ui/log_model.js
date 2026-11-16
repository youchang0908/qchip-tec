export const METRICS={temperature_c:{label:'温度',unit:'°C'},resistance_kohm:{label:'电阻',unit:'kΩ'}};
export const CHANNELS=[1,2,3,4];
export const TABLE_COLUMNS=[
  {key:'temperature_c',label:'温度',unit:'°C',digits:4,width:88},
  {key:'resistance_kohm',label:'电阻',unit:'kΩ',digits:4,width:88},
  {key:'current_a',label:'电流',unit:'A',digits:6,width:96},
  {key:'voltage_v',label:'电压',unit:'V',digits:4,width:88},
  {key:'power_w',label:'电功率',unit:'W',digits:6,width:104,note:'|I×V|，电功率，不是制冷功率'},
  {key:'tset_c',label:'温度设定值',unit:'°C',digits:4,width:116,note:'缓存的温度设定值'}
];
export const DEFAULT_TABLE_COLUMNS=['temperature_c','resistance_kohm'];
export const TABLE_COLUMN_PREFERENCE='qchip.tec.log-table-columns.v1';
export function normalizeTableColumns(value){
  if(!Array.isArray(value))return [...DEFAULT_TABLE_COLUMNS];
  const selected=TABLE_COLUMNS.filter(column=>value.includes(column.key)).map(column=>column.key);
  return value.length&&!selected.length?[...DEFAULT_TABLE_COLUMNS]:selected;
}
export function toggleTableColumn(columns,key){
  if(!TABLE_COLUMNS.some(column=>column.key===key))return normalizeTableColumns(columns);
  return TABLE_COLUMNS.filter(column=>column.key===key?!columns.includes(key):columns.includes(column.key)).map(column=>column.key);
}
export function tableCell(row,column){return fmt(row[column.key],column.digits);}
export function makeRange(preset,anchor,start,end){
  if(preset==='custom'){
    const from=Date.parse(start+'+08:00'),to=Date.parse(end+'+08:00');
    const valid=text=>/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(text);
    if(!valid(start)||!valid(end)||!Number.isFinite(from)||!Number.isFinite(to)||isoInput(from).slice(0,start.length)!==start||isoInput(to).slice(0,end.length)!==end||from>=to)throw Error('请填写完整的 UTC+8 起止时间，结束须晚于开始。');
    if(to-from>7*86400000)throw Error('一次最多查看 7 天，请缩小时间范围。');
    return {start:from,end:to};
  }
  return {start:anchor-Number(preset)*60000,end:anchor};
}
// Offset applies only to formatting. Stored/query timestamps always remain UTC epoch ms.
export function isoInput(ms){return new Date(ms+8*60*60*1000).toISOString().slice(0,-1);}
export function axisClock(ms){return isoInput(ms).slice(11,19);}
export function fmt(value,digits=4){return Number.isFinite(value)?value.toFixed(digits):'—';}
export function stamp(ms){return isoInput(ms).replace('T',' ');}
export function nearestPoint(series,ms){
  let nearest=null;
  for(const [channel,paths] of Object.entries(series))for(const points of paths)for(const point of points){
    if(!nearest||Math.abs(point.ts_ms-ms)<Math.abs(nearest.point.ts_ms-ms))nearest={channel:Number(channel),point};
  }
  return nearest;
}
