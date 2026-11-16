import type {Mode, Channel, Sample, SamplingPolicy} from '../vendor/tec-ui/display';
export type {Mode, Channel, Sample, SamplingPolicy} from '../vendor/tec-ui/display';
// Native-only transport, settings and recording model.
export interface Recording {
  channel: number;
  active: boolean;
  rows: number;
  started_at: number | null;
  stopped_at: number | null;
  elapsed_seconds: number;
  filename: string | null;
  error: string | null;
  sample_hz: number | null; // effective recording cadence; independent of page changes, guarded against overload
  requested_hz: number | null; // requested at start; sample_hz can decrease via rate guard
}
export interface Settings {
  device_port: string; // COM8 by default; used only on the next explicit connection
  read_only: boolean; // applies to every channel; changes app policy, never TEC settings
  detail_hz: number; // legacy preference only; page selection never changes acquisition
  overclock_enabled: boolean; // runtime only; every service restart forces false
  overclock_channel: number; // fixed 1..4, independent of viewed channel
  overclock_target: number | 'max'; // fixed integer2..10 or adaptive finite-cap MAX
  display_seconds: number; // integer 10–300 s, viewing window only; does not truncate recordings
  smooth_plot: boolean; // explicit per-app motion preference; never changes acquisition cadence
  recording_dir: string; // local absolute path, local UI only
  filename_prefix: string;
}
export type ControlState = {mode:'Qchip'; remote_available:false} | {
  mode:'Qchip'|'Remote'; remote_available:true;
  boot_id:string; bridge_session_id:string|null;
  connection_epoch:number; control_epoch:number; lease_id:string|null;
  remaining_ms:number; owned:boolean; global_read_only:boolean; window_read_only:boolean;
  holder?:{display_name:string;window_id:string};
};
export interface Frame {
  protocol: 'qchip.tec.demo.v1';
  stream: string;
  seq: number;
  simulation: boolean;
  connected: boolean;
  connection_state: 'connected' | 'disconnected' | 'connecting' | 'disconnecting' | 'error';
  connection_error: string | null;
  read_only: boolean;
  device: {model:string;port?:string;resource?:string;backend?:string;read_only:boolean} | null;
  overview_hz: 2;
  channel_sample_hz: Record<number, number>;
  sampling_policy?: SamplingPolicy;
  rate_status: Record<number, {requested_hz:number;effective_hz:number;measured_hz:number|null;limited:boolean}>;
  last_sample_at: number | null;
  server_time: number;
  channels: Channel[];
  samples: Sample[];
  recordings: Record<number, Recording>;
  settings: Settings;
  control: ControlState;
  cloudsim?: {profile:string;version:string;device_id:string;boot_id:string;
    bridge:{state:string;connected:boolean;code?:string|null;metrics?:Record<string,unknown>}};
}
export type Command =
  | {type:'setpoint'; params:{mode:Mode; value:number}}
  | {type:'pid'; params:{values:[number,number,number]}}
  | {type:'output'; params:{enabled:boolean}}
  | {type:'mode'; params:{mode:Mode}};
export interface TecTransport {
  initialize(): Promise<Frame>;
  getCapabilities(): Promise<unknown>;
  getSnapshot(): Promise<Frame>;
  setView(view:'overview'|'detail',channel:number): void;
  subscribe(after:number, onFrame:(frame:Frame)=>void, onError:(error:Error)=>void): ()=>void;
  sendCommand(channel:number, type:Command['type'], params:Command['params']): Promise<{id:string;status:'confirmed';channel:Channel}>;
  setConnection(connected:boolean): Promise<Frame>;
  setRecording(channel:number, active:boolean): Promise<Recording>;
  updateSettings(settings:Settings): Promise<Settings>;
  takeQchip?(): Promise<ControlState>; // loopback CloudSim capability only
}
// Cloud wire is separately versioned qchip.tec.cloud.sim.v1, not this local Frame.
// Local recording and connection-management capabilities must not be exposed remotely.
