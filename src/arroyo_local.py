"""Typed local controls, explicitly authorized by the user on 2026-10-25.

No raw SCPI, reset, calibration, limit-setting, or remote-control entry point.
The caller must serialize the complete transaction on the device owner thread.
"""
import math
import time
from contextlib import nullcontext
from arroyo_readonly import ArroyoReadOnly


class CommandRejected(ValueError):
    pass


class CommandUncertain(RuntimeError):
    pass


def finite(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise CommandRejected('请输入有限数值')
    return float(value)


def validate_command(body):
    if not isinstance(body, dict) or set(body) != {'id','channel','type','params','expires_at'}:
        raise CommandRejected('指令结构无效')
    if not isinstance(body['id'], str) or not 1 <= len(body['id']) <= 80:
        raise CommandRejected('指令 ID 无效')
    if type(body['channel']) is not int or body['channel'] not in (1,2,3,4):
        raise CommandRejected('通道无效')
    if not time.time() < finite(body['expires_at']) <= time.time() + 10:
        raise CommandRejected('指令已过期，请重新操作')
    kind, params = body['type'], body['params']
    if not isinstance(params, dict):
        raise CommandRejected('参数格式无效')
    if kind == 'setpoint' and set(params) == {'mode','value'} and params['mode'] in ('T','R','ITE'):
        finite(params['value'])
    elif kind == 'pid' and set(params) == {'values'}:
        if not isinstance(params['values'], list) or len(params['values']) != 3:
            raise CommandRejected('PID 需要三个数值')
        if any(not 0 <= finite(v) <= 1000 for v in params['values']):
            raise CommandRejected('PID 参数范围为 0–1000')
    elif kind == 'output' and set(params) == {'enabled'} and type(params['enabled']) is bool:
        pass
    elif kind == 'mode' and set(params) == {'mode'} and params['mode'] in ('T','R','ITE'):
        pass
    else:
        raise CommandRejected('不支持的操作；不接受原始设备命令')


class ArroyoLocal(ArroyoReadOnly):
    writable_channels = frozenset((1,2,3,4))

    def __init__(self, manager_factory=None):
        super().__init__(manager_factory)
        self.parameter_write_attempts = 0

    def command(self, body, authorize, write_lock=None):
        validate_command(body)
        authorize()
        ch, kind, params = body['channel'], body['type'], body['params']
        if ch not in self.writable_channels:
            raise CommandRejected('此通道不可写')
        self.select(ch)
        if kind == 'setpoint':
            mode = self.query('TEC:MODE?').upper()
            if mode != params['mode']:
                raise CommandRejected('设备模式已改变，请刷新后重试')
            if mode == 'ITE':
                limit = abs(self.scalar('TEC:LIM:ITE?'))
                low, high = -limit, limit
            else:
                low = self.scalar('TEC:LIM:' + mode + 'LO?')
                high = self.scalar('TEC:LIM:' + mode + 'HI?')
            value = finite(params['value'])
            if not low <= value <= high:
                raise CommandRejected('超出设备当前限值：{} 至 {}'.format(low, high))
            command = 'TEC:{} {:.12g}'.format(mode, value)
        elif kind == 'pid':
            command = 'TEC:PID ' + ','.join(format(v, '.12g') for v in params['values'])
        elif kind == 'output':
            command = 'TEC:OUTput {}'.format(int(params['enabled']))
        else:
            if self.scalar('TEC:OUTput?') != 0:
                raise CommandRejected('请先关闭该通道输出，再切换模式')
            command = 'TEC:MODE:' + params['mode']
        # Repeat authorization/expiry immediately before the actual mutation.
        try:
            with write_lock if write_lock is not None else nullcontext():
                validate_command(body)
                authorize()
                self._owned()
                self.parameter_write_attempts += 1
                self.inst.write(command)
            reading = self.read_channel(ch, full=True)
        except CommandRejected:
            raise
        except Exception as error:
            raise CommandUncertain('写入结果待确认，通信已停止；不会自动重发。' + str(error)) from error
        if kind == 'setpoint':
            confirmed = reading['mode'] == params['mode'] and math.isclose(
                reading['setpoints'][params['mode']], params['value'], rel_tol=1e-6, abs_tol=.00051)
        elif kind == 'pid':
            confirmed = all(math.isclose(a,b,rel_tol=1e-5,abs_tol=.00005) for a,b in zip(reading['pid'],params['values']))
        elif kind == 'output':
            confirmed = reading['output'] == params['enabled']
        else:
            confirmed = reading['mode'] == params['mode']
        if not confirmed:
            raise CommandUncertain('写入未获读回确认，通信已停止；请重新连接查看实际值，不会自动重发。')
        return reading
