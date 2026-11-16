"""Native-only history/settings routes. Never exposed through the cloud broker."""
import csv
import io
import time
from contextlib import closing
from urllib.parse import parse_qs,urlsplit

from .log_store import FIELDS

BASE='/api/local-log/'


def time_range(args):
    start,end=int(args['start'][0]),int(args['end'][0])
    if not 0<=start<end<=253402300799999 or end-start>7*86400000:
        raise ValueError('请选择不超过7天的固定时间范围')
    return start,end


def handle(handler):
    parsed=urlsplit(handler.path)
    if not parsed.path.startswith(BASE):return False
    # GETs also carry a local session token; paths/config do not belong on a public API.
    if not handler.trusted(writing=True):
        handler.send({'error':'FORBIDDEN'},403);return True
    model=handler.server.sim
    log=getattr(model,'daily_log',None)
    if log is None or not hasattr(log,'state'):
        handler.send({'error':'LOCAL_LOG_UNAVAILABLE'},503);return True
    route=parsed.path[len(BASE):]
    try:
        if handler.command=='POST':
            body=handler.read_body()
            if route=='interval' and set(body)=={'interval_seconds'}:
                result=log.configure_interval(body['interval_seconds'])
            elif route=='storage' and set(body)=={'directory','prefix'}:
                log.configure_storage(body['directory'],body['prefix'])
                result=log.state(model.channel_ids)
            elif route=='enabled' and set(body)=={'enabled'} and type(body['enabled']) is bool:
                model.configure(dict(model.settings,log_enabled=body['enabled']))
                result=log.state(model.channel_ids)
                if body['enabled'] and not result['logging']['active']:
                    raise ValueError(result['logging']['error'] or '日志尚未就绪')
            else:raise ValueError('Unsupported local log operation')
            handler.send(result);return True
        if handler.command!='GET':raise ValueError('Unsupported method')
        args=parse_qs(parsed.query,strict_parsing=True) if parsed.query else {}
        if route=='state':
            handler.send(log.state(model.channel_ids));return True
        if log.store is None and not log.open():raise ValueError(log.error or 'Local database unavailable')
        if not log.store:raise ValueError('Local database unavailable')
        if route=='bounds':
            handler.send(log.store.bounds());return True
        start,end=time_range(args)
        channel_ids=model.channel_ids or log.store.channels()
        if route=='series':
            channels=[int(ch) for ch in args['channels'][0].split(',')]
            if len(set(channels))!=len(channels) or any(ch not in channel_ids for ch in channels):raise ValueError('Invalid discovered channel')
            result=log.store.series(start,end,channels,args['metric'][0],int(args.get('buckets',['600'])[0]))
        else:
            channel=int(args['channel'][0])
            if channel not in channel_ids:raise ValueError('Invalid discovered channel')
            if route=='table':
                result=log.store.query(start,end,channel,int(args.get('page',['0'])[0]),int(args.get('page_size',['50'])[0]))
            elif route=='export.csv':
                stream=io.StringIO(newline='');writer=csv.writer(stream)
                writer.writerows([['#format','qchip.tec.local-log-export.v1'],['#time','UTC ISO8601 milliseconds; [start,end)'],FIELDS])
                with closing(log.store.read()) as connection:
                    connection.execute('BEGIN')
                    total=connection.execute('SELECT COUNT(*) FROM samples WHERE ts_ms>=? AND ts_ms<? AND channel=?',(start,end,channel)).fetchone()[0]
                    if total>100000:raise ValueError('导出上限100000行，请缩小时间范围')
                    for row in connection.execute('SELECT * FROM samples WHERE ts_ms>=? AND ts_ms<? AND channel=? ORDER BY ts_ms,session_id,seq',(start,end,channel)):
                        writer.writerow([row[k] for k in FIELDS])
                # Browser receives this through authenticated fetch; no token in a URL.
                handler.send_bytes(('\ufeff'+stream.getvalue()).encode('utf-8'),'text/csv; charset=utf-8')
                return True
            else:raise ValueError('Unknown local log route')
        handler.send(result)
    except (ValueError,TypeError,KeyError,OverflowError) as error:
        handler.send({'error':str(error)},400)
    except Exception as error:
        handler.send({'error':'LOCAL_LOG_API_FAILED: {}: {}'.format(type(error).__name__,error)},503)
    return True
