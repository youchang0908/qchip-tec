// React default import is also used by the native host's classic JSX compiler.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React, {useEffect, useId, useLayoutEffect, useRef, useState} from 'react';
import {CalendarDays, ChevronDown, ChevronLeft, ChevronRight} from 'lucide-react';
import {calendarDays, moveMonth, replaceDate, replaceTime} from './datetime_model.js';

const ROW = 34;
const pad = value => String(value).padStart(2, '0');

function TimeWheel({label, value, max, onChange}) {
  const scroll = useRef(null), input = useRef(null), timer = useRef(null), editing = useRef(false), scrolling = useRef(false);
  const [text, setText] = useState(pad(value));
  const change = useRef(onChange);
  useLayoutEffect(() => {change.current = onChange;});
  useLayoutEffect(() => {
    if (!editing.current) setText(pad(value));
    // The parent enters the popover top layer after child layout effects.
    const frame = requestAnimationFrame(() => {
      if (!scrolling.current && Math.abs(scroll.current.scrollTop - value * ROW) > 1) scroll.current.scrollTop = value * ROW;
    });
    return () => cancelAnimationFrame(frame);
  }, [value]);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const node = input.current;
    const wheel = event => {event.preventDefault(); scroll.current.scrollTop += Math.sign(event.deltaY) * ROW;};
    node.addEventListener('wheel', wheel, {passive: false});
    return () => node.removeEventListener('wheel', wheel);
  }, []);
  const select = number => {
    clearTimeout(timer.current);
    const next = Math.max(0, Math.min(max, number));
    setText(pad(next));
    change.current(next);
    scroll.current.scrollTop = next * ROW;
  };
  return <div className="time-wheel">
    <span className="time-wheel-label">{label}</span>
    <div className="time-wheel-body">
      <div className="time-wheel-scroll" ref={scroll} aria-hidden="true" onScroll={() => {
        const next = Math.max(0, Math.min(max, Math.round(scroll.current.scrollTop / ROW)));
        scrolling.current = true;
        if (!editing.current) setText(pad(next));
        change.current(next);
        clearTimeout(timer.current);
        timer.current = setTimeout(() => {scrolling.current = false;}, 100);
      }} onClick={event => {
        const row = event.target.closest('[data-value]');
        if (row) select(Number(row.dataset.value));
      }}>
        {Array.from({length: max + 1}, (_, i) => <div className="time-wheel-option" data-value={i} key={i}>{pad(i)}</div>)}
      </div>
      <input ref={input} className="time-wheel-input" type="text" inputMode="numeric" role="spinbutton" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} maxLength={2} value={text}
        onFocus={event => {editing.current = true; event.currentTarget.select();}}
        onChange={event => setText(event.target.value.replace(/\D/g, '').slice(0, 2))}
        onBlur={() => {editing.current = false; select(Number(text) || 0);}}
        onKeyDown={event => {
          if (['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            select(event.key === 'Home' ? 0 : event.key === 'End' ? max : value + (event.key === 'ArrowUp' ? 1 : -1));
          } else if (event.key === 'Enter') {event.preventDefault(); event.currentTarget.blur();}
        }}/>
    </div>
  </div>;
}

