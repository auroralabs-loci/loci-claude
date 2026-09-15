"""`lib/loci_json.sh` is the field reader for every hook, and it had no tests.

It replaced a three-rung ladder — jq, then sed, then parameter expansion — whose
verdict depended on which host tools a machine happened to have. There is one
rung now and it runs everywhere, which is the whole point of it; the cost of
that is that a defect in it is a defect in `contract-guard.sh`, both edge hooks,
`draft-pending-nudge.sh`, `turn-clean.sh`, `session-init.sh`,
`lib/detect-project.sh` and `lib/setup-steps.sh` at once. Two shipped:

* **F13** — the closing-quote scan walked one escaped quote at a time and
  re-copied the rest of the document behind each, so a 64 KB value holding
  13 000 of them cost 4.2 s of the contract guard's 5 s budget. Past that the
  hook is killed and `PreToolUse` FAILS OPEN. `test_a_quote_dense_value_*` is
  the detector, and it asserts a clock: only a clock catches a cost defect.
* **F17** — the key search looked for the literal text `"file_path"`, so a
  NAME spelled with escapes was ABSENT to it where `json.loads` and
  `JSON.parse` both read it. An absent `file_path` is a silent ALLOW in
  `contract-guard.sh` and a silent "nothing recorded" in six other hooks.
* **F12** — `\\uXXXX` was left as the six characters that were written, where
  the jq it replaced decoded it, so an edit to `…/Проект/.loci/contract.yaml`
  was denied when the payload spelled the path in raw UTF-8 and allowed when
  it spelled it escaped.
* **F19** — a name used as a key TWICE answers here with the first and in
  `json.loads`/`JSON.parse` with the last, so `contract-guard.sh` decided
  about `ok.c` while Claude Code wrote `.loci/contract.yaml`. Fixed by
  `loci_json_dup` and a refusal in the guard, NOT by changing which key wins —
  see `test_the_envelope_nesting_is_why_the_first_key_wins`, which is the
  measurement that rejected the other answer.

`json.loads` is the oracle for everything below that is not a clock. The one
place the library deliberately differs from it is `\\u0000`: a bash string
cannot hold a NUL, so the character is dropped rather than truncating the
value at it.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
LIB = PLUGIN_ROOT / "lib" / "loci_json.sh"

#: A literal backslash, built rather than written: the tooling on this machine
#: decodes a `\uXXXX` in file content into the character it names.
B = chr(92)

#: Read the document on stdin, print one field's value RAW to $3. Written to a
#: file rather than captured through `$(…)`, which eats a trailing newline —
#: the library returns one and the difference would be invisible here.
#: `$4` runs after the source, so a test can lower one of the private caps the
#: library sets as plain constants.
DRIVER = """
. "$1"
eval "$4"
IFS= read -r -d '' doc || true
loci_json_load "$doc"
loci_json_get "$2" > "$3"
exit $?
"""


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


pytestmark = [pytest.mark.unit,
              pytest.mark.skipif(_find_bash() is None, reason="bash required")]


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _read(document: str, key: str, tmp_path: Path, *,
          env: dict | None = None, setup: str = ":") -> tuple[int, bytes]:
    """`loci_json_get <key>` over `document`; returns (exit status, raw bytes)."""
    out = tmp_path / "value.bin"
    base = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())}
    proc = subprocess.run(
        [_find_bash(), "-c", DRIVER, "driver", _to_bash_path(LIB), key,
         _to_bash_path(out), setup],
        input=document.encode("utf-8"),
        capture_output=True, timeout=300, env={**base, **(env or {})},
    )
    assert not proc.stderr, proc.stderr.decode("utf-8", "replace")
    return proc.returncode, (out.read_bytes() if out.is_file() else b"")


#: `loci_json_dup <key>`'s exit status alone — it prints nothing.
DUP_DRIVER = """
. "$1"
eval "$4"
IFS= read -r -d '' doc || true
loci_json_load "$doc"
loci_json_dup "$2"
rc=$?
printf '%s' "$rc" > "$3"
exit $rc
"""


def _dup(document: str, key: str, tmp_path: Path, *,
         env: dict | None = None, setup: str = ":") -> int:
    """`loci_json_dup <key>` over `document`; returns its exit status."""
    out = tmp_path / "dup.bin"
    base = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())}
    proc = subprocess.run(
        [_find_bash(), "-c", DUP_DRIVER, "driver", _to_bash_path(LIB), key,
         _to_bash_path(out), setup],
        input=document.encode("utf-8"),
        capture_output=True, timeout=300, env={**base, **(env or {})},
    )
    assert not proc.stderr, proc.stderr.decode("utf-8", "replace")
    assert out.is_file() and out.read_bytes() == str(proc.returncode).encode()
    return proc.returncode


def _doc(body: str) -> str:
    """A document whose `target` field carries `body` VERBATIM.

    Built by hand and not with `json.dumps`, because every case here is about
    the exact escape spelling that arrives on the wire.
    """
    return '{"before": "b", "target": "%s", "after": "a"}' % body


def _value(body: str, tmp_path: Path, **kw) -> bytes:
    rc, got = _read(_doc(body), "target", tmp_path, **kw)
    assert rc == 0, f"the read failed on {body!r}"
    return got


def test_there_is_something_to_check():
    """Non-vacuous: a renamed function passes every test below silently."""
    text = LIB.read_text(encoding="utf-8")
    for fn in ("loci_json_load", "loci_json_get", "_loci_json_string",
               "_loci_json_unwrap", "_loci_json_unicode", "_loci_json_seek",
               "_loci_json_seek_win", "_loci_json_seek_escaped"):
        assert re.search(rf"^{fn}\(\) \{{", text, re.M), f"{fn} is gone"


# ── the closing-quote scan (F13) ────────────────────────────────────────────

@pytest.mark.parametrize("body,want", [
    ("plain", b"plain"),
    ("", b""),
    (r"a\"b", b'a"b'),                    # an escaped quote mid-value
    (r"\"", b'"'),                        # a value that is only one
    (r"a\\", b"a\\"),                     # ends in an escaped backslash
    (r"a\\\"b", b'a\\"b'),                # escaped backslash, then a quote
    (r"a\\\\", b"a\\\\"),                 # two escaped backslashes at the end
    (r"C:\\proj\\src\\main.c", rb"C:\proj\src\main.c"),
    (r"a\/b", b"a/b"),
    (r"tab\there", b"tab\there"),
    (r"nl\nhere", b"nl\nhere"),
])
def test_the_scan_closes_where_json_says_it_does(tmp_path, body, want):
    assert _value(body, tmp_path) == want
    assert json.loads(_doc(body))["target"].encode("utf-8") == want


@pytest.mark.parametrize("run", range(7))
def test_a_run_of_backslashes_before_the_quote(tmp_path, run):
    """The parity case the old walk counted by hand, now the probe's job.

    An EVEN run leaves the quote unescaped and closes the value; an odd one
    escapes it and the value runs on. This is where the walk was subtle — it
    counted the run on the segment and never on the accumulated body — and
    where the probe has no counting at all.
    """
    body = "x" + "\\" * (run * 2) + r'\"tail'      # always an even run, then \"
    assert _value(body, tmp_path) == json.loads(_doc(body))["target"].encode()
    closing = "x" + "\\" * (run * 2)               # even run, then the CLOSE
    rc, got = _read('{"target": "%s", "after": "a"}' % closing, "target", tmp_path)
    assert (rc, got) == (0, json.loads('{"target": "%s"}' % closing)["target"].encode())


def test_a_value_the_prefix_cut_short_is_not_a_value(tmp_path):
    """No closing quote means no answer — not a truncated one.

    `loci_json_load` parses a bounded prefix, so a long value arrives without
    its close. Answering with the part that did arrive would hand the contract
    guard a path prefix, and a prefix matches the wrong things.
    """
    doc = '{"target": "%s", "after": "a"}' % ("x" * 400)
    rc, got = _read(doc, "target", tmp_path, env={"LOCI_JSON_MAX": "64"})
    assert (rc, got) == (1, b"")


def test_an_invalid_byte_cannot_make_the_read_over_run_the_value(tmp_path):
    """The probe's offsets must survive a document bash cannot segment.

    Found in F13's review round, in the spelling this replaced. The probe was
    one pass, `${rest//\\?/FILL}`, which reads as the grammar itself — a
    backslash and the one character it claims. But `?` is a WILDCARD, matched
    against one character, and an invalid byte desynchronises bash's multibyte
    scan: `?` then took one byte of the three-byte character after a
    backslash, two orphan bytes survived, and the probe came out LONGER than
    the document. The offset mapping the whole design rests on broke, and the
    read ran past the end of the value into the document — `…/x", ` for the
    payload below. 542 of 4 000 random byte-soup values under `en_US.UTF-8`;
    none under `LC_ALL=C`, which is why the contract guard never saw it and
    the seven hooks that read under the ambient locale would have.

    Two fixed ASCII patterns cannot desynchronise, because neither the pattern
    nor the fill contains a wildcard: two characters in, two characters out,
    in any locale. The assertion in the library is the belt to that braces.
    """
    body = b'a\\"b\xff\\\xe4\xb8\xad/x'          # a \" b <FF> \ 中 /x
    doc = b'{"target": "' + body + b'", "after": "a"}'
    # `json.loads` is not the oracle here and cannot be: this document is
    # malformed twice over — an invalid UTF-8 byte, and `\` in front of a
    # character JSON does not let it escape — which is the point. What arrives
    # on the hook's stdin is untrusted bytes, not a guaranteed document, and
    # the answer to one has to stay inside the value either way. `\"` becomes
    # a quote, the invalid escape is handed back as its two characters, and
    # the read STOPS at the closing quote.
    want_b = b'a"b\xff\\\xe4\xb8\xad/x'
    for locale in ("C", "en_US.UTF-8"):
        out = tmp_path / "v.bin"
        proc = subprocess.run(
            [_find_bash(), "-c", DRIVER, "driver", _to_bash_path(LIB), "target",
             _to_bash_path(out), ":"],
            input=doc, capture_output=True, timeout=120,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()),
                 "LC_ALL": locale},
        )
        assert not proc.stderr, proc.stderr
        got = out.read_bytes()
        assert got == want_b, (
            f"under LC_ALL={locale} the read returned {got!r}, not {want_b!r} — "
            f"the probe is not length-preserving and the value over-ran into "
            f"the document")


# ── `\uXXXX` (F12) ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,want", [
    (r"\u0041", b"A"),                              # ASCII
    (r"\u00e9", "é".encode()),                      # two-byte
    (r"\u041f", "П".encode()),                      # Cyrillic — F12's own case
    (r"\u20ac", "€".encode()),                      # three-byte
    (r"\ud83d\ude00", "😀".encode()),               # a surrogate PAIR is one char
    (r"\uD83D\uDE00", "😀".encode()),               # and the case does not matter
    (r"/c/\u041f\u0440/.loci/contract.yaml",
     "/c/Пр/.loci/contract.yaml".encode()),
])
def test_a_unicode_escape_decodes_to_its_utf8_bytes(tmp_path, body, want):
    assert _value(body, tmp_path) == want
    assert json.loads(_doc(body))["target"].encode("utf-8") == want


@pytest.mark.parametrize("body,want", [
    (r"\ud83d", rb"\ud83d"),        # a high surrogate with nothing after it
    (r"\udc00", rb"\udc00"),        # a low surrogate with nothing before it
    (r"\uzzzz", rb"\uzzzz"),        # four characters that are not hex
    (r"\u12", rb"\u12"),            # two hex digits where four are required
    # A high surrogate whose neighbour is NOT its low half: the surrogate
    # is left alone and the neighbour still decodes. Only the pair is one
    # character, and only when the low half comes immediately after.
    (r"\ud83d\u0041", rb"\ud83dA"),
    (r"\ud83d\ud83d\ude00", rb"\ud83d" + "😀".encode()),
])
def test_what_encodes_no_character_is_left_as_written(tmp_path, body, want):
    """Six characters in, six characters out — never invented bytes.

    A lone surrogate encodes nothing: CESU-8 style bytes for one are invalid
    UTF-8 and would match no path on any filesystem. A malformed escape is the
    same answer to a different question — "I could not read this" is honestly
    reported by handing back what was there.

    None of these is the PREFIX-CUT case, and a review round caught the last
    row being labelled as one: a value the prefix cuts short has no closing
    quote, so `_loci_json_string` returns 1 and the decoder is never reached
    at all. That is `test_a_value_the_prefix_cut_short_is_not_a_value`, and
    the same round swept `LOCI_JSON_MAX` across all 16 offsets through a
    surrogate pair — every one answered `rc=1` and empty, never a partial and
    never an invented byte.
    """
    assert _value(body, tmp_path) == want


def test_a_nul_escape_is_dropped_and_does_not_truncate(tmp_path):
    """`\\u0000` is the one place this reader cannot match `json.loads`.

    A bash string is NUL-terminated, so the choice is between dropping the
    character and truncating the value at it. Dropping is the direction the
    contract guard needs: the path below still names a guarded file.
    """
    assert _value(r"a\u0000b", tmp_path) == b"ab"
    assert _value(r".loci/contract\u0000.yaml", tmp_path) == b".loci/contract.yaml"


def test_a_backslash_arriving_as_an_escape_is_not_re_read(tmp_path):
    """`\\u005C` decodes to a backslash, and nothing may read it as one.

    Decoded anywhere but last, the `\\` it emits joins the `n` after it and
    the chain turns a Windows path into one carrying a newline.
    """
    assert _value(r"\u005Cn", tmp_path) == b"\\n"
    assert _value(r"C:\u005Cproj", tmp_path) == rb"C:\proj"
    assert _value(r"\u005c\u0022", tmp_path) == b'\\"'


def test_an_escaped_backslash_does_not_start_a_unicode_escape(tmp_path):
    """`\\\\u0041` is a backslash and the letters `u0041`, not an `A`.

    The `\\\\` claim runs first for exactly this reason, and the marker pass
    after it finds nothing left to take.
    """
    assert _value(r"\\u0041", tmp_path) == rb"\u0041"
    assert json.loads(_doc(r"\\u0041"))["target"] == r"\u0041"


def test_the_decode_is_the_same_bytes_under_LC_ALL_C(tmp_path):
    """The guard sets `LC_ALL=C` for route 2 and restores the locale for route 1.

    Both are reachable from this library, so the decode may not depend on
    which one is in force. It does not, because the bytes are built by hand:
    bash's own `printf '\\U0001F600'` under `C` emits the ten characters of
    the escape instead of the emoji.
    """
    for body in (r"\u041f", r"\ud83d\ude00", r"\u20ac", r"\u0041"):
        want = json.loads(_doc(body))["target"].encode("utf-8")
        for locale in ("C", "C.UTF-8", "en_US.UTF-8"):
            got = _value(body, tmp_path, env={"LC_ALL": locale})
            assert got == want, f"{body!r} under LC_ALL={locale}"


def test_an_escape_the_cap_does_not_reach_stays_as_written(tmp_path):
    """`_LOCI_JSON_UMAX` bounds the decode, and the bound is the old behaviour.

    Each `\\u` costs one pass over what is left, so escapes × length is the
    meter — the same one `contract-guard.sh` charges `_R2_MAX_SCAN` with. Over
    it the rest of the escapes are handed back as the six characters they
    arrived as, which is what this library did with every one of them before
    F12; nothing that ever worked is given up.
    """
    body = r"\u0041" * 400
    got = _value(body, tmp_path, setup="_LOCI_JSON_UMAX=1")
    # A cap of 1 stops at the first escape, so the rest survive verbatim …
    assert got.count(b"\\u0041") > 380, got[:80]
    # … and the marker the decoder parks them on never reaches the caller.
    assert b"\x02" not in got
    # Uncapped, the same value decodes whole.
    assert _value(body, tmp_path) == b"A" * 400


# ── the oracle, and the clock ───────────────────────────────────────────────

_ALPHABET = [
    "a", " ", "/", ".", "-", "_", "{", "}", ":", ",", "[", "]",
    r"\"", "\\\\", r"\n", r"\t", r"\r", r"\b", r"\f", r"\/",
    r"\u0041", r"\u041f", r"\u00e9", r"\u20ac", r"\ud83d\ude00",
    r"\u005c", r"\u0022", r"\u002f", r"\u0001", r"\u007f",
    "П", "é", "😀",
]


def test_the_reader_agrees_with_json_loads(tmp_path):
    """A seeded differential over escape soup, in one bash process.

    Written as a fuzz because the interesting cases are the ADJACENCIES — a
    `\\\\` in front of a `\\"`, a `\\u005c` in front of an `n` — and there are
    more of those than anyone enumerates by hand. The seed is fixed so a red
    here is reproducible; the same generator was run over 5 600 cases against
    both locales while the fix was written.
    """
    rnd = random.Random(20260911)
    cases: list[str] = []
    while len(cases) < 250:
        body = "".join(rnd.choice(_ALPHABET) for _ in range(rnd.randint(0, 20)))
        doc = _doc(body)
        try:
            json.loads(doc)
        except ValueError:
            continue          # not a document, so the oracle has no answer
        cases.append(doc)

    out = tmp_path / "values"
    out.mkdir()
    runner = """
