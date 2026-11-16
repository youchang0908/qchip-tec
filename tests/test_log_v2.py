import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from daily_log import DailyLog, columns, read_header
from product_model import ProductModel

TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
STAMP = datetime(2026, 10, 29, 12, 34, 56, 123000, tzinfo=TZ).timestamp()


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.reader(file)
        meta, header = read_header(reader)
        return meta, header, list(reader)


def point(ch=1, stamp=STAMP, **extra):
    return dict(dict(id=ch, sampled_at=stamp, temperature=19.125, current=-.25,
                     voltage=.4, resistance=12.345, output=False, mode='R',
                     setpoints={'T': 20, 'R': 12, 'ITE': -.1}, pid=[1, .2, .3], metadata_at=stamp-1), **extra)


class LogV2Tests(unittest.TestCase):
    def test_units_sparse_missing_and_creation_whitelist(self):
        for ids in ([1], [1, 2, 3, 4]):
            with tempfile.TemporaryDirectory() as folder:
                calls=[]
                def context():
                    calls.append(1)
                    return dict(device_name='Fake lab', computer_name='FAKE-PC', source='simulation',
                                device=dict(model='Arroyo fake', port='COM_FAKE', baud_rate=38400, token='SECRET'),
                                software={'desktop': '0.11.2'}, channels=[point(ids[-1])],
                                sampling=dict(mode='normal', min_sleep_ms=1, target={'kind': 'unlimited'}, secret='SECRET'),
                                token='SECRET', recording_dir='PRIVATE_PATH')
                log=DailyLog(lambda t: datetime.fromtimestamp(t, TZ), lambda: datetime.fromtimestamp(STAMP+5, TZ))
                log.configure(True)
                self.assertTrue(log.append(folder,'fake',ids,point(ids[-1]),context))
                self.assertTrue(log.append(folder,'fake',ids,point(ids[-1],current=None,output=1,voltage=float('nan'),resistance=None,setpoints={}),context))
                log.close()
                meta, header, rows=read(next(Path(folder).glob('*.csv')))
                self.assertEqual(header,columns(ids));self.assertEqual(len(header),8*len(ids))
                index=8*(len(ids)-1)
                self.assertEqual(rows[0][:index],['']*index)
                self.assertEqual(rows[0][index:],['12:34:56.123','19.125','20','-0.25','0.4','0.1','12.345','0'])
                self.assertEqual(rows[1][index:],['12:34:56.123','19.125','','','','','',''])
                self.assertEqual(len(calls),1)
                self.assertEqual(meta['channel_parameters_at_creation'][0]['mode'],'R')
                self.assertEqual(meta['channel_parameters_at_creation'][0]['setpoints']['T'],20)
                self.assertNotIn('limits',meta['channel_parameters_at_creation'][0])
                self.assertEqual(meta['metadata_refresh_seconds'],2)
                self.assertEqual(meta['point_time'],'read_complete')
                self.assertEqual(meta['created_at'],'2026-10-29T12:35:01.123+08:00')
                self.assertNotIn('SECRET',json.dumps(meta));self.assertNotIn('PRIVATE_PATH',json.dumps(meta))

    def test_restart_appends_without_duplicate_header_or_changed_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            for offset in (0,1):
                log=DailyLog(lambda t:datetime.fromtimestamp(t,TZ));log.configure(True)
                self.assertTrue(log.append(folder,'trace',[1],point(stamp=STAMP+offset),{'device_name':str(offset)}))
                log.close()
            meta,header,rows=read(next(Path(folder).glob('*.csv')))
            self.assertEqual(meta['device_name'],'0');self.assertEqual(len(rows),2)
            self.assertEqual(rows[1][0],'12:34:57.123')

    def test_old_empty_truncated_and_offset_mismatch_are_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'trace_20261029.csv'
            for content in (b'',b'CH1_time,CH1_temperature_C\n12:00:00,20\n',b'time,temperature,current\n',b'#schema,broken\n'):
                path.write_bytes(content)
                log=DailyLog(lambda t:datetime.fromtimestamp(t,TZ));log.configure(True)
                self.assertFalse(log.append(folder,'trace',[1],point()))
                self.assertFalse(log.enabled);self.assertIn('前缀',log.error)
                self.assertEqual(path.read_bytes(),content)
            path.unlink()  # Fake fixture only.
            log=DailyLog(lambda t:datetime.fromtimestamp(t,TZ));log.configure(True)
            self.assertTrue(log.append(folder,'trace',[1],point()));log.close()
            valid=path.read_bytes()
            for content,zone in ((valid[:-1],TZ),(valid,timezone(timedelta(hours=9)))):
                path.write_bytes(content);log=DailyLog(lambda t:datetime.fromtimestamp(t,zone));log.configure(True)
                self.assertFalse(log.append(folder,'trace',[1],point()))
                self.assertEqual(path.read_bytes(),content)
                self.assertIn('前缀',log.error)

    def test_fake_paused_and_no_additional_queries(self):
        query_lists=[]
        for enabled in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                model=ProductModel(recording_dir=folder,simulation=True)
                model.configure(dict(model.settings,log_enabled=enabled))
                try:
                    model._connect()
                    model.configure(dict(model.settings,overclock_enabled=True,overclock_channel=2,overclock_target=10))
                    for _ in range(3):
                        model._accept(model.driver.read_channel(2))
                    self.assertEqual(model.driver.parameter_write_attempts,0)
                    query_lists.append(list(model.driver.fake.queries))
                    if enabled:
                        model.daily_log.store.flush();model.daily_log.mirror.flush()
                        from contextlib import closing
                        with closing(model.daily_log.store.read()) as connection:
                            rows=[dict(r) for r in connection.execute('SELECT * FROM samples ORDER BY seq')]
                        model.daily_log.close()
                        self.assertEqual(len(rows),10)
                        self.assertEqual(model.seq,7)
                        self.assertTrue(all(row['channel']==2 and row['quality']=='ok' for row in rows[-3:]))
                        self.assertEqual(sum(r['quality']=='paused' for r in rows),3)
                finally:
                    model.close_recording();model._disconnect()
        self.assertEqual(query_lists[0],query_lists[1])
