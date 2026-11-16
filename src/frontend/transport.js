// The panel depends only on this adapter, never on serial/VISA or a desktop bridge.
export class LocalTransport {
  constructor(base = '') {
    this.base = base; this.token = ''; this.viewer = crypto.randomUUID();
    this.view = 'overview'; this.channel = 4; this.detailHz = 2; this.effectiveDetailHz = 2;
  }
  setView(view, channel) { this.view = view; this.channel = channel; this.wake?.(); }
  get refreshHz() { return document.hidden ? 1 : 10; } // Cached UI reads only.
  async request(path, payload) {
    const posting = payload !== undefined;
    const headers = posting ? {'Content-Type': 'application/json'} : {};
    if (this.token && path !== '/api/session') headers['X-Qchip-Token'] = this.token;
    const res = await fetch(this.base + path, {
      cache: 'no-store',
      headers,
      method: posting ? 'POST' : 'GET',
      body: posting ? JSON.stringify(payload) : undefined,
      signal: AbortSignal.timeout(4500),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '请求失败，请重试');
    return data;
  }
  async initialize() {
    const session = await this.request('/api/session');
    this.token = session.token;
    return this.getSnapshot();
  }
  getCapabilities() { return this.request('/api/capabilities'); }
  getSnapshot() { return this.request('/api/snapshot'); }
  getUiPreferences() { return this.request('/api/ui/preferences'); }
  setUiPreferences(preferences) { return this.request('/api/ui/preferences', preferences); }
  subscribe(after, onFrame, onError) {
    let stopped = false, cursor = after, timer, pending = false, wakeRequested = false;
    // A tab reload must not leave its high-rate lease alive alongside the new
    // overview. Keepalive releases demand only; recording and COM stay active.
    const release = () => fetch(this.base + '/api/view/release', {
      method:'POST', keepalive:true,
      headers:{'Content-Type':'application/json','X-Qchip-Token':this.token},
      body:JSON.stringify({viewer:this.viewer}),
    }).catch(()=>{});
    const hide = () => {stopped=true;clearTimeout(timer);release();};
    const show = event => {if(event.persisted){stopped=false;this.viewer=crypto.randomUUID();poll();}};
    const poll = async () => {
      if (stopped || pending) return;
      pending = true;
      const started = performance.now();
      try {
        const frame = await this.request('/api/events?after='+cursor);
        if (!stopped) {
          this.detailHz = Math.min(10,Math.max(2,frame.sampling_policy?.effective_hz??2));
          this.effectiveDetailHz = frame.rate_status?.[this.channel]?.effective_hz || this.detailHz;
          cursor = frame.seq; onFrame(frame);
        }
      } catch (error) { if (!stopped) onError(error); }
      pending = false;
      if (stopped) release(); // also release after any request already in flight
      if (!stopped) timer = setTimeout(poll, wakeRequested ? 0 : Math.max(0, 1000 / this.refreshHz - (performance.now() - started)));
      wakeRequested = false;
    };
    this.wake = () => {clearTimeout(timer);if(pending)wakeRequested=true;else poll();};
    window.addEventListener('pagehide',hide);
    window.addEventListener('pageshow',show);
    poll();
    return () => {hide();this.wake=null;window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show);};
  }
  sendCommand(channel, type, params) {
    return this.request('/api/command', {id: crypto.randomUUID(), channel, type, params, expires_at: Date.now() / 1000 + 5});
  }
  setConnection(connected) { return this.request('/api/connection', {connected}); }
  setDeviceConnection(connected,port,baud) { return this.request('/api/device/connection',connected?{connected,device_port:port,baud_rate:baud}:{connected}); }
  saveDeviceName(device_name) { return this.request('/api/device/settings',{device_name}); }
  takeQchip() { return this.request('/api/control/qchip', {}); }
  setRecording(channel, active) { return this.request('/api/recording', {channel, active}); }
  commitSettings(settings,storage,preferences){
    return this.request('/api/settings/commit',{settings,storage:storage?{directory:storage.directory,prefix:storage.prefix,expected_directory:storage.expected_directory,expected_prefix:storage.expected_prefix}:null,preferences});
  }
  async updateSettings(settings) {
    const applied = await this.request('/api/settings', settings);
    this.detailHz = this.effectiveDetailHz = applied.detail_hz; this.wake?.();
    return applied;
  }
}
