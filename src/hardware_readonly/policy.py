"""Validate trusted source policy; never accepts or executes a policy mutation."""
import copy
import math
from .source import Rejected


def finite(value, high):
    return type(value) in (int,float) and math.isfinite(value) and 0 <= value <= high


def _validate_v1_policy(value, connected, rates):
    if value is None:return None
    keys={'version','revision','mode','channel','target','effective_hz','measured_hz','channels'}
    if not isinstance(value,dict) or set(value)!=keys:raise Rejected('invalid sampling policy keys')
    if type(value['version']) is not int or value['version']!=1:raise Rejected('invalid sampling policy version')
    if type(value['revision']) is not int or not 0<=value['revision']<=2**53-1:raise Rejected('invalid policy revision')
    target=value['target'];mode=value['mode'];ch=value['channel']
    if mode not in ('four_channel','single_channel_overclock'):raise Rejected('invalid policy mode')
    if not isinstance(target,dict):raise Rejected('invalid policy target')
    if mode=='four_channel':
        if ch is not None or target!={'kind':'fixed','hz':2} or type(target.get('hz')) is not int or value['effective_hz'] is not None or value['measured_hz'] is not None:
            raise Rejected('invalid four-channel policy')
    else:
        if type(ch) is not int or ch not in (1,2,3,4):raise Rejected('invalid policy channel')
        if target!={'kind':'max'} and not (set(target)=={'kind','hz'} and target['kind']=='fixed' and type(target['hz']) is int and 2<=target['hz']<=10):
            raise Rejected('invalid overclock target')
        for key,high in [('effective_hz',50),('measured_hz',100)]:
            if value[key] is not None and not finite(value[key],high):raise Rejected('invalid policy rate')
        if target['kind']=='fixed' and value['effective_hz'] is not None and value['effective_hz']>target['hz']:
            raise Rejected('fixed effective rate exceeds target')
    states=value['channels']
    if not isinstance(states,dict) or set(states)!={'1','2','3','4'}:raise Rejected('invalid channel acquisition states')
    if not isinstance(rates,dict) or set(rates)!={'1','2','3','4'}:raise Rejected('invalid channel rates')
    for key in ('1','2','3','4'):
        paused=mode=='single_channel_overclock' and int(key)!=ch
        expected='disconnected' if not connected else 'paused' if paused else 'sampling'
        reason='single_channel_overclock' if expected=='paused' else None
        if states[key]!={'state':expected,'reason':reason}:raise Rejected('inconsistent acquisition state')
        if not finite(rates[key],2 if mode=='four_channel' else 50) or paused and rates[key]!=0:
            raise Rejected('inconsistent acquisition rate')
        if not connected and rates[key]!=0:raise Rejected('disconnected channel rate must be zero')
    return copy.deepcopy(value)


def _validate_v1_rate_status(status, policy, rates, connected):
    if policy is None:return
    if not isinstance(status,dict) or set(status)!={'1','2','3','4'}:raise Rejected('missing policy rate status')
    for ch,item in status.items():
        if not isinstance(item,dict) or set(item)!={'requested_hz','effective_hz','measured_hz','limited'}:
            raise Rejected('invalid rate status keys')
        if not finite(item['requested_hz'],50) or not finite(item['effective_hz'],50) or type(item['limited']) is not bool:
            raise Rejected('invalid requested/effective status')
        if item['measured_hz'] is not None and not finite(item['measured_hz'],100):raise Rejected('invalid measured status')
        if item['effective_hz']!=rates[ch] or item['effective_hz']>item['requested_hz']:
            raise Rejected('inconsistent effective status')
        if not connected and (item['effective_hz']!=0 or item['measured_hz'] is not None):raise Rejected('invalid disconnected status')
        paused=policy['mode']=='single_channel_overclock' and int(ch)!=policy['channel']
        if paused and (item['requested_hz']!=0 or item['effective_hz']!=0 or item['measured_hz'] is not None):raise Rejected('invalid paused status')


