"""Stremio Descriptor contract: preserve remote entries; append valid HTTP add-ons."""
import copy
import re
from urllib.parse import urlsplit, urlunsplit


def transport(value):
    value = str(value or '').strip()
    if value.startswith('stremio://'):
        value = 'https://' + value[10:]
    try:
        u = urlsplit(value)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            return ''
        path = u.path.rstrip('/')
        if not path.endswith('/manifest.json'):
            path += '/manifest.json'
        return urlunsplit((u.scheme.lower(), u.netloc, path, u.query, ''))
    except ValueError:
        return ''


def key(value):
    value = transport(value)
    if not value:
        return ''
    u = urlsplit(value)
    # Configuration paths and query tokens are case sensitive.
    return urlunsplit((u.scheme, u.netloc.lower(), u.path, u.query, ''))


def valid_manifest(m):
    if not isinstance(m, dict) or not all(isinstance(m.get(k), str) and m[k] for k in ('id','name','version')):
        return False
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', m['version']):
        return False
    if not isinstance(m.get('types'), list) or not all(isinstance(v, str) for v in m['types']):
        return False
    resources = m.get('resources')
    return isinstance(resources, list) and all(isinstance(v, str) or
        isinstance(v, dict) and isinstance(v.get('name'), str) for v in resources)


def validate(rows):
    return isinstance(rows, list) and all(isinstance(r, dict) and transport(r.get('transportUrl'))
        and valid_manifest(r.get('manifest')) and isinstance(r.get('flags', {}), dict)
        and all(type(v) is bool for k,v in r.get('flags', {}).items() if k in ('official','protected')) for r in rows)


def merge(remote, providers, fetch):
    if not validate(remote):
        raise RuntimeError('Invalid Stremio add-on snapshot; account list retained')
    out = copy.deepcopy(remote)
    have = {key(r['transportUrl']) for r in remote}
    failed = []
    for row in providers or []:
        url = transport(row.get('manifest_url') or row.get('transportUrl') or row.get('url'))
        if not url or key(url) in have:
            continue
        if urlsplit(url).hostname in ('localhost', '127.0.0.1', '::1'):
            continue
        manifest = row.get('manifest')
        if not valid_manifest(manifest):
            manifest = fetch(url)
        if not valid_manifest(manifest):
            failed.append(url)
            continue
        out.append({'transportUrl':url,'manifest':copy.deepcopy(manifest),
                    'flags':{'official':False,'protected':False}})
        have.add(key(url))
    return out, failed
