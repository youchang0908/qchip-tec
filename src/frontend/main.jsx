import React from 'react';
import {createRoot} from 'react-dom/client';
import {TecPanel} from './TecPanel.jsx';
import {LocalTransport} from './transport.js';

const transport = new LocalTransport();
createRoot(document.getElementById('root')).render(<TecPanel transport={transport}/>);
