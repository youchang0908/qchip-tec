// React default import is required by the Windows classic JSX compiler.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React,{useEffect,useId,useRef,useState} from 'react';
import {Plus,X} from 'lucide-react';
import {TABLE_COLUMNS,normalizeTableColumns,toggleTableColumn,TABLE_COLUMN_PREFERENCE} from './log_model.js';

export function useTableColumns(){
  const [columns,setColumns]=useState(()=>normalizeTableColumns(null));
  const [loaded,setLoaded]=useState(false),[preferenceError,setPreferenceError]=useState(false);
  useEffect(()=>{try{setColumns(normalizeTableColumns(JSON.parse(localStorage.getItem(TABLE_COLUMN_PREFERENCE))));}catch{setPreferenceError(true);}setLoaded(true);},[]);
  useEffect(()=>{if(!loaded)return;try{localStorage.setItem(TABLE_COLUMN_PREFERENCE,JSON.stringify(columns));setPreferenceError(false);}catch{setPreferenceError(true);}},[columns,loaded]);
  return {columns,setColumns,preferenceError};
}

export function TableColumnPicker({columns,setColumns,preferenceError}){
  const [open,setOpen]=useState(false);
  const popupId=useId();
  const host=useRef(null),button=useRef(null),first=useRef(null);
  useEffect(()=>{
    if(!open)return;
    first.current?.focus();
    const outside=event=>{if(!host.current?.contains(event.target))setOpen(false);};
    const escape=event=>{if(event.key==='Escape'){event.preventDefault();setOpen(false);button.current?.focus();}};
    document.addEventListener('pointerdown',outside);
    document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
  },[open]);
  return <div className="column-picker" ref={host} onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))setOpen(false);}}>
    <button className="column-picker-button" ref={button} type="button" aria-label="选择显示列" title="选择显示列" aria-expanded={open} aria-controls={popupId} onClick={()=>setOpen(value=>!value)}><Plus size={16}/></button>
    {open&&<div className="column-picker-panel" id={popupId} role="group" aria-label="选择表格测量列">
      <div className="column-picker-title"><strong>显示列</strong><button type="button" aria-label="关闭列选择" onClick={()=>{setOpen(false);button.current?.focus();}}><X size={15}/></button></div>
      <div className="column-fixed">时间 <span>UTC+8 · 固定显示</span></div>
      {TABLE_COLUMNS.map((column,index)=><label key={column.key}><input ref={index===0?first:undefined} type="checkbox" checked={columns.includes(column.key)} onChange={()=>setColumns(previous=>toggleTableColumn(previous,column.key))}/><span>{column.label}</span><small>{column.unit}</small></label>)}
      <p>电功率 = |I×V|，不是制冷功率。</p>
      <p>CSV 导出完整字段，保留 UTC 原始时间。</p>
      {preferenceError&&<p role="status">无法保存本机偏好，本次选择仍然有效。</p>}
    </div>}
  </div>;
}
