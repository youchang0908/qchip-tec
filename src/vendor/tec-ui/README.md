# Cloud Lab shared TEC UI 1.4.3

Canonical editable source: Mac website/packages/tec-ui. Windows consumes the exact verified release at remote_lab/vendor/tec-ui; vendor is immutable. React and lucide are peer dependencies resolved from the host, never bundled/copied node_modules.

Hosts own orchestration, transport, authorization, SettingsDialog, RecordingBar, connection/recording callbacks, desktop and cloud gate. No COM/VISA/filesystem/network calls are implemented by this package. ChannelControls calls only the injected send callback; host/backend authorization remains mandatory.

Import components.jsx and sampling.js, mount inside .qchip-tec. Shared CSS is scoped to that root. Host-specific shell/theme/desktop layout overrides stay outside vendor.

Exports: ChannelCard({channel,samples,connected,open,send,busy,acquisitionState,overclockChannel}); CurvePanel({samples,selected,connected,serverTime,recording,settings,simulation,acquisitionPaused,overclockChannel,recordingBadge,recordingContent,expandAction}); ChannelControls({channel,connected,send,simulation}); ChannelReadings({channel,acquisitionState,connected}); ChannelTabs({channels,selected,onSelect}); SamplingFooter({state,view,selected,stale,end}); acquisition/holdPausedHistory/samplingStale/samplingLabel/lastSampleTime/sampleBreak. COLORS/fmt/clock/Status/Metric/PowerMetric/TemperatureChart are available for host composition.

CurvePanel slots are rendered React nodes, not local implementations: recordingBadge, recordingContent, expandAction. Local host may pass RecordingPill, RecordingBar and an expand button. Manual chart display pause is built in; host immersive layout remains local. Use holdPausedHistory for channel-specific frozen real history, acquisition for states, and source original revisions; never synthesize samples. Shared display.d.ts contains no local settings, private paths or credentials.

The website compatibility shared/ modules only re-export this package. They are not independent editable implementations. Local host imports the verified vendor package and owns its existing wrapper.

1.0.1: acquisition-pause overlay, dimmed traces and last-reading CSS now ship in the shared package, independent of website host styles. Component API unchanged.

## 1.0.2 refinement

`SamplingFooter` now includes a compact rate-limit notice for connected, actively sampled channels in the current view, for example `CH1 降频 1 Hz`. Hosts consuming this version should remove their separate rate-notice banner to avoid duplicate notices. No sampling, transport or instrument behavior changes.

## 1.1.0 settings primitives

`ToggleSwitch({checked,onChange,disabled,label,id})` supplies the shared accessible sliding policy switch. `onChange` receives the next boolean; it never submits a form or sends commands. Hosts still own all settings menus, local protection and per-window website preferences. Existing component APIs are unchanged.

## 1.2.0 thermal display

`Status` shows blue 制冷 for positive current and red 加热 for negative current while output is on. `ChannelCard` and `ChannelReadings` accept `thermalColors?: boolean` (default true); false removes their thermal background and border only, preserving direction labels. Disconnected, paused or stale cards remain neutral. Power magnitude `abs(I*V)` controls low-saturation nonlinear tint via `thermalState`; the 250 mW knee is a display scale, not an instrument limit.

Hosts may import `useThermalColors` from `preferences.js` to keep the default-on preference in browser-local storage (`qchip.tec.thermal-colors.v1`). The hook does not call transports or write source settings. It applies equally to readers and operators. Hosts own the settings row, labeled 温度多彩显示, and pass its value to both card types. Native host settings remain lab-owned. Website dark-theme color tokens are host-specific; shared defaults match the daytime palette.

## 1.3.0 · TEC product update

Dynamic 1/4-channel display helpers; no fixed-four assumption in last-reading and paused history. SamplingPolicy v2 supports normal free-running acquisition. Footer intentionally shows timestamp and optional state.logging.active only. Source model, connection, acquisition and log I/O stay host-owned. Chart recording shading/start markers are removed; deprecated host recording slots remain compatible but new hosts must not populate them. Thermal settings, PAUSED, real-sample segmentation and control guards are unchanged.


## Log display (1.4.0)

`import {LogPage} from './vendor/tec-ui/logs.jsx'`, with the existing styles.css import.
`<LogPage transport={stableLogTransport} serverMs={state.server_ms} channelIds={actualChannelIds} storageSlot={hostLogStatus}/>`.
The host supplies a stable object with `series({start,end,channels,metric,signal})`, `table({start,end,channel,page,signal})` and either synchronous `exportUrl({start,end,channel})` or async `exportCsv({start,end,channel})`. Types in logs.d.ts. Returned chart/table `start_ms/end_ms` must match the requested integer UTC range. AbortSignal is propagated; no host endpoint, auth token, path setting or interval policy is in the shared component. Re-mount on physical-device switch. Supports one/four actual channels and retains last chosen range as host state updates; it never follows latest implicitly.

Native-only storage status/settings use storageSlot, or a sibling in the host. Table columns are a display preference only; exports retain all original fields. No website historical tab is enabled until a real cloud history transport exists.

For native token-protected GETs use exportCsv: the host fetches with its header token, creates/downloads a Blob, and revokes its object URL. Never put the local token in an anchor or query. Download errors remain visible in the shared page.

## 1.4.1 · bounded cloud history

`retentionHours={72}` adds the explicit last-72-hour preset and limits custom-range duration to72h. Omit the prop for the existing native7-day maximum. `refreshKey` triggers a new read for the same fixed range without resetting the selected metric/channel/page. Cross-day axes include dates. The host enforces retention and read permission; the component neither uploads logs nor controls sampling. Website history uses a real device-scoped cloud transport. Native1.4.0 remains compatible until the next verified package consumption.

## 1.4.2 · date/time selection

The custom log range now uses a calendar and centred hour/minute/second wheels with keyboard entry. The popover uses the browser top layer to escape scrolling host containers. Values remain UTC+8 wall time with millisecond precision and an exclusive end. Picker Cancel/light-dismiss discards its draft; picker Confirm changes the form draft only, and Apply remains the only query commit. Both explanatory bottom footnotes are removed. Host timestamps and product versions are unaffected. No transport or instrument changes.
