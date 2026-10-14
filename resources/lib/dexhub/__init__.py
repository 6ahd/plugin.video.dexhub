# v5.10.235: the package no longer imports .api (and with it the HTTP client,
# concurrent.futures, logging and urllib) when only a light submodule such as
# .store or .common is wanted: the skin's Add-ons row paid a second for it at
# every Kodi start. The old package-level names still resolve, on first use.
_API = ('add_provider_from_url', 'list_providers', 'get_provider', 'remove_provider',
        'search_catalogs', 'fetch_provider_catalog', 'get_best_meta', 'get_stream_results')


def __getattr__(name):
    if name in _API:
        from . import api
        return getattr(api, name)
    raise AttributeError(name)
