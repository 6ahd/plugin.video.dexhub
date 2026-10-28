"""Use the subtitle's own headers, scoped to its HTTP origin."""
import re
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

_TOKEN=re.compile(r'^[!#$%&\'*+.^_`|~0-9A-Za-z-]+$')
_DROP={'host','content-length','connection','transfer-encoding'}
_PUBLIC={'user-agent','accept','accept-language','accept-encoding'}

def headers(*sources):
    result={}
    for source in sources:
        if not isinstance(source,dict):continue
        for key,value in source.items():
            key=str(key);value=str(value)
            if not _TOKEN.fullmatch(key) or key.lower() in _DROP or '\r' in value or '\n' in value or len(value)>8192:continue
            for old in list(result):
                if old.lower()==key.lower():result.pop(old)
            result[key]=value
    return result

def origin(url):
    p=urlsplit(url)
    return (p.scheme.lower(),(p.hostname or '').lower(),p.port or (443 if p.scheme.lower()=='https' else 80))

class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,hdrs,newurl):
        if urlsplit(newurl).scheme.lower() not in ('http','https'):return None
        next_req=super().redirect_request(req,fp,code,msg,hdrs,newurl)
        if next_req is not None and origin(req.full_url)!=origin(newurl):
            for name in list(next_req.headers):
                if name.lower() not in _PUBLIC:next_req.remove_header(name)
        return next_req

def open_response(url,request_headers,timeout):
    return build_opener(Redirects()).open(Request(url,headers=headers(request_headers)),timeout=timeout)