def validate_policy(value, connected, rates):
    if value is not None and not isinstance(value,dict):raise Rejected('invalid policy type')
    if value is None or value.get('version') == 1:
        return _validate_v1_policy(value, connected, rates)
    keys={'version','revision','mode','channel','target','effective_hz','measured_hz','channels'}
    if not isinstance(value,dict) or set(value)!=keys or type(value['version']) is not int or value['version']!=2:
        raise Rejected('invalid v2 policy keys/version')
    if type(value['revision']) is not int or not 0<=value['revision']<=2**53-1:
        raise Rejected('invalid policy revision')
    states=value['channels']
    if not isinstance(states,dict) or (set(states) not in ({'1'},{'1','2','3','4'}) and not (not connected and states=={})):
        raise Rejected('invalid discovered acquisition states')
    if not isinstance(rates,dict) or set(rates)!=set(states):
        raise Rejected('invalid discovered rates')
    mode,channel,target=value['mode'],value['channel'],value['target']
    if not isinstance(target,dict):raise Rejected('invalid policy target')
    if mode=='normal':
        if (channel is not None or set(target)!={'kind','min_sleep_ms'} or target['kind']!='unlimited'
                or type(target['min_sleep_ms']) is not int or not 1<=target['min_sleep_ms']<=5000
                or value['effective_hz'] is not None or value['measured_hz'] is not None):
            raise Rejected('invalid normal sampling policy')
    elif mode=='single_channel_overclock':
        if type(channel) is not int or str(channel) not in states:raise Rejected('invalid policy channel')
        if target!={'kind':'max'} and not (set(target)=={'kind','hz'} and target['kind']=='fixed' and type(target['hz']) is int and 2<=target['hz']<=10):
            raise Rejected('invalid overclock target')
        if value['effective_hz'] is not None and (not finite(value['effective_hz'],50) or target['kind']=='fixed' and value['effective_hz']>target['hz']):
            raise Rejected('invalid effective overclock rate')
        if value['measured_hz'] is not None and not finite(value['measured_hz'],1000):raise Rejected('invalid measured rate')
    else:
        raise Rejected('invalid policy mode')
    for ch,state in states.items():
        paused=mode=='single_channel_overclock' and int(ch)!=channel
        expected='disconnected' if not connected else 'paused' if paused else 'sampling'
        reason='single_channel_overclock' if expected=='paused' else None
        if state!={'state':expected,'reason':reason}:raise Rejected('inconsistent channel state')
        if not finite(rates[ch],1000) or (not connected or paused) and rates[ch]!=0:
            raise Rejected('inconsistent channel rate')
    return copy.deepcopy(value)


def validate_rate_status(status, policy, rates, connected):
    if policy is None or policy['version']==1:
        return _validate_v1_rate_status(status,policy,rates,connected)
    if not isinstance(status,dict) or set(status)!=set(policy['channels']):raise Rejected('invalid rate status channels')
    for ch,item in status.items():
        if not isinstance(item,dict) or set(item)!={'requested_hz','effective_hz','measured_hz','limited'}:
            raise Rejected('invalid rate status keys')
        if not finite(item['requested_hz'],1000) or not finite(item['effective_hz'],1000) or type(item['limited']) is not bool:
            raise Rejected('invalid rate status')
        if item['effective_hz']!=rates[ch] or item['measured_hz'] is not None and not finite(item['measured_hz'],1000):
            raise Rejected('inconsistent rate status')
        if policy['mode']=='normal':
            if item['requested_hz']!=0 or item['limited']:raise Rejected('normal mode has no Hz target')
        elif item['effective_hz']>item['requested_hz']:
            raise Rejected('effective rate exceeds target')
        paused=policy['mode']=='single_channel_overclock' and int(ch)!=policy['channel']
        if not connected and (item['effective_hz']!=0 or item['measured_hz'] is not None):raise Rejected('disconnected rate status')
        if paused and (item['requested_hz']!=0 or item['effective_hz']!=0 or item['measured_hz'] is not None):raise Rejected('paused rate status')