. "$1"
LOCI_JSON_MAX=1000000
i=0
while IFS= read -r line; do
    loci_json_load "$line"
    loci_json_get target > "$2/$i"
    printf '%s\\n' "$?"
    i=$((i + 1))
done
"""
    proc = subprocess.run(
        [_find_bash(), "-c", runner, "runner", _to_bash_path(LIB),
         _to_bash_path(out)],
        input=("\n".join(cases) + "\n").encode("utf-8"),
        capture_output=True, timeout=600,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())},
    )
    assert not proc.stderr, proc.stderr.decode("utf-8", "replace")
    codes = proc.stdout.decode().split()
    assert len(codes) == len(cases), (len(codes), len(cases))

    bad = []
    for i, (doc, rc) in enumerate(zip(cases, codes)):
        # `chr(0)` and not `"\\x00"`: `test_no_test_file_carries_a_mangled_escape`
        # scans every compiled string in this package for a control character,
        # because one welded into a regex makes the pattern unmatchable and the
        # assertion vacuous. The NUL here is deliberate — the library drops it
        # and the oracle has to as well — and spelling it as a call keeps it out
        # of that scan without adding a line to the registry of exceptions.
        want = json.loads(doc)["target"].replace(chr(0), "").encode("utf-8")
        got = (out / str(i)).read_bytes()
        if rc != "0" or got != want:
            bad.append(f"{ascii(doc)}\n    want {want!r}\n    got  {got!r} rc={rc}")
    assert not bad, "the reader and json.loads disagree:\n  " + "\n  ".join(bad[:8])


def _quote_dense(chars: int) -> str:
    unit = r'  emit(\"line\", \"x\");'
    return (unit * (chars // len(unit) + 1))[:chars]


def test_a_quote_dense_value_is_read_in_the_same_time_as_a_plain_one(tmp_path):
    """F13's detector, and it is a ratio so that a slow machine cannot hide it.

    The walk this replaced was O(escaped quotes × length): 64 KB holding
    ~11 600 of them took 14.7 s in-process against 0.4 s for the same 64 KB
    with no escapes at all — a ratio of 37. The probe is still O(k·n) — bash's
    `${v//pat/rep}` re-copies the tail per replacement, like the walk did — but
    with a ~60× smaller constant, and the ratio is what that buys. The probe is
    two substitution passes and
    a windowed find, and the same pair is 0.44 s against 0.20 s. 8 is loose
    enough that neither a loaded machine nor a faster one moves it, and tight
    enough that nothing quadratic gets back in.
    """
    dense = _doc(_quote_dense(64_000))
    plain = _doc("  emit((line), (x));" * (64_000 // 20))

    guard_cap = {"LOCI_JSON_MAX": "65536"}   # what contract-guard.sh sets
    t0 = time.monotonic()
    rc_p, got_p = _read(plain, "target", tmp_path, env=guard_cap)
    plain_s = time.monotonic() - t0
    t0 = time.monotonic()
    rc_d, got_d = _read(dense, "target", tmp_path, env=guard_cap)
    dense_s = time.monotonic() - t0

    assert rc_p == 0 and rc_d == 0
    assert got_d == json.loads(dense)["target"].encode("utf-8")
    assert got_p == json.loads(plain)["target"].encode("utf-8")
    assert dense_s / max(plain_s, 0.01) < 8.0, (
        f"{dense_s:.2f}s on a value with ~11 600 escaped quotes against "
        f"{plain_s:.2f}s on the same 64 KB with none — the closing-quote scan "
        f"is quadratic in escaped quotes again (F13)")


def test_a_quote_dense_value_is_read_inside_the_hook_budget(tmp_path):
    """The same defect against the wall clock, because the ratio alone is not it.

    `hooks.json` gives `contract-guard.sh` 5 s, and past it the hook is KILLED
    and `PreToolUse` fails OPEN — the write goes through. The guard sets
    `LOCI_JSON_MAX` to its own 64 KB cap, so this document is the largest
    value any hook reads. Measured here at 0.7 s including bash's startup,
    against 15 s before the fix; 2.5 s is the line because this repo's budget
    tests have a history of going red under load and green idle, and a
    detector that cries wolf gets ignored.
    """
    start = time.monotonic()
    rc, got = _read(_doc(_quote_dense(64_000)), "target", tmp_path,
                    env={"LOCI_JSON_MAX": "65536"})
    elapsed = time.monotonic() - start
    assert rc == 0 and len(got) > 50_000
    assert elapsed < 2.5, (
        f"reading one 64 KB quote-dense value took {elapsed:.1f}s; the whole "
        f"contract guard has 5 s before it is killed and fails open")
# ---------------------------------------------------------------------------
# F14 — `_loci_json_seek`: the key search, and the two walks beside it
# ---------------------------------------------------------------------------

#: Read NUL-separated documents from stdin, one `<rc>\t<value>` line out per
#: document. One bash process for a whole boundary sweep — which is what makes a
#: 900-offset sweep affordable in a unit test, where 900 `_read` calls would not
#: be. `$4` is the key; `$3` runs after the source, to lower `_LOCI_JSON_WINDOW`.
SWEEP_DRIVER = """
. "$1"
eval "$3"
: > "$2"
while IFS= read -r -d '' doc; do
    loci_json_load "$doc"
    if v=$(loci_json_get "$4"); then printf '0\t%s\n' "$v" >> "$2"
    else printf '1\t%s\n' "$v" >> "$2"; fi
