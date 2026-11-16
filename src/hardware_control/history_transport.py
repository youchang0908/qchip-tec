"""Verified TLS, no redirects, bounded history-v1 HTTP; no device imports."""
import gzip
import json
import re
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from . import VERSION

PROTOCOL='qchip.tec.history.v1'
MAX_ROWS=8192
MAX_JSON=2000000
MAX_GZIP=512000
SAFE=9007199254740991
PUBLISHER=re.compile(r'^[A-Za-z0-9_-]{16,80}$')
FIELDS=('session_id','seq','channel','ts_ms','utc','temperature_c','resistance_kohm',
        'current_a','voltage_v','power_w','tset_c','output','mode','metadata_utc','quality','segment','source')


def safe_int(value,minimum=0):return type(value) is int and minimum<=value<=SAFE


def pack_batch(publisher,after,through,channel_ids,rows):
    if (not isinstance(publisher,str) or not PUBLISHER.fullmatch(publisher) or
            not safe_int(after) or not safe_int(through,after+1) or
            channel_ids not in ([1],[1,2,3,4]) or any(type(ch) is not int for ch in channel_ids) or len(rows)>MAX_ROWS):
        raise ValueError('Invalid history envelope')
    previous=after
    for row in rows:
        if (not isinstance(row,list) or len(row)!=18 or not safe_int(row[0],previous+1) or row[0]>through or
                not safe_int(row[2],1) or not safe_int(row[3],1) or row[3]>4 or
                not safe_int(row[4]) or not safe_int(row[16])):raise ValueError('Invalid immutable history row')
        previous=row[0]
    batch=dict(protocol=PROTOCOL,publisher_id=publisher,after=after,through=through,channel_ids=channel_ids,rows=rows)
    raw=json.dumps(batch,allow_nan=False,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    if len(raw)>MAX_JSON:raise OverflowError('History JSON cap')
    compressed=gzip.compress(raw,compresslevel=1,mtime=0)
    if len(compressed)>MAX_GZIP:raise OverflowError('History gzip cap')
    return batch,compressed


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class HistoryClient:
    def __init__(self,bridge_url,token,session,boot,publisher):
        url=urlsplit(bridge_url)
        if (url.scheme!='wss' or not url.hostname or url.username or url.password or url.fragment or
                not url.path.endswith('/bridge') or not PUBLISHER.fullmatch(publisher)):
            raise ValueError('Expected existing authenticated bridge URL')
        self.base=url._replace(scheme='https',path=url.path[:-7]+'/history/')
        self.publisher=publisher
        self.headers={'Authorization':'Bearer '+token,'X-TEC-Bridge-Session':session,'X-TEC-Source-Boot':boot,
                      'User-Agent':'QchipTEC-History/'+VERSION.split('-')[0]}
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def _url(self,action,**params):
        query=parse_qsl(self.base.query,keep_blank_values=True)+list(params.items())
        return urlunsplit(self.base._replace(path=self.base.path+action,query=urlencode(query)))

    def _request(self,action,data=None,**params):
        headers=dict(self.headers)
        if data is not None:headers.update({'Content-Type':'application/json','Content-Encoding':'gzip'})
        request=Request(self._url(action,**params),data=data,headers=headers,method='POST' if data is not None else 'GET')
        # Python's HTTPSHandler validates hostname/certificate with system trust.
        with self.opener.open(request,timeout=5) as response:
            raw=response.read(8193)
            if len(raw)>8192 or response.status!=200:raise ValueError('Invalid history response size/status')
        result=json.loads(raw)
        if (not isinstance(result,dict) or result.get('protocol')!=PROTOCOL or result.get('publisher_id')!=self.publisher or
                not safe_int(result.get('through')) or not safe_int(result.get('server_ms')) or result.get('retention_hours')!=72):
            raise ValueError('Invalid history acknowledgement')
        return result

    def cursor(self):return self._request('cursor',publisher_id=self.publisher)

    def send(self,batch,compressed):
        if batch['publisher_id']!=self.publisher:raise ValueError('Wrong history dataset')
        result=self._request('batch',compressed)
        if result['through']!=batch['through'] or type(result.get('duplicate')) is not bool:
            raise ValueError('History ACK does not match retained batch')
        return result
