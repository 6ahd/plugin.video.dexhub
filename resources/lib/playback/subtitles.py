"""Subtitle policy and runtime facade."""
from .. import subtitle_policy as policy
from .. import subtitle_logic as logic

is_ai_subtitle = policy.is_ai_subtitle
allow_automatic = policy.allow_automatic
automatic_rows = policy.automatic_rows

def __getattr__(name):
    if hasattr(policy, name): return getattr(policy, name)
    return getattr(logic, name)
