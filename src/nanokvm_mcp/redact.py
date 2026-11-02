"""Deterministic, reversible redaction of screen text before it leaves this laptop.

Each secret-looking value becomes a placeholder such as [[EMAIL_1]]; the same value always gets the same
placeholder, and sharing the mapping keeps them stable across passes. Placeholders are put back only
locally (restore), in Spark's reply or in text typed onto the locked computer. Patterns are deliberately
narrow so ordinary code survives (std::vector<int>, a->b, 0x2A, [[nodiscard]], self.net, token = next()).
Redaction can't recognise a secret that looks like ordinary text: list those in .run/redact_terms.txt.

The outbound gate (outbound.py) runs it on Glimmer's reading, the question and its own outgoing text before
anything goes to Spark.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PLACEHOLDER = re.compile(r"\[\[([A-Z]+)_(\d+)\]\]")


def _luhn_ok(s: str) -> bool:
    """A card number passes the Luhn check, so other long runs of digits are left alone."""
    digits = re.sub(r"\D", "", s)
    if not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total, alt = total + n, not alt
    return total % 10 == 0


def _ipv6_ok(s: str) -> bool:
    """Enough groups and a digit to be an IPv6 address, not a C++ name such as abc::def."""
    groups = s.split(":")
    return (s.count("::") <= 1 and sum(1 for g in groups if g) >= 3
            and ("::" in s or len(groups) == 8) and any(c.isdigit() for c in s))


def _token_ok(s: str) -> bool:
    """A long run is a token if it is hex, or mixes digits with both cases; a long identifier isn't."""
    body = s.rstrip("=")
    if re.fullmatch(r"[0-9a-fA-F]{32,}", body):
        return True
    return (sum(c.isdigit() for c in body) >= 4 and any(c.islower() for c in body)
            and any(c.isupper() for c in body))


# (kind, pattern, check). A pattern with groups named v/v2/v3 replaces only that group, keeping the rest.
_HOST_TLDS = r"(?:com|org|gov|edu|mil|local|corp|internal|intranet|lan|(?:co|ac|gov|org|ltd|plc)\.uk)"
_PATTERNS: list[tuple[str, re.Pattern, Callable[[str], bool] | None]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), None),
    ("KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"                     # AWS access key id
                       r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"        # GitHub tokens
                       r"|\bgithub_pat_[A-Za-z0-9_]{40,}\b"
                       r"|\bxox[abprs]-[A-Za-z0-9-]{10,}\b"                    # Slack
                       r"|\bAIza[0-9A-Za-z_-]{35}\b"                           # Google API key
                       r"|\bLLM_\d{6,}_[A-Za-z0-9_-]{10,}"                     # Meta Model API key
                       r"|\bsk-[A-Za-z0-9_-]{20,}"), None),                    # sk-... style keys
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), None),
    ("SECRET", re.compile(r"(?i)\bbearer\s+(?P<v>[A-Za-z0-9._~+/=-]{16,})"), None),
    # key = "value" / key: 'value' (any quoted value), or an unquoted config-style value that contains a
    # digit or symbol and ends the token (so code such as `token = next_token();` is left alone)
    # (also as part of a longer name: DB_PASSWORD, AWS_SECRET_ACCESS_KEY, github_token, password_hash)
    ("SECRET", re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+_)*(?:password|passwd|pwd|secret|token|api_?key|"
                          r"access_?key|private_?key)(?:_[A-Za-z0-9]+)*\b[\"']?\s*[:=]\s*"
                          r"(?:\"(?P<v>[^\"\n]{1,200})\"|'(?P<v2>[^'\n]{1,200})'"
                          r"|(?P<v3>(?=[^\s,;\"'()]*[\d!@#$%^&*~])[^\s,;\"'()]{4,200})(?=[\s,;]|$))"), None),
    ("URL", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]{1,15}://[^\s\"'<>()\[\]{}]+"), None),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),
    ("PATH", re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s\"'<>|]+"), None),           # UNC \\server\share\...
    ("USER", re.compile(r"(?i)\b[A-Z]:\\Users\\(?P<v>[^\\\s\"'<>|]+)"), None),  # C:\Users\<name>
    ("USER", re.compile(r"(?<![\w/])/home/(?P<v>[a-z_][a-z0-9_.-]*)"), None),
    ("IP", re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])"), None),
    ("IP", re.compile(r"(?<![\w:])[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7}(?![\w:])"), _ipv6_ok),
    ("MAC", re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), None),
    ("CARD", re.compile(r"(?<![\d.])\d(?:[ -]?\d){12,18}(?![\d.])"), _luhn_ok),
    ("HOST", re.compile(rf"(?<![\w.@/-])(?:[a-z0-9][a-z0-9-]*\.)+{_HOST_TLDS}(?![\w.(-])"), None),
    ("TOKEN", re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9+/_=-])"), _token_ok),
]
_TERMS_AT = 5   # the user's own terms run after the key and secret patterns, before URLs and the rest


