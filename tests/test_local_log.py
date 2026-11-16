import csv
from contextlib import closing
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from local_log.host import LocalLog
from local_log.log_interval import IntervalSelector
from local_log.log_store import FIELDS,LogStore,utc
from local_log.csv_mirror import CSV_FIELDS,META
from product_model import ProductModel
from hardware_control.http import make_server


def wait(predicate):
    until=time.monotonic()+3
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Fake fixture deadline')


def row(seq=1,ch=1,stamp=1793420000123,segment=0,quality='ok'):
    return dict(session_id='fixture',seq=seq,channel=ch,ts_ms=stamp,utc=utc(stamp),temperature_c=20.,
        resistance_kohm=12.,current_a=-.2,voltage_v=-.7,power_w=.14,tset_c=21.,output=0,mode='T',
        metadata_utc=utc(stamp-500),quality=quality,segment=segment,source='simulation')


def mirrored_rows(directory):
    result=[]
    for path in Path(directory).glob('*.csv'):
        with path.open(encoding='utf-8-sig',newline='') as file:
            reader=csv.reader(file)
            assert [next(reader) for _ in META]==[list(item) for item in META]
            assert next(reader)==list(CSV_FIELDS)
            result.extend(dict(zip(CSV_FIELDS,r)) for r in reader)
    return result


