import type {ReactNode} from 'react';
export type LogMetric='temperature_c'|'resistance_kohm';
export interface LogRow {session_id:string;seq:number;channel:number;ts_ms:number;utc:string;temperature_c:number|null;resistance_kohm:number|null;current_a:number|null;voltage_v:number|null;power_w:number|null;tset_c:number|null;output:0|1|null;mode:string|null;metadata_utc:string|null;quality:'ok'|'missing'|'paused';segment:number;source:string}
export interface LogRange {start:number;end:number}
export interface LogSeries {series:Record<number,LogRow[][]>;raw_count:number;start_ms:number;end_ms:number;metric:LogMetric;method?:string}
export interface LogTable {rows:LogRow[];total:number;page_size:number;start_ms:number;end_ms:number}
export interface LogTransport {
 series(input:LogRange&{channels:number[];metric:LogMetric;signal:AbortSignal}):Promise<LogSeries>;
 table(input:LogRange&{channel:number;page:number;signal:AbortSignal}):Promise<LogTable>;
 exportUrl?(input:LogRange&{channel:number}):string;
 /** Alternative for token-authenticated native download; host owns Blob cleanup. */
 exportCsv?(input:LogRange&{channel:number}):Promise<void>;
}
/** Stable host transport, integer UTC serverMs. Remount on physical-device switch. */
export declare function LogPage(props:{transport:LogTransport;serverMs:number;channelIds?:number[];storageSlot?:ReactNode;retentionHours?:number|null;refreshKey?:number}):ReactNode;
export declare function LogSeriesChart(props:{data?:LogSeries|null;range:LogRange;metric:LogMetric;loading?:boolean;error?:string}):ReactNode;
