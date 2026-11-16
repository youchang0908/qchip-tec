"""Portable storage layout. No implicit import of a legacy user profile."""
import hashlib,json,os,platform,sys,secrets
from dataclasses import dataclass
from pathlib import Path

_active=None

def machine_key():
    if os.name=='nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Cryptography',0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            identity=winreg.QueryValueEx(key,'MachineGuid')[0]
    else:identity=platform.node()
    if not isinstance(identity,str) or not identity:raise ValueError('无法识别本机，未加载设备身份')
    return hashlib.sha256(('QchipTEC portable host v1\0'+identity).encode()).hexdigest()

@dataclass(frozen=True)
class Layout:
    base:Path
    install:Path
    key:str
    csv:Path
    legacy_local:Path=None
    @property
    def native(self):return self.base if self.legacy_local else self.base/'native'
    @property
    def bridge(self):return self.legacy_local/'QchipTEC/HardwareReadonly' if self.legacy_local else self.base/'bridge'
    @property
    def history(self):
        if self.legacy_local:
            namespace=hashlib.sha256(str((self.native/'.runtime/settings.json').resolve()).encode('utf-8')).hexdigest()[:16]
            return self.legacy_local/'QchipTEC/LocalLogs'/('hardware-'+namespace)
        return self.base/'history'
    @property
    def updates(self):return self.base/'updates'
    def bind(self):
        if self.legacy_local:
            marker=self.base/'.runtime/legacy-host.json'
            if marker.exists():
                if json.loads(marker.read_text(encoding='utf-8'))!={'schema':1,'machine_key':self.key}:
                    raise ValueError('旧配置属于另一台电脑；请显式导入并重新配对')
            else:
                with marker.open('x',encoding='utf-8') as file:json.dump({'schema':1,'machine_key':self.key},file)
            return self
        self.base.mkdir(parents=True,exist_ok=True)
        marker=self.base/'portable-host.json'
        expected={'schema':1,'machine_key':self.key,'default_csv':str(self.csv)}
        if marker.exists():
            previous=json.loads(marker.read_text(encoding='utf-8'))
            if previous.get('schema')!=1 or previous.get('machine_key')!=self.key:
                raise ValueError('此数据目录属于另一台电脑；请使用新电脑的默认目录并重新配对')
            old_csv=previous.get('default_csv')
            if old_csv and old_csv!=str(self.csv):
                # Relocate only the known default. A user-selected external
                # directory is kept verbatim. Never touch measurements/CSV.
                for path,field in ((self.native/'.runtime/settings.json','recording_dir'),(self.history/'csv_settings.json','directory')):
                    if path.exists():
                        data=json.loads(path.read_text(encoding='utf-8'))
                        if data.get(field)==old_csv:
                            data[field]=str(self.csv);atomic_json(path,data)
                atomic_json(marker,expected)
        else:
            if any((self.base/name).exists() for name in ('native','bridge','history')):
                raise ValueError('现有数据缺少本机标记；请使用新目录和显式导入工具，不自动接管身份')
            with marker.open('x',encoding='utf-8') as file:json.dump(expected,file)
        return self

def layout(explicit=None,install_root=None,host=None,frozen=None,bind=False):
    frozen=getattr(sys,'frozen',False) if frozen is None else frozen
    if explicit is None and not frozen:raise ValueError('源码/开发模式必须显式指定 --data-dir')
    install=Path(install_root or Path(sys.executable).parent).resolve()
    key=host or machine_key()
    # Keep Windows paths short; the full digest in portable-host.json still
    # fences a hypothetical shortened-directory collision before loading data.
    base=Path(explicit).resolve() if explicit is not None else install/'data/machines'/key[:16]
    # 1.0.0's updater passes the old native root, not a new portable base.
    # Only an explicit profile selected by this Windows user's existing launcher
    # may keep the shared legacy broker/history. Never search/import on defaults.
    if explicit is not None and (base/'.runtime/settings.json').is_file() and not (base/'portable-host.json').exists():
        local=Path(os.environ['LOCALAPPDATA']).resolve()
        launcher=local/'QchipTEC/BridgeSettingsLauncher/launcher.json'
        selected=local/'QchipTEC/Desktop'
        if launcher.exists():
            if launcher.stat().st_size>8192:raise ValueError('旧启动配置过大，停止升级')
            data=json.loads(launcher.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or set(data)!={'data_dir'} or not isinstance(data['data_dir'],str) or not Path(data['data_dir']).is_absolute():
                raise ValueError('旧启动配置无效，停止升级')
            selected=Path(data['data_dir']).resolve()
        if selected!=base:raise ValueError('旧配置与本机启动记录不匹配；请显式导入，不复用配对')
        marker=base/'.runtime/legacy-host.json'
        if marker.exists() and json.loads(marker.read_text(encoding='utf-8'))!={'schema':1,'machine_key':key}:
            raise ValueError('旧配置属于另一台电脑；请显式导入并重新配对')
        settings=json.loads((base/'.runtime/settings.json').read_text(encoding='utf-8'))
        result=Layout(base,install,key,Path(settings.get('recording_dir',str(base/'TEC_data'))),local)
        return result.bind() if bind else result
    standard=base==install/'data/machines'/key[:16]
    result=Layout(base,install,key,install/'log' if standard else base/'log')
    return result.bind() if bind else result

def configure(explicit=None,**options):
    global _active
    _active=layout(explicit,bind=True,**options)
    return _active

def current():return _active

def bridge_directory():
    if _active:return _active.bridge
    # Library-only compatibility for existing isolated fixtures. Executable and
    # source CLI entries always configure an explicit/portable layout first.
    return Path(os.environ['LOCALAPPDATA'])/'QchipTEC/HardwareReadonly'

def native_broker_token():
    if _active:return broker_profile(_active.native)/'private/local_broker_token.txt'
    return Path(os.environ['LOCALAPPDATA'])/'QchipTEC/HardwareControl/private/local_broker_token.txt'


def broker_profile(native,simulation=False):
    if not simulation and _active and _active.legacy_local:
        return _active.legacy_local/'QchipTEC/HardwareControl'
    return Path(native)/'.runtime'/('HardwareControlSimulation' if simulation else 'HardwareControl')


def ui_preferences_path(native,simulation=False):
    if not simulation and _active and _active.legacy_local:
        return _active.legacy_local/'QchipTEC/UiPreferences/display.json'
    return Path(native)/'.runtime'/('UiPreferencesSimulation' if simulation else 'UiPreferences')/'display.json'

def profile_id(directory):return hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()

def atomic_json(path,value):
    temporary=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as file:
            json.dump(value,file,ensure_ascii=False);file.flush();os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
