// Shared display model only; host applications own settings, transports and authorization.
export type Mode = 'T' | 'R' | 'ITE';
export interface Channel {
  id: number;
  temperature: number | null; // deg C; null until a real measurement exists
  resistance: number | null; // kOhm; real adapter checks thermistor sensor type
  current: number | null; // A
  voltage: number | null; // V
  output: boolean | null;
  mode: Mode | null;
  setpoints: Record<Mode, number | null>;
  pid: [number | null, number | null, number | null];
  sampled_at?: number | null;
  metadata_at?: number;
  writable: boolean;
}
export interface SamplingPolicy {
  version: 1 | 2;
  revision: number;
  mode: 'four_channel' | 'normal' | 'single_channel_overclock';
  channel: number | null;
  target: {kind:'fixed';hz:number} | {kind:'max'} | {kind:'unlimited';min_sleep_ms:number};
  effective_hz: number | null;
  measured_hz: number | null;
  channels: Record<number,{state:'sampling'|'paused'|'disconnected';reason:'single_channel_overclock'|null}>;
}
export interface Sample {
  sampling_revision?: number; // Original revision at acquisition; never assigned on upload.
  seq: number;
  t: number; // UNIX seconds, sampled by service
  sampled_channels: number[]; // only these channels acquired a new reading in this frame
  channels: Array<Pick<Channel, 'id'|'temperature'|'current'|'voltage'|'output'> & {setpoint:number|null}>;
}