/** Draft-only picker. The host form commits a query only after its Apply action. */
export function DateTimeField({label, value, onChange}) {
  const id = useId(), trigger = useRef(null), panel = useRef(null);
  const [open, setOpen] = useState(false), [draft, setDraft] = useState(value);
  const [cursor, setCursor] = useState({year: Number(value.slice(0, 4)), month: Number(value.slice(5, 7))});
  const [yearText, setYearText] = useState(value.slice(0, 4));
  const close = () => {panel.current?.hidePopover(); setOpen(false); trigger.current?.focus();};
  useLayoutEffect(() => {
    if (!open) return;
    const node = panel.current;
    const toggle = event => {if (event.newState === 'closed') setOpen(false);};
    const escape = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopPropagation(); node.hidePopover(); setOpen(false); trigger.current?.focus();
    };
    node.addEventListener('toggle', toggle);
    node.addEventListener('keydown', escape);
    node.showPopover();
    const place = () => {
      const rect = trigger.current.getBoundingClientRect(), height = node.getBoundingClientRect().height;
      const width = Math.min(352, window.innerWidth - 24);
      node.style.width = width + 'px';
      node.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px';
      const below = rect.bottom + 8;
      node.style.top = Math.max(12, Math.min(below, window.innerHeight - height - 12)) + 'px';
    };
    place();
    const observer = new ResizeObserver(place); observer.observe(node);
    window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    node.querySelector('button[aria-pressed="true"]')?.focus({preventScroll: true});
    return () => {
      observer.disconnect(); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true);
      node.removeEventListener('toggle', toggle);
      node.removeEventListener('keydown', escape);
      if (node.matches(':popover-open')) node.hidePopover();
    };
  }, [open]);
  const changeMonth = step => {
    const next = moveMonth(cursor.year, cursor.month, step);
    if (next.year < 1900 || next.year > 9999) return;
    setCursor(next); setYearText(String(next.year));
  };
  const selectDate = date => {
    setDraft(old => replaceDate(old, date));
    setCursor({year: Number(date.slice(0, 4)), month: Number(date.slice(5, 7))});
    setYearText(date.slice(0, 4));
  };
  return <div className="datetime-field">
    <span id={id + '-label'} className="datetime-label">{label}</span>
    <button className="datetime-trigger" type="button" aria-labelledby={id + '-label ' + id + '-value'} aria-haspopup="dialog" aria-expanded={open} aria-controls={id} ref={trigger} onClick={() => {
      if (open) {close(); return;}
      setDraft(value); setCursor({year: Number(value.slice(0, 4)), month: Number(value.slice(5, 7))}); setYearText(value.slice(0, 4)); setOpen(true);
    }}><CalendarDays size={16}/><span id={id + '-value'}>{value.slice(0, 10).replaceAll('-', '/')}<b>{value.slice(11, 19)}<small>{value.slice(19)}</small></b></span><ChevronDown size={14}/></button>
    {open && <div id={id} ref={panel} popover="auto" role="dialog" aria-label={label + '选择器'} className="datetime-panel">
      <div className="datetime-panel-heading"><strong>{label}</strong><span>UTC+8</span></div>
      <div className="calendar-heading">
        <button type="button" className="calendar-step" aria-label="上个月" onClick={() => changeMonth(-1)}><ChevronLeft size={17}/></button>
        <div className="calendar-month">
          <input aria-label="年份" inputMode="numeric" value={yearText} maxLength={4} onChange={event => {const text = event.target.value.replace(/\D/g, '').slice(0, 4); setYearText(text); if (Number(text) >= 1900) setCursor(old => ({...old, year: Number(text)}));}} onBlur={() => setYearText(String(cursor.year))} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); event.currentTarget.blur();}}}/><span>年</span>
          <select aria-label="月份" value={cursor.month} onChange={event => setCursor(old => ({...old, month: Number(event.target.value)}))}>{Array.from({length: 12}, (_, i) => <option key={i} value={i + 1}>{i + 1} 月</option>)}</select>
        </div>
        <button type="button" className="calendar-step" aria-label="下个月" onClick={() => changeMonth(1)}><ChevronRight size={17}/></button>
      </div>
      <div className="calendar-week" aria-hidden="true">{['一','二','三','四','五','六','日'].map(day => <span key={day}>{day}</span>)}</div>
      <div className="calendar-days">{calendarDays(cursor.year, cursor.month).map(day => <button type="button" key={day.key} data-current={day.current} data-date={day.key} aria-pressed={draft.slice(0, 10) === day.key} aria-label={day.key + (day.current ? '' : '，相邻月份')} onClick={() => selectDate(day.key)} onKeyDown={event => {
        const steps = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7};
        if (!(event.key in steps)) return;
        event.preventDefault();
        const next = new Date(day.key + 'T00:00:00Z'); next.setUTCDate(next.getUTCDate() + steps[event.key]);
        const key = next.toISOString().slice(0, 10);
        selectDate(key);
        requestAnimationFrame(() => panel.current?.querySelector(`[data-date="${key}"]`)?.focus());
      }}>{day.day}</button>)}</div>
      <div className="datetime-time">
        <TimeWheel label="时" value={Number(draft.slice(11, 13))} max={23} onChange={next => setDraft(old => replaceTime(old, 'hour', next))}/>
        <span className="time-colon">:</span>
        <TimeWheel label="分" value={Number(draft.slice(14, 16))} max={59} onChange={next => setDraft(old => replaceTime(old, 'minute', next))}/>
        <span className="time-colon">:</span>
        <TimeWheel label="秒" value={Number(draft.slice(17, 19))} max={59} onChange={next => setDraft(old => replaceTime(old, 'second', next))}/>
        <label className="datetime-milliseconds">毫秒<input aria-label="毫秒" type="text" inputMode="numeric" maxLength={3} value={Number(draft.slice(20, 23))} onFocus={event => event.currentTarget.select()} onChange={event => setDraft(old => replaceTime(old, 'millisecond', event.target.value.replace(/\D/g, '')))} onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault(); event.currentTarget.blur();}}}/></label>
      </div>
      <div className="datetime-actions"><button type="button" className="button secondary" onClick={close}>取消</button><button type="button" className="button primary" onClick={() => {onChange(draft); close();}}>确定</button></div>
    </div>}
  </div>;
}
