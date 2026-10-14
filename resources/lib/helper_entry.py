"""Import-light Helper entry. Runtime adapters must be supplied explicitly.

This module never imports the compatibility router, even as a fallback. The
legacy entry can supply its API during migration; a standalone runtime can use
the same entry without changing parameter handling or resume behavior.
"""

NOT_HANDLED = object()
PARAMETERS = (
    'video_type', 'tmdb_id', 'imdb_id', 'tvdb_id', 'title', 'year', 'showname',
    'season', 'episode', 'ar_title', 'ar_showname', 'poster', 'fanart',
    'resume_seconds', 'resume_percent', 'resume_duration', 'resume_updated_at',
    # v5.10.78: per-call override of the play-mode setting. Validated in
    # helper_route.resolve_play_mode; anything unrecognised means "use the
    # setting", so an old or hand-typed URL behaves exactly as before.
    'play_mode',
)


def dispatch(params, runtime):
    if params.get('action') != 'tmdb_player':
        return NOT_HANDLED
    # Only known player arguments cross the boundary. No URL-supplied attribute
    # names, imports or action evaluation. Empty strings preserve old defaults.
    arguments = {key: params.get(key, 'movie' if key == 'video_type' else '')
                 for key in PARAMETERS}
    from .helper_route import tmdb_player
    return tmdb_player(runtime, **arguments)