@dataclass
class Redaction:
    """One pass of redact: the text with placeholders, and what they stand for."""
    text: str
    mapping: dict[str, str] = field(default_factory=dict)   # placeholder -> original
    findings: dict[str, int] = field(default_factory=dict)  # kind -> number replaced in this pass


def load_terms(path: Path) -> list[str]:
    """The user's own sensitive words (project, client, host names), one per line; '#' starts a comment."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    terms = {ln.split("#", 1)[0].strip() for ln in lines}
    return sorted((t for t in terms if len(t) >= 2), key=len, reverse=True)   # longest first


def redact(text: str, terms: list[str] | tuple = (), mapping: dict[str, str] | None = None) -> Redaction:
    """Replace each secret-looking value and each of the user's ``terms`` with a placeholder. An earlier pass's
    ``mapping`` keeps the same placeholder for the same value; ``mapping`` itself isn't changed."""
    mapping = dict(mapping or {})
    reverse = {v: k for k, v in mapping.items()}
    counters: dict[str, int] = {}
    for ph in mapping:
        m = PLACEHOLDER.fullmatch(ph)
        if m:
            counters[m.group(1)] = max(counters.get(m.group(1), 0), int(m.group(2)))
    findings: dict[str, int] = {}

    def placeholder(kind: str, value: str) -> str:
        if value in reverse:
            ph = reverse[value]
        else:
            counters[kind] = counters.get(kind, 0) + 1
            ph = f"[[{kind}_{counters[kind]}]]"
            mapping[ph], reverse[value] = value, ph
        findings[kind] = findings.get(kind, 0) + 1
        return ph

    patterns = list(_PATTERNS)
    if terms:
        alt = "|".join(re.escape(t) for t in terms)
        patterns.insert(_TERMS_AT, ("TERM", re.compile(rf"(?i)(?<!\w)(?:{alt})(?!\w)"), None))

    for kind, pat, check in patterns:
        def sub(m: re.Match, kind: str = kind, pat: re.Pattern = pat, check=check) -> str:
            whole = m.group(0)
            if PLACEHOLDER.search(whole) or (check is not None and not check(whole)):
                return whole
            names = [g for g in ("v", "v2", "v3") if g in pat.groupindex and m.group(g) is not None]
            if not names:
                return placeholder(kind, whole)
            g = names[0]
            start, end = m.start(g) - m.start(), m.end(g) - m.start()
            return whole[:start] + placeholder(kind, m.group(g)) + whole[end:]
        text = pat.sub(sub, text)
    return Redaction(text, mapping, findings)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put the original values back (only ever on this laptop)."""
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def leaked_values(text: str, mapping: dict[str, str]) -> list[str]:
    """Placeholders whose original value still appears in ``text`` (for example re-added by a model)."""
    low = text.lower()
    return [ph for ph, v in mapping.items() if len(v) >= 4 and v.lower() in low]
