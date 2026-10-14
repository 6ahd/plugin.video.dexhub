"""Small Kodi RPC boundary; importing this module never loads the router."""


def call(method, params=None):
    try:
        import json
        import xbmc
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
        if params is not None:
            payload['params'] = params
        raw = xbmc.executeJSONRPC(json.dumps(payload, separators=(',', ':')))
        data = json.loads(raw or '{}')
        return data.get('result') if isinstance(data, dict) else None
    except Exception:
        return None