done
"""

#: The five calls `draft-pending-nudge.sh` makes per invocation, in one process.
COUNT_DRIVER = """
. "$1"
eval "$4"
IFS= read -r -d '' doc || true
loci_json_load "$doc"
{
    loci_json_count "$3"
    for k in add edit disable enable; do printf ' '; loci_json_count "$3" "$k"; done
} > "$2"
"""

#: `extglob` is global shell state; the seek borrows it and must put it back.
#: RAW, because the document below carries an escape and a non-raw literal
#: would hand the shell the CHARACTER instead — which is the whole point of the
#: `z` field: it makes the second pass run, so the state sampling covers it.
STATE_DRIVER = r"""
. "$1"
eval "$3"
state() { shopt -q extglob && printf on || printf off; }
loci_json_load '{"a": ["cwd","cwd"], "cwd": "v", "n": [1,2], "z": "\u00e9"}'
: > "$2"
printf '%s' "$(state)" >> "$2"
loci_json_get cwd > /dev/null;     printf ' %s' "$(state)" >> "$2"
loci_json_get absent > /dev/null;  printf ' %s' "$(state)" >> "$2"
loci_json_has absent || true;      printf ' %s' "$(state)" >> "$2"
loci_json_kind cwd > /dev/null;    printf ' %s' "$(state)" >> "$2"
loci_json_count cwd > /dev/null;   printf ' %s' "$(state)" >> "$2"
loci_json_array_len n > /dev/null; printf ' %s' "$(state)" >> "$2"
"""

#: `loci_json_count` and the walk it falls back to, over one document. The two
#: are one function's two paths, so the differential IS the assertion.
COUNT_PAIR_DRIVER = """
. "$1"
eval "$5"
IFS= read -r -d '' doc || true
loci_json_load "$doc"
printf '%s %s' "$(loci_json_count "$3" "$4")" \
               "$(_loci_json_count_walk "$3" "$4")" > "$2"
