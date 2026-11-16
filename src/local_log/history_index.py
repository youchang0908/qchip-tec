"""Additive commit index; called only by LogStore's existing SQLite writer.

Legacy rowid is a ONE-TIME bootstrap source, never the published runtime cursor.
Reserve its entire region before new commits, then fill it in bounded chunks.
Explicit INTEGER PRIMARY KEY cursors survive VACUUM and source restarts.
"""
import secrets

CHUNK = 1024
MAX_SAFE = 9007199254740991


def initialize(db):
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS history_dataset (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), publisher_id TEXT NOT NULL,
            legacy_tail INTEGER NOT NULL, legacy_progress INTEGER NOT NULL,
            next_cursor INTEGER NOT NULL, ready INTEGER NOT NULL, fault TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS history_commits (
            cursor INTEGER PRIMARY KEY, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
            channel INTEGER NOT NULL, ts_ms INTEGER NOT NULL,
            UNIQUE(session_id,seq,channel),
            FOREIGN KEY(session_id,seq,channel) REFERENCES samples(session_id,seq,channel))''')
        db.execute('CREATE INDEX IF NOT EXISTS history_time ON history_commits(ts_ms,cursor)')
        row=db.execute('SELECT publisher_id,ready,fault FROM history_dataset WHERE singleton=1').fetchone()
        if row is None:
            tail=db.execute('SELECT COALESCE(MAX(rowid),0) FROM samples').fetchone()[0]
            db.execute('INSERT INTO history_dataset VALUES (1,?,?,0,?,?,NULL)',
                       (secrets.token_urlsafe(24),tail,tail+1,int(tail==0)))
        elif row[2]:
            raise RuntimeError('History index requires review: '+row[2])
        elif row[1]:
            counts=db.execute('SELECT (SELECT COUNT(*) FROM samples),(SELECT COUNT(*) FROM history_commits)').fetchone()
            missing=db.execute('''SELECT 1 FROM history_commits h LEFT JOIN samples s
                USING(session_id,seq,channel) WHERE s.session_id IS NULL LIMIT 1''').fetchone()
            if counts[0]!=counts[1] or missing:
                db.execute("UPDATE history_dataset SET fault='COMMIT_INDEX_MISMATCH' WHERE singleton=1")
                # Commit fault marker before raising, never rotate the identity.
                db.commit()
                raise RuntimeError('COMMIT_INDEX_MISMATCH')


def append(db, records):
    row=db.execute('SELECT next_cursor,fault FROM history_dataset WHERE singleton=1').fetchone()
    if row is None or row[1]:raise RuntimeError('History index unavailable')
    cursor=row[0]
    if cursor+len(records)>MAX_SAFE:raise RuntimeError('History cursor exhausted')
    db.executemany('INSERT INTO history_commits VALUES (?,?,?,?,?)',
        [(cursor+i,r['session_id'],r['seq'],r['channel'],r['ts_ms']) for i,r in enumerate(records)])
    db.execute('UPDATE history_dataset SET next_cursor=? WHERE singleton=1',(cursor+len(records),))


def bootstrap(db):
    row=db.execute('SELECT legacy_tail,legacy_progress,ready,fault FROM history_dataset WHERE singleton=1').fetchone()
    if row is None or row[2] or row[3]:return
    tail,progress=row[:2]
    rows=db.execute('''SELECT rowid,session_id,seq,channel,ts_ms FROM samples
        WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?''',(progress,tail,CHUNK)).fetchall()
    with db:
        db.executemany('INSERT INTO history_commits VALUES (?,?,?,?,?)',rows)
        progress=rows[-1][0] if rows else tail
        done=progress==tail
        if done:
            a,b=db.execute('SELECT (SELECT COUNT(*) FROM samples),(SELECT COUNT(*) FROM history_commits)').fetchone()
            if a!=b:raise RuntimeError('Legacy history backfill mismatch')
        db.execute('UPDATE history_dataset SET legacy_progress=?,ready=? WHERE singleton=1',(progress,int(done)))


def fault(db):
    # Keep measurements committed even when the auxiliary history index fails.
    # A durable fault prevents exporting over a hole on any later restart.
    try:
        db.execute("UPDATE history_dataset SET fault='HISTORY_INDEX_FAILED' WHERE singleton=1")
    except Exception:
        pass
