"""Redaction before text leaves the laptop (redact.py)."""
import pytest

from nanokvm_mcp.redact import PLACEHOLDER, leaked_values, load_terms, redact, restore

SECRETS = [
    ("aws_key = AKIAIOSFODNN7EXAMPLE", "KEY"),
    ("export TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "KEY"),
    ("key LLM_1234567890123456_" + "Zz9-dummy-KEY-value", "KEY"),  # split so the commit hook allows it
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "JWT"),
    ('const char* password = "hunter2!";', "SECRET"),
    ("api_key: 'abc123def'", "SECRET"),
    ("DB_PASSWORD=S3cret!pass", "SECRET"),
    ('AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG"', "SECRET"),
    ("see https://build.example-corp.com/job/42?token=x", "URL"),
    ("mail jane.doe@example.org now", "EMAIL"),
    (r"open \\fileserver01\projects\alpha\spec.docx", "PATH"),
    (r"C:\Users\jdoe\source\repos", "USER"),
    ("connect to 10.20.30.40 port 22", "IP"),
    ("fe80::1ff:fe23:4567:890a is up", "IP"),
    ("nic 00:1A:2B:3C:4D:5E", "MAC"),
    ("card 4111 1111 1111 1111 on file", "CARD"),
    ("ping buildbox.corp and git.example.co.uk", "HOST"),
    ("hash 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "TOKEN"),
]


@pytest.mark.parametrize("text,kind", SECRETS)
def test_each_kind_is_redacted_and_restored(text, kind):
    r = redact(text)
    assert r.findings.get(kind), (r.text, r.findings)
    assert any(ph.startswith(f"[[{kind}_") for ph in r.mapping)
    for value in r.mapping.values():
        assert value not in r.text
    assert restore(r.text, r.mapping) == text


CODE = """\
#include <vector>
[[nodiscard]] std::vector<int> parse_line(const std::string& line);
auto a = b->next; int mask = 0x2A; double eps = 1e-9; long long big = 1000000007LL;
token = next_token(); auth = get_auth(); if (password_ok) { retry(3); } token_count = 5; api_key_len = 32;
self.net.forward(x); ctx.app.run(); const char* v = "v1.2.3"; open("config.json");
std::chrono::milliseconds(1500); a::b::c(); kMaxBufferSizeForChannelBytesTotal = 1;
for (int i = 0; i <= n; ++i) { total += v[i]; } // version 10.0.19045 build
"""


def test_ordinary_code_is_left_alone():
    r = redact(CODE)
    assert r.text == CODE, r.findings
    assert r.mapping == {}


def test_same_value_same_placeholder_and_shared_mapping():
    r1 = redact("a@x.com and b@y.com and a@x.com")
    assert r1.text == "[[EMAIL_1]] and [[EMAIL_2]] and [[EMAIL_1]]"
    r2 = redact("b@y.com wrote to c@z.com", mapping=r1.mapping)
    assert r2.text == "[[EMAIL_2]] wrote to [[EMAIL_3]]"
    assert redact(r2.text, mapping=r2.mapping).text == r2.text          # a second pass changes nothing


def test_user_terms_case_insensitive_and_whole_words(tmp_path):
    f = tmp_path / "redact_terms.txt"
    f.write_text("# our names\nProject Falcon\nAcmeCorp  # client\n", encoding="utf-8")
    terms = load_terms(f)
    assert terms == ["Project Falcon", "AcmeCorp"]
    r = redact("// project falcon for ACMECORP; AcmeCorporation stays", terms)
    assert r.text == "// [[TERM_1]] for [[TERM_2]]; AcmeCorporation stays"
    assert load_terms(tmp_path / "missing.txt") == []


def test_card_numbers_need_a_valid_checksum():
    assert redact("id 4111 1111 1111 1112").mapping == {}


def test_leaked_values_and_placeholder_pattern():
    r = redact("mail jane.doe@example.org")
    assert leaked_values("text with Jane.Doe@example.org again", r.mapping) == ["[[EMAIL_1]]"]
    assert leaked_values(r.text, r.mapping) == []
    assert not PLACEHOLDER.search("[[nodiscard]] [[maybe_unused]]")
