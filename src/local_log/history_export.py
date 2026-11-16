"""Committed-only, whitelist-only read adapter; no device or controller calls."""
from contextlib import closing
from .log_store import FIELDS

PROTOCOL='qchip.tec.history.v1'
SAFE=9007199254740991


def state(store):
    if store.history_error:raise ValueError(store.history_error)
    with closing(store.read()) as db:
        row=db.execute('SELECT publisher_id,ready,next_cursor,fault FROM history_dataset WHERE singleton=1').fetchone()
    if row is None or row[3]:raise ValueError('HISTORY_INDEX_UNAVAILABLE')
    return dict(protocol=PROTOCOL,publisher_id=row[0],ready=bool(row[1]),tail=row[2]-1)


def export(store,after,cutoff_ms,limit):
    if (type(after) is not int or not 0<=after<=SAFE or type(cutoff_ms) is not int or
            not 0<=cutoff_ms<=SAFE or type(limit) is not int or not 1<=limit<=8192):
        raise ValueError('Invalid committed history range')
    if store.history_error:raise ValueError(store.history_error)
    with closing(store.read()) as db:
        db.execute('BEGIN')
        meta=db.execute('SELECT publisher_id,ready,next_cursor,fault FROM history_dataset WHERE singleton=1').fetchone()
        if not meta or not meta[1] or meta[3]:raise ValueError('HISTORY_INDEX_NOT_READY')
        tail=meta[2]-1
        if after>tail:raise ValueError('HISTORY_CURSOR_AHEAD_OF_LOCAL')
        first=db.execute('SELECT cursor,ts_ms FROM history_commits WHERE cursor>? ORDER BY cursor LIMIT 1',(after,)).fetchone()
        start=after
        if first is not None and first[1]<cutoff_ms:
            # Search the recent time index once, without scanning millions of old
            # records in Python or sending empty cloud batches for every chunk.
            recent=db.execute('SELECT MIN(cursor) FROM history_commits INDEXED BY history_time WHERE ts_ms>=? AND cursor>?',
                              (cutoff_ms,after)).fetchone()[0]
            start=tail if recent is None else recent-1
        columns=','.join('s.'+name for name in FIELDS)
        rows=db.execute('''SELECT h.cursor,'''+columns+''' FROM history_commits h
            JOIN samples s USING(session_id,seq,channel)
            WHERE h.cursor>? ORDER BY h.cursor LIMIT ?''',(start,limit)).fetchall()
        through=rows[-1][0] if rows else start
        values=[list(row) for row in rows if row[4]>=cutoff_ms]
    return dict(protocol=PROTOCOL,publisher_id=meta[0],after=after,through=through,tail=tail,rows=values)