"""

def _bash(driver: str, args: list[str], tmp_path: Path, *,
          stdin: bytes = b"", env: dict | None = None) -> Path:
    """Run `driver` with `$1` bound to the library; returns the output file."""
    out = tmp_path / "out.bin"
    base = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())}
    proc = subprocess.run(
        [_find_bash(), "-c", driver, "driver", _to_bash_path(LIB),
         _to_bash_path(out), *args],
        input=stdin, capture_output=True, timeout=300,
        env={**base, **(env or {})},
    )
    assert not proc.stderr, proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, proc.returncode
    return out


def _sweep(documents: list[str], key: str, tmp_path: Path, *,
           setup: str = ":") -> list[tuple[int, str]]:
    """`loci_json_get <key>` over each document; one (rc, value) per document."""
    payload = b"".join(d.encode("utf-8") + b"\0" for d in documents)
    out = _bash(SWEEP_DRIVER, [setup, key], tmp_path, stdin=payload)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(documents), (len(lines), len(documents))
    return [(int(rc), val) for rc, _, val in (ln.partition("\t") for ln in lines)]


def _elements(name: str, times: int, *, pad: str = "") -> str:
    """A document holding `name` `times` times as an ARRAY ELEMENT, then as a key.

    The only shape that reached the walk this replaced: unescaped, and with no
    colon after it. A string VALUE cannot do it — inside one every `"` arrives
    as `\\"`, so the name is spelled `\\"cwd\\"` and never matches.
    """
    return '{"a": [%s"z"], "%s": "/c/p"}' % ('"%s"%s,' % (name, pad) * times, name)


def test_a_name_repeated_as_an_array_element_costs_what_an_absent_one_costs(tmp_path):
    """F14's detector, as a ratio so a slow machine cannot hide it.

    The walk this replaced re-sliced the document once per occurrence of the
    NAME that was not a key, so it was quadratic in that count: 8 000 of them
    cost 9.50 s in-process against 0.04 s for the identical document with a name
    that does not collide — while the windowed search costs 0.41 s for the same
    pair. The control is the same document byte for byte, so what is measured is
    the occurrences and not the size.

    8 is the same line `test_a_quote_dense_value_*` draws, and for the same
    reason: loose enough that load does not move it, tight enough that nothing
    quadratic gets back in.
    """
    guard_cap = {"LOCI_JSON_MAX": "65536"}  # what contract-guard.sh sets
    dense = _elements("cwd", 8_000)
    plain = _elements("xyz", 8_000).replace('"xyz": "/c/p"', '"cwd": "/c/p"')
    assert len(dense) == len(plain)

    t0 = time.monotonic()
    rc_p, got_p = _read(plain, "cwd", tmp_path, env=guard_cap)
    plain_s = time.monotonic() - t0
    t0 = time.monotonic()
    rc_d, got_d = _read(dense, "cwd", tmp_path, env=guard_cap)
    dense_s = time.monotonic() - t0

    assert rc_p == 0 and got_p == b"/c/p"
    assert rc_d == 0 and got_d == b"/c/p"
    assert dense_s / max(plain_s, 0.01) < 8.0, (
        f"{dense_s:.2f}s on a document naming the key 8 000 times without a "
        f"colon against {plain_s:.2f}s on the same document with a name that "
        f"does not collide — the key search is quadratic again (F14)")


def test_a_name_repeated_as_an_array_element_is_read_inside_the_hook_budget(tmp_path):
    """The same defect against the wall clock, because the ratio alone is not it.

    `hooks.json` gives `contract-guard.sh` 5 s, and past it the hook is KILLED
    and `PreToolUse` fails OPEN — the write goes through. Measured here at 0.7 s
    including bash's startup, against 9.7 s before the fix. 2.5 s is the line
    this file already uses, for the reason recorded there.

    ⚠ IT RUNS IN A MULTIBYTE LOCALE, for the reason the op-tally budget test
    below does: this harness hands the hook a minimal env with no `LANG`, i.e.
    C, where bash's parameter expansion is several times faster. The walk this
    replaced came in at 2.98 s under C — over the line, but by 1.19×, which is
    not a margin. Under the UTF-8 a real session has it is far clear of it.
    """
    locale = _a_multibyte_locale(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    start = time.monotonic()
    rc, got = _read(_elements("cwd", 8_000), "cwd", tmp_path,
                    env={"LOCI_JSON_MAX": "65536",
                         "LANG": locale, "LC_ALL": locale})
    elapsed = time.monotonic() - start
    assert rc == 0 and got == b"/c/p"
    assert elapsed < 2.5, (
        f"reading one key past 8 000 non-key occurrences of its name took "
        f"{elapsed:.1f}s; the whole contract guard has 5 s before it is killed "
        f"and fails open")


def test_a_key_straddling_a_window_edge_is_still_found(tmp_path):
    """Boundary case 1: `"<key>"` is several characters and a window can cut it.

    The step overlaps by the key's width for exactly this, and the sweep walks
    the key across three whole windows one byte at a time, so no alignment goes
    untested. A miss reads as "key absent", which is ALLOW in the contract guard
    and "nothing recorded" in six other hooks.
    """
    docs = ['{"p": "%s", "cwd": "hit"}' % ("x" * pad) for pad in range(200)]
    got = _sweep(docs, "cwd", tmp_path, setup="_LOCI_JSON_WINDOW=64")
    bad = [i for i, (rc, v) in enumerate(got) if rc != 0 or v != "hit"]
    assert not bad, f"the key was missed at offsets {bad[:10]} of 200"


def test_the_whitespace_before_a_colon_may_cross_a_window_edge(tmp_path):
    """Boundary case 2, and the harder one: whitespace has no width to overlap by.

    `"cwd"` then 9 000 spaces then `:` is a legal key, so NO fixed overlap can
    hold the whole pattern — which is why a candidate running off a window's end
    is resolved against the document instead. Swept across two windows for runs
    that fit inside one, that straddle it exactly, and that span several.
    """
    runs = (0, 1, 2, 5, 63, 64, 65, 127, 128, 129, 200)
    docs, labels = [], []
    for pad in range(80):
        for nws in runs:
            docs.append('{"p": "%s", "cwd"%s: "hit"}' % ("x" * pad, " " * nws))
            labels.append((pad, nws))
    got = _sweep(docs, "cwd", tmp_path, setup="_LOCI_JSON_WINDOW=64")
    bad = [labels[i] for i, (rc, v) in enumerate(got) if rc != 0 or v != "hit"]
    assert not bad, f"the key was missed at (offset, whitespace) {bad[:10]}"


def test_a_name_with_no_colon_after_it_is_not_a_key_at_any_offset(tmp_path):
    """The property the search exists for, swept across the edge that could lose it.

    Both boundary cases above resolve a candidate against the DOCUMENT rather
    than the window, which is where a fix for them could start reading an array
    element as a key. Two sweeps: the element must not shadow the real key
    behind it, and on its own it must not answer at all.
    """
    docs = ['{"p": "%s", "a": ["cwd" ], "cwd": "hit"}' % ("x" * pad)
            for pad in range(120)]
    got = _sweep(docs, "cwd", tmp_path, setup="_LOCI_JSON_WINDOW=64")
    bad = [i for i, (rc, v) in enumerate(got) if rc != 0 or v != "hit"]
    assert not bad, f"an array element shadowed the key at offsets {bad[:10]}"

    alone = ['{"p": "%s", "a": ["cwd" ]}' % ("x" * pad) for pad in range(120)]
    got = _sweep(alone, "cwd", tmp_path, setup="_LOCI_JSON_WINDOW=64")
    bad = [i for i, (rc, v) in enumerate(got) if rc == 0]
    assert not bad, f"an array element answered as a key at offsets {bad[:10]}"


@pytest.mark.parametrize("document,want", [
    ('{"cwd": "/c/p"}', "/c/p"),
    ('{"cwd":"/c/p"}', "/c/p"),
    ('{"cwd"  : "/c/p"}', "/c/p"),
    ('{"cwd"\n\t: "/c/p"}', "/c/p"),
    ('{"cwd"\r\n: "/c/p"}', "/c/p"),
    ('{"cwd":\n\n  "/c/p"}', "/c/p"),
    ('{"tool_name": "cwd", "cwd": "/c/p"}', "/c/p"),
    ('{"a": ["cwd", "cwd"], "cwd": "/c/p"}', "/c/p"),
    ('{"a": {"cwd": "/c/p"}}', "/c/p"),
    ('{"a": "x\\"cwd\\": y", "cwd": "/c/p"}', "/c/p"),
])
def test_the_colon_is_what_makes_a_name_a_key(tmp_path, document, want):
    """The behaviours the walk got right, which the windowed search must keep."""
    rc, got = _read(document, "cwd", tmp_path)
    assert rc == 0 and got.decode("utf-8") == want


@pytest.mark.parametrize("document", [
    '{"tool_name": "cwd"}',
    '{"a": ["cwd", "cwd"]}',
    '{"a": "\\"cwd\\": x"}',
    '{"a": 1}',
    '{"cwd"}',
    '{"cwd"   }',
    '{}',
    '',
])
def test_a_name_never_followed_by_a_colon_is_absent(tmp_path, document):
    """An absent key answers non-zero — the `loci_json_has` contract."""
    rc, got = _read(document, "cwd", tmp_path)
    assert rc != 0 and got == b""


@pytest.mark.parametrize("setup,expect", [
    (":", " ".join(["off"] * 7)),
    ("shopt -s extglob", " ".join(["on"] * 7)),
])
def test_the_seek_puts_back_the_shell_option_it_borrows(tmp_path, setup, expect):
    """`extglob` is GLOBAL shell state and seven hooks source this library.

    The search needs it: the pattern that steps over a non-key occurrence in one
    glob is an extended one, and bash parses a function's `case` patterns when
    the function is DEFINED — so the pattern can only live in a parameter
    expansion, whose setting is read at expansion time. Borrowing it is fine;
    leaving it on would change how every `case` in every sourcing hook matches,
    and turning it off would break a hook that had set it for itself.

    Sampled after EVERY entry point rather than once at the end, so one leaking
    path cannot hide behind a later one that restores.
    """
    out = _bash(STATE_DRIVER, [setup], tmp_path)
    assert out.read_text(encoding="utf-8") == expect


@pytest.mark.parametrize("document,tally", [
    ('{"o":[{"op":"add"},{"op":"edit"},{"op":"add"}]}', "3 2 1 0 0"),
    ('{"o": [{"op": "add"}, {"op": "edit"}]}', "2 1 1 0 0"),
    ('{"o": [{"op"  : "add"}, {"op":"add"}]}', "2 2 0 0 0"),
    ('{"o": [{"op":  "add"}, {"op":"add"}]}', "2 2 0 0 0"),
    ('{"o":[{"op":"added"},{"op":"add"}]}', "2 1 0 0 0"),
    ('{"o":[{"op":"disable"},{"op":"enable"}]}', "2 0 0 1 1"),
    ('{"a":["op","op"],"op":"add"}', "1 1 0 0 0"),
    ('{"c":"\\"op\\":x","op":"add"}', "1 1 0 0 0"),
    ('{"op":5,"op":"add"}', "2 1 0 0 0"),
    ('{"a":1}', "0 0 0 0 0"),
    # Overlapping literals — the `"` that closes one match opens the next.
    # `${doc//…}` deletes non-overlapping matches left to right and undercounts
    # both of these, so both must reach the walk. Malformed JSON, and that is
    # the point: what arrives on a hook's stdin is untrusted bytes.
    ('{"op":"add"op":"add"}', "2 2 0 0 0"),
    ('{"op":"op":"op":"op"}', "3 0 0 0 0"),
])
def test_the_tally_counts_the_same_ops_whichever_path_answers(tmp_path, document, tally):
    """`loci_json_count` has a fast path and a walk; they must not disagree.

    The fast path is arithmetic over a FIXED-length literal and answers the
    canonical `"op":` and `json.dumps`' `"op": `; anything else — whitespace
    before the colon, or more than one space after it — falls through to the
    walk. Rows 3 and 4 are the fallback, and they are here so a change to the
    test for it cannot silently route every document down one path.
    """
    out = _bash(COUNT_DRIVER, ["op", ":"], tmp_path,
                stdin=document.encode("utf-8") + b"\0")
    assert out.read_text(encoding="utf-8").strip() == tally


def _a_multibyte_locale(tmp_path) -> str | None:
    """A locale on this host where bash's `${#var}` is not a byte count.

    PROBED and not named, the way `test_contract_guard.py` probes for one: Git
    Bash here has `en_US.UTF-8` and not `C.UTF-8`, WSL has the reverse, and
    naming either one pins `LC_ALL=C` on the other platform.
    """
    script = tmp_path / "mb.sh"
    script.write_text('v=$(printf "\\xf0\\x9f\\x98\\x80")\n'
                      '[ "${#v}" -lt 4 ] && echo multibyte\n',
                      encoding="utf-8", newline="\n")
    for name in ("en_US.UTF-8", "C.UTF-8", "C.utf8", "en_US.utf8"):
        out = subprocess.run(
            [_find_bash(), _to_bash_path(script)],
            capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LC_ALL": name})
        if out.stdout.strip() == "multibyte":
            return name
    return None


def test_the_whole_op_tally_is_counted_inside_the_hook_budget(tmp_path):
    """F14 scope item 3: `draft-pending-nudge.sh` makes FIVE of these per turn.

    The walk cost 0.819 s each at the 16 KB default its callers read under, so
    the tally spent 5.67 s of the 10 s `hooks.json` gives that hook — on
    counting four verbs. The document here is that ceiling: a contract draft
    with as many pending ops as 16 KB holds. Measured at 0.3 s including bash's
    startup.

    ⚠ IT RUNS IN A MULTIBYTE LOCALE, and without that it does not detect
    anything. Every harness in this repo hands the hook a minimal env with no
    `LANG`, i.e. C, where bash's parameter expansion is about four times faster
    than in the UTF-8 a real session has: the walk is 5.67 s here and 1.34 s
    under C, which is UNDER this budget. `contract-guard.sh` sets `LC_ALL=C`
    for its own accounting and says the same thing at its top; this hook sets
    no locale at all, so what a user's machine gives it is what it runs under.
    """
    locale = _a_multibyte_locale(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    doc = '{"ops": [%s{}]}' % ('{"op": "add"}, ' * 1_400)
    start = time.monotonic()
    out = _bash(COUNT_DRIVER, ["op", ":"], tmp_path,
                stdin=doc.encode("utf-8") + b"\0",
                env={"LANG": locale, "LC_ALL": locale})
    elapsed = time.monotonic() - start
    counted = [int(c) for c in out.read_text(encoding="utf-8").split()]
    # `loci_json_load` keeps a 16 KB PREFIX, so the last op can be cut
    # between its key and its value: the key counts, the verb does not.
    assert counted[0] > 1_000
    assert counted[0] - counted[1] in (0, 1), counted
    assert sum(counted[1:]) in (counted[0], counted[0] - 1), counted
    assert elapsed < 2.5, (
        f"the five-call op tally took {elapsed:.1f}s; the hook it runs in has "
        f"10 s, and it has four other things to do")
def test_an_invalid_byte_cannot_hide_a_key(tmp_path):
    """F14's review round, and the reason the seek reads BYTES.

    `${doc:off:4096}` under a multibyte locale comes back SHORT when bash's
    scan desynchronises on a byte that starts no character — 4 094 for a 4 096
    request, with 9 000 characters still to go — and the window loop read that
    as the end of the document. The key after it was never looked at, and
    `loci_json_has file_path` answered NO for a payload that carries one, which
    is ALLOW in `contract-guard.sh`. The same slice also came back SHIFTED by
    two characters, which put the value two early: `loci_json_get file_path`
    returned `: ` rather than the path.

    `contract-guard.sh` itself was never exposed — it exports `LC_ALL=C` above
    its three field reads — but `pre-edit-hook.sh`, `post-edit-hook.sh`,
    `turn-clean.sh`, `draft-pending-nudge.sh`, `session-init.sh` and
    `lib/setup-steps.sh` all read `file_path` or `cwd` under the ambient one.

    This is the same hazard class as
    `test_an_invalid_byte_cannot_make_the_read_over_run_the_value` above, one
    layer over: there the probe desynchronised, here the window did. The locale
    is what makes either a test — neither reproduces under `LC_ALL=C`.
    """
    locale = _a_multibyte_locale(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    # Bytes that begin no character: a stray continuation byte, two that are
    # never valid UTF-8 at all, and a 4-byte lead cut short.
    soup = b"\x9f\x2c\xb8\xad\xf0\x9f\x80\xfe\xfe\xf0\x98\x80"
    want = b"/p/.loci/contract.yaml"
    # 4073 puts the soup astride the first window edge at the shipped 4096.
    docs = [b'{"c":"' + b"X" * 4073 + soup + b"X" * tail
            + b'", "file_path": "' + want + b'"}'
            for tail in (0, 1, 200, 4_096, 9_000)]
    out = _bash(SWEEP_DRIVER, [":", "file_path"], tmp_path,
                stdin=b"".join(d + b"\0" for d in docs),
                env={"LANG": locale, "LC_ALL": locale,
                     "LOCI_JSON_MAX": "65536"})
    lines = out.read_bytes().splitlines()
    got = [(int(rc), val) for rc, _, val in (ln.partition(b"\t") for ln in lines)]
    assert got == [(0, want)] * len(docs), got


def test_a_key_whose_opening_quote_closed_the_one_before_is_still_found(tmp_path):
    """F14's review round: the search resumed one character too far.

    A key name cannot hold an unescaped quote, so the only way two occurrences
    of `"<key>"` can overlap is the closing quote of one doubling as the
    opening quote of the next — `…"cwd"cwd" : 1`. The candidate that runs off a
    window's end is resolved against the document, and resuming PAST it skipped
    the second occurrence, which is a real `"<key>" :`. It resumes one
    character back now, which is the most an overlap can be.

    Malformed JSON, and the walk this replaced missed the shape too — for its
    own reason, so this is not a regression. It is here because the comment
    beside that line used to justify only that the loop makes progress, which
    is a weaker claim than the one the code needs to support.
    """
    docs = ['{"p": "%s", "a": "x"cwd"cwd" : "hit"}' % ("x" * pad)
            for pad in range(40)]
    got = _sweep(docs, "cwd", tmp_path, setup="_LOCI_JSON_WINDOW=16")
    bad = [i for i, (rc, v) in enumerate(got) if rc != 0 or v != "hit"]
    assert not bad, f"the overlapping key was missed at offsets {bad[:10]}"
@pytest.mark.parametrize("document,key,want,expect", [
    ('{"op":"add"op":"add"}', "op", "add", 2),
    ('{"op":"add"op":"add"op":"add"}', "op", "add", 3),
    ('{"op":"op":"op":"op"}', "op", "op", 3),
    ('{"op":"o"op":"o"}', "op", "o", 2),
    ('{"a":"a"a":"a"}', "a", "a", 1),
    # …and the ordinary documents, so a fix that routed EVERYTHING to the walk
    # would still have to answer these and could not hide here.
    ('{"o":[{"op": "add"}, {"op": "add"}]}', "op", "add", 2),
    ('{"o":[{"op":"add"},{"op":"edit"}]}', "op", "add", 1),
    ('{"o":[{"op":"added"}]}', "op", "add", 0),
])
def test_the_fast_count_and_its_walk_agree_on_an_overlapping_literal(
        tmp_path, document, key, want, expect):
    """`loci_json_count` has two paths and they must answer the same thing.

    The fast path counts by deleting a fixed-length literal and dividing the
    length that went with it. `"op":"add"` begins and ends with a quote, so it
    can OVERLAP ITSELF — `{"op":"add"op":"add"}`, where one `"` both closes a
    value and opens the next name — and `${doc//…}` deletes non-overlapping
    matches left to right, counting 1 where the walk counts 2. The pair can sit
    any BORDER of the literal apart, not just one character: `"op":"op"` has one
    at 5, which a one-character test missed.

    Every row is malformed JSON, which is the point rather than an excuse — a
    hook reads untrusted bytes, not a guaranteed document. The `expect` column
    is what the walk answers, and the walk is the older of the two paths.
    """
    out = _bash(COUNT_PAIR_DRIVER, [key, want, ":"], tmp_path,
                stdin=document.encode("utf-8") + b"\0")
    fast, walk = out.read_text(encoding="utf-8").split()
    assert (int(fast), int(walk)) == (expect, expect)


# ---------------------------------------------------------------------------
# F17 — a name spelled in escapes is a name
# ---------------------------------------------------------------------------

def _spell(name: str, idx, *, upper: bool = False) -> str:
    """`name` with the characters at `idx` written as an escape.

    Built rather than written for the reason `B` exists, and used rather than
    `json.dumps`: no serializer produces one of these for an ASCII letter, in a
    name any more than in a value, which is the reachability argument for this
    whole family stated as code.
    """
    fmt = "u%04X" if upper else "u%04x"
    return "".join(B + fmt % ord(c) if i in idx else c
                   for i, c in enumerate(name))


#: The ways `file_path` can be spelled that `_loci_json_seek_win` cannot see.
#:
#: ⚠ AN `upper=True` ROW IS ONLY A ROW WHERE THE HEX PAIR HOLDS A LETTER. `l`
#: (0x6c) and `_` (0x5f) are the only two characters of `file_path` that have a
#: second spelling at all; ask for an uppercase `f` (0x66) and you get the same
#: six characters as the lowercase row, which is a duplicate reading as
#: coverage. This list held one of those until
#: `test_an_uppercase_row_is_a_different_payload` was written.
NAME_SPELLINGS = [
    ((0,), False, "the first character"),
    ((8,), False, "the last character"),
    ((4,), False, "the underscore"),
    ((4,), True, "the underscore, uppercase hex"),
    ((2,), False, "a character whose hex pair holds a letter"),
    ((2,), True, "the same character, uppercase hex"),
    ((2, 4), True, "both of them, uppercase hex"),
    ((1, 3, 5, 7), False, "every other character"),
    (tuple(range(9)), False, "every character"),
]


def test_an_uppercase_row_is_a_different_payload():
    """The guard on the list above, because the failure is SILENT — a row that
    escapes no character with a letter in its hex pair is the lowercase row
    again, and it reads as coverage of the uppercase arm."""
    for idx, upper, what in NAME_SPELLINGS:
        if not upper:
            continue
        assert _spell("file_path", idx, upper=True) != _spell("file_path", idx), (
            f"the {what!r} row escapes no character whose hex pair holds a "
            f"letter, so it is the lowercase row again")


@pytest.mark.parametrize("idx,upper,what", NAME_SPELLINGS)
def test_a_name_spelled_in_escapes_is_the_name_it_spells(tmp_path, idx, upper,
                                                         what):
    """F17: an escaped `file_path` IS `file_path`, and the search said ABSENT.

    `json.loads` is the oracle and it is asserted on every row rather than
    assumed — a row whose key is not really `file_path` would make the read
    below it a test of nothing. The harm is one layer up: an absent `file_path`
    means `contract-guard.sh`'s route 1 never runs, so a write to the guarded
    file is ALLOWED.
    """
    doc = ('{"tool_name": "Edit", "%s": "/c/p/x.c", "after": 1}'
           % _spell("file_path", idx, upper=upper))
    assert json.loads(doc)["file_path"] == "/c/p/x.c", "the row is not the key"
    rc, got = _read(doc, "file_path", tmp_path)
    assert rc == 0 and got == b"/c/p/x.c", f"the name with {what} was missed"


@pytest.mark.parametrize("idx,upper,what", NAME_SPELLINGS)
def test_an_escaped_name_that_is_not_a_key_is_still_not_a_key(tmp_path, idx,
                                                              upper, what):
    """The property the first pass has, and the second must not lose.

    The colon is what makes a name a key, and widening what MATCHES the name is
    exactly the change that could start reading an array element as one. Both
    halves, as the literal sweeps above test them: the escaped element must not
    shadow the real key behind it, and on its own it must not answer at all.
    """
    spelled = _spell("file_path", idx, upper=upper)
    shadow = '{"a": ["%s"], "file_path": "/real"}' % spelled
    assert json.loads(shadow)["file_path"] == "/real"
    rc, got = _read(shadow, "file_path", tmp_path)
    assert rc == 0 and got == b"/real", (
        f"an escaped element with {what} shadowed the key")

    alone = '{"a": ["%s"], "b": 1}' % spelled
    assert "file_path" not in json.loads(alone)
    rc, got = _read(alone, "file_path", tmp_path)
    assert rc != 0 and got == b"", (
        f"an escaped element with {what} answered as a key")


def _oracle(document: str, key: str):
    """The first `key` at ANY depth, which is the contract this library states.

    `json.loads(doc)[key]` is the oracle everywhere else in this file and it is
    the wrong one here: `loci_json_get` reads a NAMED KEY and not a path, so a
    row that nests the key one object down has to be resolved the same way or
    the row is testing the test.
    """
    def walk(o):
        if isinstance(o, dict):
            if key in o:
                return o[key]
            for v in o.values():
                got = walk(v)
                if got is not None:
                    return got
        elif isinstance(o, list):
            for v in o:
                got = walk(v)
                if got is not None:
                    return got
        return None
    return walk(json.loads(document))


@pytest.mark.parametrize("document", [
    '{"%s"  : "/c/p"}',
    '{"%s"\n\t: "/c/p"}',
    '{"%s":"/c/p"}',
    '{"a": {"%s": "/c/p"}}',
    '{"tool_name": "file_path", "%s": "/c/p"}',
    '{"a": ["file_path", "file_path"], "%s": "/c/p"}',
])
def test_the_colon_still_makes_an_escaped_name_a_key(tmp_path, document):
    """Everything `test_the_colon_is_what_makes_a_name_a_key` pins, escaped.

    The second pass runs the FIRST pass over a rewritten document, so these
    cannot diverge by construction — which is the argument for the shape, and
    this is what says the argument holds rather than leaving it in a comment.
    """
    doc = document % _spell("file_path", (0,))
    assert _oracle(doc, "file_path") == "/c/p", "the row is not the key"
    rc, got = _read(doc, "file_path", tmp_path)
    assert rc == 0 and got == b"/c/p"


def test_a_literal_name_is_preferred_to_an_escaped_one(tmp_path):
    """The duplicate-name decision, pinned rather than left to the search.

    Two keys spelling one name is a duplicate name; RFC 8259 leaves the choice
    to the parser and `json.loads` and `JSON.parse` both take the LAST. This
    library takes **the literal spelling wherever it sits, and an escaped one
    only when no literal is used as a key anywhere** — which agrees with both
    parsers when the escaped one comes first, and disagrees when it comes
    second. Preferring the FIRST in document order would disagree in both, so
    this is the closer rule as well as the cheaper one.

    Asserted in both orders, and against `json.loads` in both, so the
    divergence is visible here rather than discovered in a guard.
    """
    esc = _spell("file_path", (0,))
    first_literal = '{"file_path": "L", "%s": "E"}' % esc
    first_escaped = '{"%s": "E", "file_path": "L"}' % esc

    assert json.loads(first_literal)["file_path"] == "E"   # last wins, there
    assert json.loads(first_escaped)["file_path"] == "L"

    for doc, order in ((first_literal, "literal first"),
                       (first_escaped, "escaped first")):
        rc, got = _read(doc, "file_path", tmp_path)
        assert rc == 0 and got == b"L", f"{order}: the literal must win"


@pytest.mark.parametrize("document,key,first,last", [
    ('{"file_path": "A", "file_path": "B"}', "file_path", b"A", "B"),
    ('{"cwd": "X", "a": 1, "cwd": "Y"}', "cwd", b"X", "Y"),
])
def test_two_literal_spellings_of_one_name_still_answer_with_the_first(
        tmp_path, document, key, first, last):
    """The rule, and since F19 it is a decision rather than a residue.

    This library answers with the FIRST use of a name as a key; `json.loads`
    and `JSON.parse` answer with the LAST. F19 was filed to close that and
    closed it the other way round: the divergence STAYS here, because these
    read a name at any DEPTH and taking the last would break the documents
    these actually read (`test_the_envelope_nesting_is_why_the_first_key_wins`
    below is that measurement), and the one caller whose verdict turns on it —
    `contract-guard.sh` — asks `loci_json_dup` and refuses to decide instead.

    So this is no longer "flip it when the fix lands". Changing it means
    re-opening F19 with an answer to the nesting measurement.
    """
    assert json.loads(document)[key] == last
    rc, got = _read(document, key, tmp_path)
    assert rc == 0 and got == first


def test_the_envelope_nesting_is_why_the_first_key_wins(tmp_path):
    """F19's counter-measurement, kept so the obvious fix is not tried twice.

    Taking the LAST key looks like agreement with `json.loads` and is not: it
    is agreement for two keys in ONE OBJECT and the opposite for a key one
    level down, because these read a NAME AT ANY DEPTH and a parser reads a
    PATH. Which matters, because the CLI's envelope is
    `{"ok":…,"data":{…}}` *precisely* so a result field "can never collide with
    `ok`/`error`" (`src/loci/cli/_json.py`) — and first-wins is what delivers
    that to a depth-blind reader.

    The document below is the shape `loci contract draft show` really emits:
    `ops[].entry` is a MODEL-AUTHORED object whose unknown fields the CLI
    preserves verbatim (`contract.py`, `render_entry`). An entry naming its own
    `ok` sits AFTER the envelope's `ok`. `draft-pending-nudge.sh` reads
    `[ "$(loci_json_get ok)" = "true" ] || exit 0`, so under last-wins the
    nudge exited silently — measured on a branch that made that change, and the
    reason the change was reverted.
    """
    envelope = json.dumps({
        "ok": True,
        "data": {
            "draft": "/c/p/.loci/contract.draft.yaml",
            "pending": 1,
            "ops": [{"op": "add", "index": 0,
                     "entry": {"text": "t", "kind": "budget",
                               "ok": "HIJACK", "pending": "HIJACK"}}],
            "stale": False,
        },
    })
    for key, want in (("ok", b"true"), ("pending", b"1")):
        rc, got = _read(envelope, key, tmp_path)
        assert rc == 0 and got == want, (
            f"`{key}` answered {got!r}: a nested, model-authored field is "
            f"beating the envelope's own, which is what the `data` nesting "
            f"exists to prevent. If the search now takes the last key, "
            f"`draft-pending-nudge.sh` exits silently on this document.")


@pytest.mark.parametrize("document,dup,what", [
    ('{"file_path": "A", "file_path": "B"}', True, "two literal keys"),
    ('{"file_path": "A"}', False, "one key"),
    ('{"a": 1}', False, "the name absent"),
    ('{"a": ["file_path", "file_path"], "file_path": "B"}', False,
     "twice as an array ELEMENT, once as a key"),
    ('{"file_path": "A", "b": {"file_path": "B"}}', True, "one of them nested"),
    ('{"file_path": "A", "file_path" : "B"}', True, "whitespace before a colon"),
    ('{"file_path": "A", "file_path": "B", "file_path": "C"}', True, "three"),
])
def test_the_duplicate_detector_counts_keys_and_not_occurrences(
        tmp_path, document, dup, what):
    """`loci_json_dup` is what `contract-guard.sh` refuses on, so both its
    directions are load-bearing.

    Saying YES where the name merely APPEARS twice would refuse an ordinary
    payload — F14's own shape, a model-controlled array of strings, is row
    four — and saying NO on a real duplicate is the fail-open F19 was filed
    for. The colon is what makes a name a key, here as everywhere else in this
    file.
    """
    rc = _dup(document, "file_path", tmp_path)
    assert (rc == 0) is dup, f"{what}: loci_json_dup answered {rc == 0}"


@pytest.mark.parametrize("idx,upper,what", NAME_SPELLINGS)
def test_the_duplicate_detector_sees_an_escaped_second_spelling(
        tmp_path, idx, upper, what):
    r"""F17 and F19 composed, which is the half `loci_json_count` cannot do.

    `loci_json_count` reads the literal name only — deliberately, and its own
    comment says so — so a duplicate whose second key is spelled `\uXXXX` is
    invisible to the tally alone. `loci_json_dup` runs the tally over the copy
    the escaped SEARCH reads, so the two agree on what an escaped name is by
    construction. In `contract-guard.sh` this row was ALLOW at `4fbbc2a`, with
    the harness writing the guarded file.
    """
    spelled = _spell("file_path", idx, upper=upper)
    doc = '{"file_path": "A", "%s": "B"}' % spelled
    assert json.loads(doc)["file_path"] == "B", "the row is not a duplicate"
    assert _dup(doc, "file_path", tmp_path) == 0, (
        f"a duplicate with {what} escaped was not seen")

    alone = '{"%s": "B"}' % spelled
    assert _dup(alone, "file_path", tmp_path) != 0, (
        f"one escaped key with {what} was called a duplicate")


@pytest.mark.parametrize("kind,what", [
    ("bs", "escaped backslashes and one escape behind a pair — the claim"),
    ("elements", "the name 8 000 times as an array element, one with a newline"),
])
def test_the_duplicate_detector_answers_inside_the_hook_budget(
        tmp_path, kind, what):
    r"""The cost, because `contract-guard.sh` asks this twice per payload.

    `hooks.json` gives that hook 5 s and past it it is KILLED and PreToolUse
    fails OPEN, so a cost defect here is a fail-open exactly as F13 and F14
    were. Two shapes:

    * the one the rewrite exists for — a document carrying `\u00` behind
      escaped backslashes, which makes the claim run;
    * **the one that rebuilt this function.** It is F14's array of strings with
      ONE whitespace byte after an element, which is what drops
      `loci_json_count` out of its arithmetic and into a quadratic walk
      (`case "$doc" in *"$key"[ws]*`). The detector asked the tally at first,
      and that shape took `contract-guard.sh` from 0.109 s to **3.846 s**
      against a 5 s kill — a worse hole than the one the screen closes, and
      `json.dumps(indent=2)` reaches it. It asks `_loci_json_seek_win` twice
      now, which is the LINEAR search F14 bought.

    Measured here at 0.36 s and 0.30 s for one call including bash's startup.
    A document with no `\u00` and no repetition — every payload a serializer
    produces — is one glob.
    """
    locale = _a_multibyte_locale(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    if kind == "bs":
        doc = _no_literal("bs").replace('"cwd": "/c/p"', '"file_path": "/c/p"')
    else:
        doc = ('{"file_path": "/c/p", "a": [%s"file_path"\n]}'
               % ('"file_path",' * 8_000))
    start = time.monotonic()
    rc = _dup(doc, "file_path", tmp_path,
              env={"LOCI_JSON_MAX": "65536", "LANG": locale, "LC_ALL": locale})
    elapsed = time.monotonic() - start
    assert rc != 0, f"{what}: one key is not a duplicate"
    assert elapsed < 2.5, (
        f"one duplicate check over a 64 KB document with {what} took "
        f"{elapsed:.1f}s; the guard makes two of them and has 5 s")


def test_the_names_the_guard_screens_for_duplicates_are_ones_it_can_spell():
    """The enumeration `test_every_name_the_hooks_read_is_one_the_escaped_pass
    _can_see` cannot make, because the guard passes a loop VARIABLE.

    `loci_json_dup` goes through `_loci_json_rewrite`, which DECLINES a name
    outside `[A-Za-z0-9_]` — declining means "no escaped spelling was looked
    for", which is the old behaviour and therefore silent. The names are a
    literal list in `contract-guard.sh`; this reads that list rather than
    trusting it.
    """
    guard = (PLUGIN_ROOT / "hooks" / "contract-guard.sh").read_text(encoding="utf-8")
    m = re.search(r"for _cg_dup in ([^;\n]+); do", guard)
    assert m, "the duplicate screen's name list moved — this lint is now blind"
    names = m.group(1).split()
    assert names == ["file_path", "command"], (
        f"{names}: the screened list changed. `cwd` is out deliberately — the "
        f"harness writes it at TOP LEVEL, so a tool_input carrying its own is "
        f"a nested duplicate of nothing the model did; see "
        f"`test_a_tool_input_may_carry_its_own_cwd` in test_contract_guard.py.")
    for name in names:
        assert re.fullmatch(r"[A-Za-z0-9_]+", name), (
            f"`{name}` is outside the alphabet `_loci_json_rewrite` accepts, so "
            f"an escaped duplicate of it would read as no duplicate at all")


def test_an_escaped_backslash_does_not_spell_a_name(tmp_path):
    """An escaped backslash before `u0066ile_path` does NOT make `file_path`.

    It is a backslash followed by the LETTERS `u0066ile_path`, so `json.loads`
    reads the name with the escape still in it and there is no `file_path` key
    at all. Decoding the escape sitting inside it would invent one — the same
    misreading `_loci_json_unwrap` claims the escaped backslash first to avoid,
    one layer down, and the reason the pass above it claims it too.

    Both directions: on its own the name must be ABSENT, and beside a real key
    it must not shadow it.
    """
    spelled = B + B + "u0066ile_path"
    alone = '{"%s": "X", "b": 1}' % spelled
    assert "file_path" not in json.loads(alone), "the row is not what it claims"
    rc, got = _read(alone, "file_path", tmp_path)
    assert rc != 0 and got == b"", "an escaped backslash was read as an escape"

    beside = '{"%s": "X", "file_path": "/real"}' % spelled
    rc, got = _read(beside, "file_path", tmp_path)
    assert rc == 0 and got == b"/real"


@pytest.mark.parametrize("body", [
    # ⚠ THE FIRST ROW IS THE CLAIM'S ONLY DETECTOR, and it is the one the first
    # draft of this list did not have. The escape buried behind the escaped
    # backslash has to spell a character of the NAME BEING SOUGHT — `t` is
    # 0x74 and the name here is `target` — or no substitution touches it and
    # dropping the claim changes nothing. Measured: with the claim this reads
    # back as written; without it, as `C:` and a TAB.
    "C:" + B + B + "u0074oo",        # an escaped backslash, then the letters
    "C:" + B + B + "u0066oo",        # the same, spelling a character it does not seek
    "C:" + B + B + B + "u0074oo",    # …and a real escape right after it
    "a" + B + "u0066b",              # an escape the rewrite also decodes
    B + "u00e9t" + B + "u00e9",      # escapes it does not
    B + B,                           # a lone escaped backslash
    "x" + B + '"' + "y",             # an escaped quote
    "tab" + B + "there",
    B + "u0022",                     # a quote as an escape: must NOT pre-decode
    B + "u005C",                     # a backslash as an escape: nor this
    B + "ud83d" + B + "ude00",       # a surrogate pair
])
def test_the_rewrite_does_not_change_the_value_it_returns(tmp_path, body):
    """The second pass hands back a slice of a REWRITTEN document.

    So the value a caller gets has been through the rewrite too, and the claim
    this rests on is that every difference is an escape `_loci_json_unwrap`
    would have decoded to the same character anyway. The two it must not
    pre-decode are a quote and a backslash — one would forge a string boundary,
    the other would start an escape — and they are exactly the two a name of
    `[A-Za-z0-9_]` cannot contain, so the rewrite never looks for them. These
    rows are that argument checked against `json.loads` rather than asserted.
    """
    doc = '{"before": "b", "%s": "%s", "after": "a"}' % (
        _spell("target", (0,)), body)
    want = json.loads(doc)["target"]
    rc, got = _read(doc, "target", tmp_path)
    assert rc == 0, "the escaped name was not found"
    assert got.decode("utf-8") == want, f"{body!r} came back changed"


def test_a_raw_sentinel_pair_survives_only_a_document_that_misses_the_claim(
        tmp_path):
    """The claim parks escaped backslashes on a PAIR OF SOH BYTES, so a
    document that trips it and carries such a pair of its own gets it rewritten.

    Both halves, because the first version of this test pinned only the shut
    one and read as proof of the open one:

    * **no escaped backslash before an escape** — the claim never runs, the
      undo never runs, and the same value under an escaped name and a literal
      one comes back identically. That is the gating.
    * **an escaped backslash before an escape anywhere in the document** — the
      claim runs over the WHOLE document, and the pair comes back as two
      backslashes. `_loci_json_unwrap` reserves SOH too, but per VALUE and only
      when that value holds a backslash, so its note does not cover this; this
      is a second, narrower place, and the comment at `_loci_json_seek_escaped`
      says so.

    The over-rewrite names no file: JSON forbids a raw control character inside
    a string, so `json.loads` and `JSON.parse` both REJECT this document and no
    tool call is made from it. Recorded rather than closed — a sentinel has to
    be some byte, and this one is already the file's.
    """
    soh = chr(1) * 2
    esc_name = _spell("target", (0,))

    shut = '{"%s": "%sx"}' % (esc_name, soh)
    literal = '{"target": "%sx"}' % soh
    assert _read(shut, "target", tmp_path) == _read(literal, "target", tmp_path)
    assert _read(shut, "target", tmp_path)[1] == soh.encode("ascii") + b"x"

    # `q\\u0041` is an escaped backslash followed by `u0041`, which is what
    # trips the claim; it is in a different field, so nothing else changes.
    trips = '{"z": "q%s%su0041", "%s": "%sx"}' % (B, B, esc_name, soh)
    rc, got = _read(trips, "target", tmp_path)
    assert rc == 0
    assert got == (B + "x").encode("ascii"), (
        f"expected the claim's undo to rewrite the raw pair, got {got!r} — if "
        f"this is now the two SOH bytes the over-rewrite is closed and the "
        f"comment at `_loci_json_seek_escaped` should say so")
    assert json.loads(trips.replace(chr(1), "")), "the row must be JSON but for the SOH"
    with pytest.raises(json.JSONDecodeError):
        json.loads(trips)


def test_the_second_pass_decodes_past_the_cap_the_first_one_stops_at(tmp_path):
    """`_LOCI_JSON_UMAX` meters `_loci_json_unicode`; the rewrite is not metered.

    So one value reads two ways depending on how its KEY was spelled: under a
    literal name the cap stops the decode part-way and hands the rest back as
    written, and under an escaped name the rewrite has already done all of them
    for free. Measured at the guard's own cap with 400 escapes in a 64 KB
    value: 97 decoded under the literal key, 400 under the escaped one.

    The direction is toward MORE decoding — more matches, more denies — and the
    cap is a documented ALLOW door, so this widens it in the safe direction.
    Pinned because both numbers are tunable and a future change to either
    should see that they are coupled.
    """
    val = (B + "u0063") * 400 + "y" * 60_000
    lit = '{"command": "%s"}' % val
    esc = '{"%s": "%s"}' % (_spell("command", (0,)), val)
    guard_cap = {"LOCI_JSON_MAX": "65536"}

    rc_l, got_l = _read(lit, "command", tmp_path, env=guard_cap)
    rc_e, got_e = _read(esc, "command", tmp_path, env=guard_cap)
    assert rc_l == 0 and rc_e == 0
    n_l = got_l.count(b"c") - got_l.count(B.encode() + b"u0063")
    n_e = got_e.count(b"c")
    assert n_e == 400, f"the escaped spelling decoded {n_e} of 400"
    assert n_l < n_e, (
        f"the literal spelling decoded {n_l} of 400 and the escaped one "
        f"{n_e} — if these now agree, `_LOCI_JSON_UMAX` no longer bites here "
        f"and the note at `_loci_json_seek_escaped` should go")


@pytest.mark.parametrize("name,doc_key,what", [
    # BUILT, not written: a non-raw literal holding the escape is decoded by
    # PYTHON, and the document then carries the character instead of the six
    # characters. That made this test green against a library with the
    # restriction deleted — see `test_no_literal_here_hides_an_escape_python_ate`.
    (chr(0xe9), B + "u00c3" + B + "u00a9",
     "a non-ASCII name against a byte-wise table"),
])
def test_a_name_outside_the_alphabet_is_declined_rather_than_answered_wrongly(
        tmp_path, name, doc_key, what):
    r"""The `[A-Za-z0-9_]` restriction, with the wrong answer it prevents.

    The escape table is built with `printf '%02x'`, which reads a BYTE. For a
    non-ASCII name that is not just useless, it is WRONG IN THE ANSWERING
    DIRECTION: `é` is the two bytes C3 A9, so a byte-wise table looks for the
    escapes of C3 and A9 — and `{"\u00c3\u00a9": "v"}` is the two-character name
    `Ã©`, whose own UTF-8 is four bytes. Without the restriction the pass finds
    `é` in a document that does not contain it and answers `v`; `json.loads`
    has no such key. Measured both ways.

    So the restriction is not tidiness, and this is the test that says so —
    without it, deleting the `case` is green on every other assertion here.
    """
    doc = '{"%s": "v", "z": 1}' % doc_key
    assert name not in json.loads(doc), "the row is not what it claims"
    rc, got = _read(doc, name, tmp_path)
    assert rc != 0 and got == b"", (
        f"{what}: the pass answered {got!r} for a name the document does not "
        f"contain — the `[A-Za-z0-9_]` restriction is gone")


def test_an_escaped_name_may_straddle_a_window_edge(tmp_path):
    """The second pass runs the windowed search, so it inherits its edges.

    Inherits, not re-implements — but a rewritten document is a DIFFERENT
    document, five characters shorter per escape, so every alignment the
    literal sweep walks is a different alignment here. Swept the same way, one
    byte at a time across three windows.
    """
    spelled = _spell("cwd", (0,))
    docs = ['{"p": "%s", "%s": "hit"}' % ("x" * pad, spelled)
            for pad in range(200)]
    got = _sweep(docs, "cwd", tmp_path, setup="_LOCI_JSON_WINDOW=64")
    bad = [i for i, (rc, v) in enumerate(got) if rc != 0 or v != "hit"]
    assert not bad, f"the escaped name was missed at offsets {bad[:10]} of 200"


@pytest.mark.parametrize("name", ["a.b", "a*b", "a-b", "a b", "ключ"])
def test_a_name_the_escaped_pass_cannot_spell_is_left_to_the_first(tmp_path,
                                                                   name):
    """The restriction to `[A-Za-z0-9_]`, asserted where it bites.

    The escape table is built with `printf '%02x'`, which reads a BYTE, so a
    non-ASCII name would be escaped wrong; a name holding a quote or a
    backslash can be spelled with a SHORT escape this does not look for; a name
    holding a glob metacharacter would be a pattern and not a literal. The
    second pass declines those names rather than answering them badly — and the
    LITERAL spelling of each must still be found, which is what says the
    decline is a decline and not a break.

    Every document here carries an escape, so the decline happens BELOW the
    gate rather than at it; without that these would pass for the wrong reason.
    """
    doc = '{"z": "%s", "%s": "/c/p"}' % (B + "u00e9", name)
    assert json.loads(doc)[name] == "/c/p"
    rc, got = _read(doc, name, tmp_path)
    assert rc == 0 and got == b"/c/p", f"the literal name {name!r} was lost"


#: Every shell call in this repository that asks the library for a named field.
_FIELD_READ_RE = re.compile(
    r"\b(?:loci_json_get|loci_json_has|loci_json_kind|loci_json_count"
    r"|loci_json_array_len|_loci_ctx_field)\s+"
    r"""(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_$]+))""")


def test_every_name_the_hooks_read_is_one_the_escaped_pass_can_see():
    """The restriction above is only safe while every caller stays inside it.

    A name outside `[A-Za-z0-9_]` is DECLINED by the second pass, which is the
    old behaviour — an escaped spelling of it reads as ABSENT — so adding one
    would be a silent hole rather than a loud failure. This is the enumeration
    that stops that happening quietly: every literal name asked for anywhere in
    `hooks/` or `lib/` is checked, and the one call site that passes a VARIABLE
    (`_loci_ctx_field`, which forwards its own `$1`) is covered because its
    callers are literal and are scanned here too.
    """
    names, dynamic = set(), []
    for path in sorted(list((PLUGIN_ROOT / "hooks").glob("*.sh"))
                       + list((PLUGIN_ROOT / "lib").glob("*.sh"))):
        if path.name == "loci_json.sh":
            continue
        for m in _FIELD_READ_RE.finditer(path.read_text(encoding="utf-8")):
            arg = m.group(1) or m.group(2) or m.group(3) or ""
            if arg.startswith("$"):
                dynamic.append(f"{path.name}: {arg}")
            elif arg:
                names.add(arg)
    assert len(names) >= 20, f"the scan found only {sorted(names)} — it broke"
    for expected in ("file_path", "command", "cwd", "hook_event_name"):
        assert expected in names, f"{expected} is read and the scan missed it"
    bad = sorted(n for n in names if not re.fullmatch(r"[A-Za-z0-9_]+", n))
    assert not bad, (
        f"these field names cannot be seen by the escaped-name pass in "
        f"`lib/loci_json.sh`, so an escaped spelling of one reads as ABSENT: "
        f"{bad}. Either rename the field or widen `_loci_json_seek_escaped`.")
    assert dynamic == ["setup-steps.sh: $_key"], (
        f"a new call site passes a variable name, so this enumeration no "
        f"longer covers every read: {dynamic}")


# ── the cost of the second pass (F17) ──────────────────────────────────────

def _no_literal(kind: str) -> str:
    """64 KB with no `"file_path"` in it — the document that pays for pass 2."""
    pad = {
        # no escape at all: the four-character gate answers, nothing else runs
        "plain": "x" * 64_000,
        # one escape: the gate admits and the rewrite finds almost nothing.
        # Padded to the same length as "plain" — the ratio below is a control
        # for SIZE, so a document 14 bytes shorter is not one.
        "one": "x" * (64_000 - 6) + B + "u00e9",
        # 10 600 of the one escape that spells a character of the name
        "uheavy": (B + "u0066") * 10_600,
        # the claim's worst case: 31 900 escaped backslashes, and one escape
        # sitting behind a pair, which is what makes the claim run at all
        "bs": (B + B) + (B + "u0066") + (B + B) * 31_900,
    }[kind]
    return '{"a": "%s", "cwd": "/c/p"}' % pad


#: N reads of one name over one document, in ONE bash process. Per-process
#: timing cannot see this: a read that should cost a glob costs 0.0003 s and
#: bash's startup is 0.09 s, so the signal is 0.3% of the measurement.
NREADS_DRIVER = """
. "$1"
eval "$5"
IFS= read -r -d '' doc || true
loci_json_load "$doc"
i=0
while [ "$i" -lt "$4" ]; do loci_json_get "$3" > /dev/null || true; i=$((i+1)); done
printf done > "$2"
"""


def test_answering_absent_over_a_document_with_no_escape_costs_a_glob(tmp_path):
    """F17's own cost test: the read that now takes a SECOND pass.

    `file_path` is absent from every Bash payload `contract-guard.sh` reads, so
    this is not a corner — it is that hook's hot path, and the second pass is
    ENTERED on all of it. What keeps it free is the four-character gate: one
    glob, and a payload with no escape in it never reaches the rewrite.

    ⚠ THE SHAPE IS A SLOPE, and the first version of this test was a ratio
    between two documents that both take the same path once the gate is gone —
    it could not fail. Timing ONE process cannot see this either: the signal is
    0.0003 s against bash's 0.09 s of startup. Two runs over the SAME document
    differing only in how many times it reads cancels startup, stdin and
    `loci_json_load`, and what is left is the per-read cost.

    Measured at the guard's 64 KB cap: **0.78 ms a read** with the gate and
    **7.65 ms** with the gate deleted, stable across runs. 3 ms is the line —
    3.8x of headroom below it and 2.5x above.
    """
    doc = '{"a": "%s", "cwd": "/c/p"}' % ("x" * 64_000)
    guard_cap = {"LOCI_JSON_MAX": "65536"}

    def run(n):
        best = None
        for _ in range(3):
            t0 = time.monotonic()
            out = _bash(NREADS_DRIVER, ["file_path", str(n), ":"], tmp_path,
                        stdin=doc.encode("utf-8") + b"\0", env=guard_cap)
            assert out.read_text(encoding="utf-8") == "done"
            dt = time.monotonic() - t0
            best = dt if best is None else min(best, dt)
        return best

    few, many = run(4), run(104)
    per_read_ms = (many - few) / 100 * 1000
    assert per_read_ms < 3.0, (
        f"answering ABSENT over a 64 KB document with no escape in it costs "
        f"{per_read_ms:.2f} ms a read; the four-character gate should make it "
        f"one glob, so the second pass is running the rewrite on the contract "
        f"guard's hot path")


@pytest.mark.parametrize("kind,what", [
    ("uheavy", "64 KB of the escape that spells one character of the name"),
    ("bs", "31 900 escaped backslashes and one escape behind a pair"),
])
def test_the_second_pass_answers_absent_inside_the_hook_budget(tmp_path, kind,
                                                              what):
    """The wall clock, because a ratio alone is not it.

    `hooks.json` gives `contract-guard.sh` 5 s and past it the hook is KILLED
    and `PreToolUse` fails OPEN — the write goes through — so a cost defect
    here is a fail-open exactly as F13 and F14 were. These are the two shapes
    that reach the rewrite: a document that is nothing but the escape, and the
    one that also makes the backslash claim run, which is the expensive half
    (0.606 s to claim and 0.602 s to put back at this cap, against 0.011 s each
    with no pairs to claim).

    Measured here at 0.13 s and 0.31 s including bash's startup, against
    0.09 s for the same read at `dea825d`. 2.5 s is the line this file already
    draws.
    """
    locale = _a_multibyte_locale(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    start = time.monotonic()
    rc, got = _read(_no_literal(kind), "file_path", tmp_path,
                    env={"LOCI_JSON_MAX": "65536",
                         "LANG": locale, "LC_ALL": locale})
    elapsed = time.monotonic() - start
    assert rc != 0 and got == b""
    assert elapsed < 2.5, (
        f"answering ABSENT over a 64 KB document with {what} took "
        f"{elapsed:.1f}s; the whole contract guard has 5 s before it is killed "
        f"and fails open")


#: Every distinct name `post-edit-hook.sh` reads off one payload.
TWELVE_READS_DRIVER = """
. "$1"
eval "$3"
IFS= read -r -d '' doc || true
loci_json_load "$doc"
for k in file_path prompt_id agent_id cwd hook_event_name error applied \\
         measurable measure_via turn agent init_status; do
    loci_json_get "$k" > /dev/null || true
