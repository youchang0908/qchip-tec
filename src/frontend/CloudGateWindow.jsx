import React from 'react';
import {createRoot} from 'react-dom/client';
import {CloudLinkSetting} from './CloudLinkSetting.jsx';
import './styles.css';
createRoot(document.getElementById('root')).render(<main className="qchip-tec cloud-gate-window">
  <h1>Qchip TEC</h1><CloudLinkSetting endpoint=""/>
</main>);
