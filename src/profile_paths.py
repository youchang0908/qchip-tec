"""Stable existing private profile selection; never migrate or merge user data."""
import json,os
from pathlib import Path

def desktop_profile(explicit=None,local=None):
    if explicit is not None:return Path(explicit).absolute()
    local=Path(local or os.environ['LOCALAPPDATA'])
    legacy=local/'QchipTEC/BridgeSettingsLauncher/launcher.json'
    if legacy.exists():
        if legacy.stat().st_size>8192:raise ValueError('原启动配置过大，请检查本机配置')
        value=json.loads(legacy.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or set(value)!={'data_dir'} or not isinstance(value['data_dir'],str) or not Path(value['data_dir']).is_absolute():
            raise ValueError('原本机数据目录配置无效，请检查后再启动')
        return Path(value['data_dir']).absolute()
    return local/'QchipTEC/Desktop'