done
printf 'done' > "$2"
"""


def test_twelve_absent_names_over_one_document_stay_inside_the_edge_budget(tmp_path):
    """The rewrite is per NAME, and nothing caches across names or across reads.

    Nothing caches across READS either: every field read in `hooks/` and `lib/`
    but one is a command substitution, so a cache built inside `$( )` would die
    with the subshell. The bound is therefore (absent names) x (one rewrite),
    and the only thing keeping it affordable is WHICH CAP each hook reads
    under. `post-edit-hook.sh` reads the twelve names below and gets 5 s from
    `hooks.json` — not the 8 s an earlier draft of this docstring claimed,
    which is `pre-edit-hook.sh`'s — and it reads at the 16 KB DEFAULT.

    Measured over the worst document this file can build: **+0.265 s** at
    16 KB, and +2.781 s at 64 KB. The second combination is not reachable —
    `contract-guard.sh` is the only caller that raises `LOCI_JSON_MAX` and it
    reads three names — and that, not a margin, is what makes this safe.
    Anyone raising the cap in another hook owes this measurement again.
    """
    locale = _a_multibyte_locale(tmp_path)
    if locale is None:
        pytest.skip("no multibyte locale on this host")
    start = time.monotonic()
    out = _bash(TWELVE_READS_DRIVER, [":"], tmp_path,
                stdin=_no_literal("bs").encode("utf-8") + b"\0",
                env={"LANG": locale, "LC_ALL": locale})
    elapsed = time.monotonic() - start
    assert out.read_text(encoding="utf-8") == "done"
    assert elapsed < 2.5, (
        f"twelve absent field reads over one hostile document took "
        f"{elapsed:.1f}s at the 16 KB default; `post-edit-hook.sh` has 5 s "
        f"before it is killed")


def test_no_literal_here_hides_an_escape_python_ate():
    r"""Every escape in this file must reach the SHELL, not stop at the parser.

    A non-raw Python string literal whose source holds `\uXXXX` is decoded at
    parse time, so a test that means to send the six characters of an escape
    sends the one character it names instead — and then measures a library path
    it never reached. It has happened twice in this file: `STATE_DRIVER` sent a
    raw `é` where it meant the escape, and
    `test_a_name_outside_the_alphabet_is_declined_rather_than_answered_wrongly`
    was GREEN against a library with the restriction it tests deleted.

    The rule is therefore: build an escape (`B + "u00e9"`), or mark the literal
    raw. This walks the file's own AST rather than grepping, so a docstring
    that merely TALKS about an escape is caught too — those must be raw, or
    they document something other than what they say.
    """
    import ast

    src = Path(__file__).read_text(encoding="utf-8")
    pat = re.compile(re.escape(B) + r"u[0-9A-Fa-f]{4}")
    eaten = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if pat.search(seg) and not pat.search(node.value):
            eaten.append(f"line {node.lineno}: {seg[:60]!r}")
    assert not eaten, (
        "these literals hold an escape that Python decodes at parse time, so "
        "what reaches the shell is the character and not the escape. Build it "
        "from `B`, or make the literal raw:" + "".join("\n  " + e for e in eaten))
