"""Native global Save: validate all drafts first; commit files and runtime together."""
import copy
import os
from pathlib import Path
import secrets
from hardware_control.ui_preferences import UiPreferences
from local_log.csv_mirror import validate_settings
from settings_transaction import Transaction


def commit(model,ui,body):
    if not isinstance(body,dict) or set(body)!={'settings','storage','preferences'}:
        raise ValueError('设置格式无效')
    # Lock order matches normal configure; no instrument request is submitted.
    with model.sampling_lock,model.lock,ui.lock:
        proposed=dict(body['settings'])
        for k in ('overclock_enabled','overclock_channel','overclock_target','update_interval_ms','baud_rate','log_enabled','device_name'):
            proposed.setdefault(k,model.settings[k])
        settings=model.validate_settings(proposed)
        if model.connection_state in ('connecting','disconnecting') and any(settings[k]!=model.settings[k] for k in ('device_port','baud_rate')):
            raise ValueError('通信连接正在处理中，请稍后修改端口')
        if settings['overclock_enabled'] and settings['overclock_channel'] not in model.channel_ids:
            raise ValueError('超频通道无效')
        prefs=UiPreferences.validate(body['preferences'])
        log=model.daily_log;storage=body['storage'];changed=False
        if storage is not None:
            if not isinstance(storage,dict) or set(storage)!={'directory','prefix','expected_directory','expected_prefix'}:
                raise ValueError('日志设置格式无效')
            directory,prefix=validate_settings(storage['directory'],storage['prefix'])
            if (str(log.directory),log.prefix)!=(storage['expected_directory'],storage['expected_prefix']):
                raise ValueError('日志保存位置已改变，请重新打开设置')
            changed=(directory,prefix)!=(log.directory,log.prefix)
            if changed:
                if log.enabled:raise ValueError('请先关闭日志，再修改保存位置')
                if not log.open() or not log.store or log.store.error:
                    raise ValueError('日志存储故障，请修复后重新保存')
                # Validate actual destination writability before any settings commit.
                directory.mkdir(parents=True,exist_ok=True)
                probe=directory/('.qchip-writecheck-'+secrets.token_hex(10))
                try:
                    with probe.open('xb') as f:f.write(b'');f.flush();os.fsync(f.fileno())
                finally:
                    if probe.exists():probe.unlink()
        if not model.settings_path:raise ValueError('设置存储位置不可用')
        updates={model.settings_path:dict(settings,overclock_enabled=False),ui.path:prefs}
        if changed:updates[log.root/'csv_settings.json']=dict(directory=str(directory),prefix=prefix)
        previous=copy.deepcopy(model.settings);old_storage=(log.directory,log.prefix)
        journal=model.settings_path.with_name('settings-transaction.json')
        with Transaction(journal,updates):
            try:
                if changed:log.configure_storage(directory,prefix,persist=False)
                applied=model.configure(settings,_persist=False)
            except Exception:
                if changed and (log.directory,log.prefix)!=old_storage:
                    log.configure_storage(*old_storage,persist=False)
                model.configure(previous,_persist=False)
                raise
        return dict(settings=applied,preferences=prefs)
