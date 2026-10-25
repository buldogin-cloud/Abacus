"""Read *provider-returned* usage from known response shapes.

No prompt text, tokens' contents, or user payload are retained.
Do not infer usage from estimated word counts.
"""
from __future__ import annotations

def extract_usage(provider, response):
    """Return (input_tokens, output_tokens) or (None, None).

    Accept dict responses only. Unknown or malformed schemas remain unknown.
    """
    if not isinstance(response, dict):
        return None, None
    u = response.get("usage")
    if not isinstance(u, dict):
        return None, None
    name = (provider or "").lower()
    if name == "openai":
        a, b = u.get("prompt_tokens"), u.get("completion_tokens")
        if a is None or b is None:
            a, b = u.get("input_tokens"), u.get("output_tokens")
    elif name == "anthropic":
        a, b = u.get("input_tokens"), u.get("output_tokens")
    else:
        return None, None
    if type(a) is not int or type(b) is not int or a < 0 or b < 0:
        return None, None
    return a, b