class LocalLogTests(unittest.TestCase):
    def test_actual_queue_overflow_drains_accepted_and_sql_failure_is_not_durable(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LogStore(Path(folder)/'queue.sqlite3',capacity=1,batch_size=1)
            blocker=sqlite3.connect(str(store.path));blocker.execute('BEGIN IMMEDIATE')
            try:
                store.submit(row(1));wait(lambda:store.queue.empty())
                store.submit(row(2))
                with self.assertRaises(RuntimeError):store.submit(row(3))
                self.assertIn('QUEUE_FULL',store.status()['error'])
            finally:blocker.rollback();blocker.close();store.close()
            self.assertEqual((store.accepted,store.committed,store.rejected),(2,2,1))
            broken=LogStore(Path(folder)/'broken.sqlite3')
            try:
                with closing(sqlite3.connect(str(broken.path))) as connection:
                    connection.execute("CREATE TRIGGER fixture_fail BEFORE INSERT ON samples BEGIN SELECT RAISE(ABORT,'fake disk failure'); END")
                    connection.commit()
                broken.submit(row(1));wait(lambda:broken.error)
                self.assertEqual((broken.status()['accepted'],broken.status()['committed'],broken.status()['pending']),(1,0,1))
                self.assertEqual(broken.bounds()['records'],0)
            finally:broken.close()

    def test_real_adapter_one_four_sparse_pause_and_query_equivalence(self):
        for count in (1,4):
            query_lists=[]
            for enabled in (False,True):
                with tempfile.TemporaryDirectory() as folder:
                    model=ProductModel(recording_dir=folder,simulation=True,sim_channels=count)
                    model.configure(dict(model.settings,log_enabled=enabled))
                    try:
                        model._connect()
                        self.assertTrue(all(type(c['read_complete_monotonic_ns']) is int for c in model.channels))
                        if count==4:
                            model.configure(dict(model.settings,overclock_enabled=True,overclock_channel=2,overclock_target=10))
                        for _ in range(3):model._accept(model.driver.read_channel(2 if count==4 else 1))
                        query_lists.append(list(model.driver.fake.queries))
                        if enabled:
                            model.daily_log.store.flush();model.daily_log.mirror.flush()
                            stored=model.daily_log.store.query(0,253402300799999,2 if count==4 else 1)['rows']
                            valid=[r for r in stored if r['quality']=='ok']
                            self.assertEqual(len(valid),4)
                            self.assertEqual(len(mirrored_rows(model.daily_log.directory)),count+3+(3 if count==4 else 0))
                            first=valid[0]
                            self.assertEqual(first['power_w'],abs(first['current_a']*first['voltage_v']))
                            self.assertEqual(first['source'],'simulation')
                            if count==4:
                                other=model.daily_log.store.query(0,253402300799999,1)['rows']
                                self.assertEqual([r['quality'] for r in other],['ok','paused'])
                        self.assertTrue(model.connected);self.assertEqual(model.driver.parameter_write_attempts,0)
                    finally:model.close_recording();model._disconnect()
            self.assertEqual(query_lists[0],query_lists[1])

    def test_interval_precision_clock_independence_persist_and_failure(self):
        for seconds in (0,1,2,5):
            gate=IntervalSelector(seconds)
            points=[]
            for n in range(101):
                if gate.select(row(n+1,stamp=1793420000123-n*7000),n*100000000):points.append(n)
            self.assertEqual(points,list(range(101)) if seconds==0 else list(range(0,101,seconds*10)))
            gate.configure(seconds)
            self.assertEqual(gate.select(row(102),10100000000),seconds==0)
            missing=dict(row(200,quality='paused'),temperature_c=None,resistance_kohm=None,current_a=None,voltage_v=None,power_w=None)
            self.assertTrue(gate.select(missing,10100000001))
            self.assertTrue(gate.select(row(103),10100000000+seconds*1000000000))
            self.assertTrue(gate.select(row(104,ch=2),1))
            self.assertTrue(gate.select(dict(row(105),session_id='another-session'),1))
        with tempfile.TemporaryDirectory() as folder:
            log=LocalLog(Path(folder)/'db',Path(folder)/'csv','raw','simulation')
            self.assertEqual(log.configure_interval(2)['interval_seconds'],2)
            with patch('local_log.host.save_interval',side_effect=OSError('FIXTURE_WRITE_FAILURE')):
                with self.assertRaises(OSError):log.configure_interval(5)
            self.assertEqual(log.selector.seconds,2)
            log.close()
            second=LocalLog(Path(folder)/'db',Path(folder)/'csv','raw','simulation')
            self.assertEqual(second.selector.seconds,2);second.close()

    def test_sql_queue_csv_faults_do_not_stop_sampling_or_fake_control(self):
        for failure in ('sqlite','queue','csv'):
            with tempfile.TemporaryDirectory() as folder:
                model=ProductModel(recording_dir=folder,simulation=True,sim_channels=1)
                model.configure(dict(model.settings,log_enabled=True))
                worker=threading.Thread(target=model.run);worker.start()
                model.set_connection(True);wait(lambda:model.connected and model.seq>=3)
                try:
                    if failure=='sqlite':model.daily_log.store._fail(sqlite3.OperationalError('FIXTURE_SQL_FAILURE'))
                    elif failure=='csv':model.daily_log.mirror.error='FIXTURE_CSV_FAILURE'
                    else:model.daily_log.store.error='QUEUE_FULL: FIXTURE_ADMISSION_FAILURE'
                    initial=model.seq
                    wait(lambda:model.seq>=initial+4)
                    self.assertTrue(model.connected)
                    self.assertFalse(model.daily_log.snapshot()['active'])
                    self.assertIsNotNone(model.daily_log.snapshot()['error'])
                    self.assertEqual(model.driver.parameter_write_attempts,0)
                    # Same production executor remains available; only an isolated fake is written.
                    result=model.command(dict(id='fake-after-log-fault',channel=1,type='setpoint',params=dict(mode='T',value=20.01),expires_at=time.time()+5))
                    self.assertEqual(result['status'],'confirmed')
                finally:
                    model.stop.set();worker.join(5)
                    self.assertFalse(worker.is_alive())

    def test_native_store_old_files_preserved_and_mirror_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);dbroot=folder/'db';dbroot.mkdir()
            path=dbroot/'tec-local-log-v1.sqlite3'
            with closing(sqlite3.connect(str(path))) as connection:
                connection.execute('CREATE TABLE old_data(value)');connection.commit()
            original=path.read_bytes()
            broken=LocalLog(dbroot,folder/'csv','raw','simulation')
            self.assertFalse(broken.configure(True));self.assertEqual(path.read_bytes(),original)
            broken.close()
            other=LocalLog(folder/'otherdb',folder/'csv','raw','simulation');other.configure(True)
            other.begin_session('new-session',[1])
            stamp=time.time();oldfile=folder/'csv'/('raw_'+datetime.fromtimestamp(stamp,timezone(timedelta(hours=8))).strftime('%Y%m%d')+'.csv')
            oldfile.parent.mkdir(exist_ok=True)
            oldfile.write_bytes(b'CH1_time,CH1_T_C\n12:00:00,20\n')
            content=oldfile.read_bytes()
            reading=dict(id=1,sampled_at=stamp,read_complete_monotonic_ns=1,temperature=20)
            other.observe(reading)
            wait(lambda:other.mirror.error)
            self.assertFalse(other.snapshot()['active']);self.assertEqual(oldfile.read_bytes(),content)
            other.close()
            # Remove only this test fixture, then explicitly recover committed rows.
            oldfile.unlink()
            recovered=LocalLog(folder/'otherdb',folder/'csv','raw','simulation');recovered.open()
            recovered.mirror.flush()
            self.assertEqual(len(mirrored_rows(folder/'csv')),1)
            recovered.close()
            again=LocalLog(folder/'otherdb',folder/'csv','raw','simulation');again.open();again.mirror.flush()
            self.assertEqual(len(mirrored_rows(folder/'csv')),1);again.close()

    def test_pagination_extrema_and_explicit_segments_not_two_seconds(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LogStore(Path(folder)/'log.sqlite3')
            try:
                for i in range(1,31):store.submit(dict(row(i,stamp=1793420000000+i*5000,segment=0 if i<20 else 1),temperature_c=99 if i==17 else float(i)))
                store.flush()
                table=store.query(1793420000000,1793421000000,1,page=1,page_size=5)
                self.assertEqual([r['seq'] for r in table['rows']],[6,7,8,9,10])
                series=store.series(1793420000000,1793421000000,[1],'temperature_c',10)
                paths=series['series']['1']
                self.assertEqual(len(paths),2)
                self.assertIn(99,[r['temperature_c'] for path in paths for r in path])
                self.assertTrue(all(len({r['segment'] for r in path})==1 for path in paths))
            finally:store.close()

    def test_native_http_token_interval_export_and_path_separation(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(recording_dir=folder,settings_path=Path(folder)/'settings.json',simulation=True,sim_channels=1)
            model._connect()
            server=make_server(0,sim=model,data_dir=folder,device='simulation')
            worker=threading.Thread(target=server.serve_forever);worker.start()
            base='http://127.0.0.1:'+str(server.server_port)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def get(path,token=True,body=None):
                headers={'Origin':base,'Content-Type':'application/json'}
                if token:headers['X-Qchip-Token']=server.token
                req=urllib.request.Request(base+'/api/local-log/'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
                with opener.open(req,timeout=3) as response:return response.read()
            try:
                with self.assertRaises(urllib.error.HTTPError):get('state',False)
                state=json.loads(get('state'));self.assertEqual(state['channel_ids'],[1])
                self.assertIn('database_path',state['storage'])
                self.assertEqual(json.loads(get('interval',body={'interval_seconds':5}))['interval_seconds'],5)
                with self.assertRaises(urllib.error.HTTPError):get('interval',body={'interval_seconds':True})
                get('enabled',body={'enabled':True});model._accept(model.driver.read_channel(1));model.daily_log.store.flush()
                start,end=int(time.time()*1000)-10000,int(time.time()*1000)+1000
                raw=get('export.csv?start={}&end={}&channel=1'.format(start,end))
                self.assertIn(b'session_id,seq,channel,ts_ms,utc',raw)
                get('enabled',body={'enabled':False})
                changed=json.loads(get('storage',body={'directory':str(Path(folder)/'newcsv'),'prefix':'new_raw'}))
                self.assertEqual(changed['storage']['prefix'],'new_raw')
            finally:
                server.shutdown();worker.join(4);server.server_close();model.close_recording();model._disconnect()
