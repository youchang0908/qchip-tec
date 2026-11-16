import React from 'react';
import {createRoot} from 'react-dom/client';
import {SettingsDialog} from './SettingsDialog.jsx';
import './styles.css';
const settings={read_only:true,device_port:'COM8',detail_hz:2,display_seconds:120,smooth_plot:true,
  recording_dir:'离线界面验证',filename_prefix:'Arroyo_trace'};
createRoot(document.getElementById('root')).render(<div className="qchip-tec"><SettingsDialog settings={settings} recording={false}
  save={async()=>{}} close={()=>{}} cloudEndpoint=""/></div>);
