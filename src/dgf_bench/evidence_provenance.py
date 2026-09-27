"""Lexical JSON provenance, insensitive only to whitespace outside strings.

This proves that a complete relevant field/value was observed, not that every
premise in a finding has been demonstrated. It is not semantic entailment.
"""
import json
import re

_TOKEN = re.compile(r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
                    r'|true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
                    r'|[{}\[\],:]')


def json_tokens(text):
    """Tokenize a JSON excerpt; reject ellipses, broken strings and other prose."""
    tokens = []
    end = 0
    for match in _TOKEN.finditer(text):
        if text[end:match.start()].strip():
            return None
        token=match.group()
        # JSON escapes are an encoding, not different evidence. Preserve the
        # decoded string exactly, including spaces/newlines inside its value.
        if token.startswith('"'):
            token=json.dumps(json.loads(token),ensure_ascii=False)
        tokens.append(token)
        end = match.end()
    return tokens if not text[end:].strip() else None


def supports_observed_fields(content, quote, fields):
    if not isinstance(quote, str) or not quote.strip() or not fields:
        return False
    source = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    observed, excerpt = json_tokens(source), json_tokens(quote)
    if not observed or not excerpt:
        return False
    # Match tokens rather than substrings: 10 cannot match 100, and whitespace
    # inside a quoted value remains significant. No ellipses or missing values.
    width = len(excerpt)
    if not any(observed[i:i + width] == excerpt for i in range(len(observed) - width + 1)):
        return False
    for i in range(len(excerpt) - 2):
        key, colon, value = excerpt[i:i + 3]
        if colon != ':' or not key.startswith('"') or json.loads(key) not in fields:
            continue
        if value not in '{}[],:':
            return True  # A complete scalar token, including quoted text/null.
    return False
