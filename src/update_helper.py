"""Bounded program-only in-place replacement; never walks or migrates user data."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from updates import PROGRAM_FILES,validate_manifest,version
from version import PRODUCT_VERSION


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(65536),b''):h.update(block)
    return h.hexdigest()


def regular_path(path):
    path=Path(path).absolute()
    for p in (path,)+tuple(path.parents):
        if p.exists():
            st=p.lstat()
            if p.is_symlink() or getattr(st,'st_file_attributes',0)&1024:
                raise ValueError('更新路径包含链接或重解析点')
    return path.resolve()


def wait_pids(pids,image,timeout=60):
    if len(pids)>8 or any(type(p) is not int or p<=0 or p==os.getpid() for p in pids):raise ValueError('无效更新进程')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_uint32)]
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handles=[]
    try:
        for pid in set(pids):
            h=kernel.OpenProcess(0x100000|0x1000,False,pid)
            if not h:
                if ctypes.get_last_error()==87:continue
                raise OSError('无法确认旧程序退出')
            handles.append(h);buf=ctypes.create_unicode_buffer(32768);size=ctypes.c_uint32(len(buf))
            if not kernel.QueryFullProcessImageNameW(h,0,buf,ctypes.byref(size)) or Path(buf.value).resolve()!=image:
                raise ValueError('更新等待进程与当前安装程序不符')
        deadline=time.monotonic()+timeout
        while any(kernel.WaitForSingleObject(h,0)!=0 for h in handles):
            if time.monotonic()>=deadline:raise TimeoutError('旧程序尚未正常退出，未替换文件')
            time.sleep(.2)
    finally:
        for h in handles:kernel.CloseHandle(h)


def apply(plan,wait=wait_pids,replace=os.replace):
    if set(plan)!={'schema','target','staged','manifest','old_sha256','pids','restart_args'} or plan['schema']!=1:
        raise ValueError('无效更新计划')
    m=validate_manifest(plan['manifest'],plan['manifest'].get('version'))
    if version(m['version'])<=version(PRODUCT_VERSION):raise ValueError('更新版本必须高于当前版本')
    target=regular_path(plan['target']);stage=regular_path(plan['staged'])
    if not target.is_dir() or not stage.is_dir() or target==stage or stage in target.parents:
        raise ValueError('更新目标与暂存目录无效')
    if target in stage.parents:
        try:stage.relative_to(target/'data')
        except ValueError:raise ValueError('安装根内的暂存目录须位于 data 下') from None
    old=target/'Qchip TEC.exe'
    if not old.is_file() or digest(old)!=plan['old_sha256']:raise ValueError('当前程序已改变，取消更新')
    names=[f['path'] for f in m['files']]
    for f in m['files']:
        p=regular_path(stage/f['path'])
        if not p.is_file() or p.stat().st_size!=f['bytes'] or digest(p)!=f['sha256']:
            raise ValueError('暂存文件校验失败')
        if (target/f['path']).exists():regular_path(target/f['path'])
    if json.loads((stage/'version.json').read_text(encoding='utf-8'))!={'product':'QchipTEC','version':m['version']}:
        raise ValueError('暂存程序版本不匹配')
    args=plan['restart_args']
    if not isinstance(args,list) or len(args)!=6 or args[0]!='--device' or args[1] not in ('arroyo','simulation') or args[2]!='--port' or args[4]!='--data-dir':
        raise ValueError('更新重启参数无效')
    if (args[1]=='arroyo' and args[3]!='8765') or (args[1]=='simulation' and args[3] not in [str(n) for n in range(28774,28780)]):
        raise ValueError('更新重启端口无效')
    if not Path(args[5]).is_absolute():raise ValueError('更新重启配置路径无效')
    wait(plan['pids'],old)
    # Unique staging belongs to this update only; no recursive install-directory copy.
    backup=stage.parent/'backup';backup.mkdir(exist_ok=False)
    previous={};applied=[]
    for name in names:
        existing=target/name
        if existing.is_file():shutil.copy2(existing,backup/name);previous[name]=digest(existing)
        else:previous[name]=None
    journal=stage.parent/'update-result.json'
    def record(state):
        journal.write_text(json.dumps(dict(state=state,version=m['version'],files=names)),encoding='utf-8')
    record('replacing')
    try:
        for name in names:
            tmp=target/(name+'.qchip-update.tmp')
            if tmp.exists():raise ValueError('存在未处理的更新临时文件')
            try:
                shutil.copyfile(stage/name,tmp)
                replace(str(tmp),str(target/name));applied.append(name)
            finally:
                if tmp.exists():tmp.unlink()
        for f in m['files']:
            if digest(target/f['path'])!=f['sha256']:raise ValueError('替换后校验失败')
        record('complete')
        return True
    except Exception:
        for name in reversed(applied):
            dest=target/name
            if previous[name] is None:
                if dest.exists():dest.unlink()
            else:
                if digest(backup/name)!=previous[name]:raise RuntimeError('回退备份校验失败')
                temp=target/(name+'.qchip-rollback.tmp')
                if temp.exists():raise RuntimeError('存在未处理的回退临时文件')
                shutil.copyfile(backup/name,temp);os.replace(str(temp),str(dest))
        record('rolled_back')
        return False


def report_failure(message):
    if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,message,'Qchip TEC 更新',0x10)


def main(argv):
    if len(argv)!=1:raise ValueError('Update plan required')
    plan_path=regular_path(argv[0])
    if plan_path.stat().st_size>128*1024:raise ValueError('Update plan too large')
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    try:
        success=apply(plan)
        subprocess.Popen([str(Path(plan['target'])/'Qchip TEC.exe')]+plan['restart_args'],cwd=plan['target'],creationflags=subprocess.CREATE_NO_WINDOW)
        if not success:
            report_failure('更新失败，已恢复并重新启动原版本。回退备份已保留。')
        return 0 if success else 1
    except Exception:
        report_failure('更新未完成，原程序或回退备份已保留。请检查更新目录。')
        return 1
