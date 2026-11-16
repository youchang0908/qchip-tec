import hashlib,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from updates import Updates,ARCHIVE,extract_verified,github_url,validate_manifest
from update_helper import apply

def sha(b):return hashlib.sha256(b).hexdigest()
def fixture(root):
    files={'Qchip TEC.exe':b'fake exe version1.0.2','version.json':json.dumps({'product':'QchipTEC','version':'1.0.2'}).encode()}
    z=root/ARCHIVE
    with zipfile.ZipFile(z,'w') as f:
        for name,data in files.items():f.writestr(name,data)
    m=dict(schema=1,product='QchipTEC',version='1.0.2',platform='windows-x64',
           archive=dict(name=ARCHIVE,bytes=z.stat().st_size,sha256=sha(z.read_bytes())),
           files=[dict(path=n,bytes=len(b),sha256=sha(b)) for n,b in files.items()])
    return z,m

class UpdateTests(unittest.TestCase):
    def test_invalid_download_does_not_enter_busy(self):
        u=Updates('unused')
        with self.assertRaises(ValueError):u.launch('download')
        self.assertEqual(u.snapshot()['state'],'idle')
    def test_rolled_back_helper_reports_failure_but_restarts_old_app(self):
        from unittest.mock import patch
        import update_helper
        with tempfile.TemporaryDirectory() as tmp:
            plan=Path(tmp).resolve()/'plan.json';plan.write_text(json.dumps({'target':tmp,'restart_args':[]}))
            with patch('update_helper.apply',return_value=False),patch.object(update_helper.subprocess,'CREATE_NO_WINDOW',0,create=True),patch('update_helper.subprocess.Popen') as restart,patch('update_helper.report_failure') as notice:
                self.assertEqual(update_helper.main([str(plan)]),1)
                restart.assert_called_once();notice.assert_called_once()
    def test_unpublished_and_building_never_use_source_zip(self):
        def missing(*args):raise HTTPError(args[0],404,'fixture',{},None)
        u=Updates('unused',transport=missing);u.check();self.assertEqual(u.snapshot()['state'],'unpublished')
        u=Updates('unused',transport=lambda *a:json.dumps({'tag_name':'v1.0.2','zipball_url':'https://github.com/source.zip','assets':[]}).encode())
        u.check();self.assertEqual(u.snapshot()['state'],'building');self.assertIsNone(u.ready)
    def test_download_verified_without_real_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp).resolve();z,m=fixture(r);repo='youchang0908/qchip-tec'
            release={'tag_name':'v1.0.2','assets':[{'name':n,'state':'uploaded','size':z.stat().st_size,'browser_download_url':f'https://github.com/{repo}/releases/download/v1.0.2/{n}'} for n in ('update.json',ARCHIVE)]}
            def fetch(url,limit,destination=None):
                if url.endswith('/latest'):return json.dumps(release).encode()
                if url.endswith('update.json'):return json.dumps(m).encode()
                destination.write_bytes(z.read_bytes());return z.stat().st_size,sha(z.read_bytes())
            u=Updates(r/'cache',transport=fetch);u.check();u.download()
            self.assertEqual(u.snapshot()['state'],'ready');self.assertFalse(u.snapshot()['can_install'])
            self.assertEqual((Path(u.ready['directory'])/'Qchip TEC.exe').read_bytes(),b'fake exe version1.0.2')
    def test_strict_urls_paths_hash_and_zip(self):
        for url in ('http://github.com/a','https://evil.example/a','https://github.com@evil.example/a','https://github.com:444/a'):
            with self.assertRaises(ValueError):github_url(url)
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp).resolve();z,m=fixture(r)
            bad=json.loads(json.dumps(m));bad['files'][0]['path']='../data/settings.json'
            with self.assertRaises(ValueError):validate_manifest(bad,'1.0.2')
            bad=json.loads(json.dumps(m));bad['files'][0]['sha256']='0'*64
            with self.assertRaises(ValueError):extract_verified(z,r/'wrong-hash',bad)
            with zipfile.ZipFile(z,'a') as f:f.writestr('logs/private.csv','do not import')
            with self.assertRaises(ValueError):extract_verified(z,r/'wrong-files',m)
    def test_in_place_and_failure_rollback_preserve_user_files(self):
        for fail in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                r=Path(tmp).resolve();target=r/'installed';target.mkdir();(target/'Qchip TEC.exe').write_bytes(b'old exe')
                (target/'version.json').write_text('old metadata')
                saved={}
                for name in ('logs/history.csv','data/history.sqlite3','private/pairing.json','user-note.txt'):
                    p=target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original '+name);saved[name]=p.read_bytes()
                cache=r/'cache';cache.mkdir();z,m=fixture(cache);extract_verified(z,cache/'program',m)
                plan=dict(schema=1,target=str(target),staged=str(cache/'program'),manifest=m,
                          old_sha256=sha(b'old exe'),pids=[],restart_args=['--device','simulation','--port','28774','--data-dir',str(r/'profile')])
                import os
                def replace(a,b):
                    if fail and Path(b).name=='version.json':raise PermissionError('locked fixture')
                    os.replace(a,b)
                result=apply(plan,wait=lambda *a:None,replace=replace)
                self.assertEqual(result,not fail)
                self.assertEqual((target/'Qchip TEC.exe').read_bytes(),b'old exe' if fail else b'fake exe version1.0.2')
                for n,b in saved.items():self.assertEqual((target/n).read_bytes(),b)

if __name__=='__main__':unittest.main()
