import type {ReactNode} from 'react';
import type {Channel,Sample,SamplingPolicy} from './display';
export interface AcquisitionState {paused:boolean;stale:boolean;last:number|null}
export interface DisplaySettings {display_seconds:number;smooth_plot:boolean}
export interface RecordingMark {active:boolean;started_at:number|null}
export type Send=(channel:number,type:string,params:object)=>Promise<unknown>;
export interface CardProps {channel:Channel;samples:Sample[];connected:boolean;open:(channel:number)=>void;send:Send;busy?:boolean;acquisitionState:AcquisitionState;overclockChannel?:number|null;thermalColors?:boolean}
export interface CurveProps {samples:Sample[];selected:number;connected:boolean;serverTime:number;recording?:RecordingMark;settings:DisplaySettings;simulation?:boolean;acquisitionPaused?:boolean;overclockChannel?:number|null;recordingBadge?:ReactNode;recordingContent?:ReactNode;expandAction?:ReactNode}
export declare const COLORS:string[];
export declare function ToggleSwitch(props:{checked:boolean;onChange:(checked:boolean)=>void;disabled?:boolean;label:string;id?:string}):ReactNode;
export declare function fmt(value:number|null,places?:number):string;
export declare function clock(value:number|null):string;
export declare function ChannelCard(props:CardProps):ReactNode;
export declare function CurvePanel(props:CurveProps):ReactNode;
export declare function ChannelControls(props:{channel:Channel;connected:boolean;send:Send;simulation?:boolean}):ReactNode;
export declare function ChannelReadings(props:{channel:Channel;acquisitionState:AcquisitionState;connected:boolean;thermalColors?:boolean}):ReactNode;
export declare function ChannelTabs(props:{channels:Channel[];selected:number;onSelect:(channel:number)=>void}):ReactNode;
export declare function SamplingFooter(props:{state:{connected:boolean;channels:Channel[];logging?:{active:boolean};sampling_policy?:SamplingPolicy|null;channel_sample_hz:Record<number,number>;server_time:number;rate_status?:Record<number,{limited:boolean;effective_hz:number}>};view:string;selected:number;stale:boolean;end?:ReactNode}):ReactNode;
export declare function Status(props:{on:boolean|null;current?:number|null;disconnected?:boolean;last?:boolean}):ReactNode;
export declare function Metric(props:{label:string;value:string;suffix?:string}):ReactNode;
export declare function PowerMetric(props:{current:number|null;voltage:number|null;detail?:boolean}):ReactNode;
export declare function Spark(props:{samples:Sample[];channel:number;acquisitionPaused?:boolean;overclockChannel?:number|null}):ReactNode;
