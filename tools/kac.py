#!/usr/bin/env python3
"""kac.py - Knowledge-as-Code kit v2.0 (2026-10-02). Python 3.9+, standard library + PyYAML.

One additive file for any wiki built with the Knowledge-as-Code guide, whatever its layout.
It wraps the tools a wiki already has. It never edits raw evidence and never rewrites git history.

  status [-n N]                   session start in one call: state, last runs, page counts, what needs attention
  doctor [--deep] [--json] [--sync NAME] [--device NAME]   read-only audit, findings ranked P0/P1/P2
  adopt [--apply]                 add the safety files (kit config, .gitattributes, manifest, pins)
  normalize [--apply]             settle files that have CRLF line endings in the folder but LF in git (only when
                                  nothing else differs): text files are rewritten, their previous copy is kept.
                                  Evidence needs a decision, because one of its two copies was converted:
                                  --raw git: rewrite the folder copies from git (a checkout converted them)
                                  --raw folder: make git store the folder's bytes, as a commit of its own
  verify [--deep] [--expect FP]   raw files vs manifest, instruction-file pins, JSONL stores
  pin                             re-pin instruction files after you reviewed their diff; prints the fingerprint
  check [--lenient] [--since REF] built-in lint, then the commands under kit.checks (--since: for CI, pages
                                  changed since REF must pass)
  begin OP TITLE                  optional: take the writer lock at the start of a run
                                  --allow-dirty: start although files are changed (abort will leave those alone)
                                  --steal: a person takes over a dead run's lock and its changes
  commit [OP TITLE] [-t TRAILER]  check, log and commit the run as ONE change unit with a Run-Id
                                  (with a lock from `begin`: plain `commit`, or `commit --run ID`)
                                  --repin: accept reviewed changes to instruction files
                                  --lenient: bulk mechanical runs; page problems only warn, safety checks stay
                                  --steal: a person commits what a dead run left behind
  abort                           set the run's changes aside in _to_delete/ and release the lock
  undo RUN_ID [--with-raw]        undo one run as a new commit, rebuild derived files, keep the log
  runs [-n N]                     list recent change units
  history PAGE [-n N]             the runs that changed one page (id or path)
  show PAGE --at REF              the page as it was at a run id, tag, commit or date (YYYY-MM-DD)
  index [--check] [--force]       regenerate index.md (flat while it fits the budget, else map + index/<type>.md);
                                  --force replaces an index.md that the kit did not write
  search QUERY [-k N] [--type T] [--state S] [--json]   ranked cards (BM25, cached outside the wiki)
  list [--type T] [--where K=V] [--count-by K] [--fields a,b] [--json]   exact answers from front matter
  eval [-k N]                     golden-question retrieval check (evals/golden.yaml)
  pack                            build the context pack for surfaces without file access
  snapshot [--name N]             tag + verified git bundle in the backup folder (KAC_BACKUP or kit.backup.dir)

Global: --root DIR (the wiki folder, used as given), --config FILE (a kac.yaml kept outside the wiki, for a wiki
the kit must not write into; also KAC_CONFIG), --version.
Exit codes: 0 ok, 1 findings or failed checks, 2 usage or environment error, 3 writer lock held.
"""
import sys

if __name__ == "__main__" and not (sys.flags.isolated or getattr(sys.flags, "safe_path", False)):
    sys.path.append(sys.path.pop(0))     # the folder this file lives in goes last: a file such as tools/json.py
                                         # must never be imported in place of a standard module (guide section 9.4)
import argparse
import bisect
import datetime as dt
import fnmatch
import hashlib
import html
import json
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("kac: PyYAML is required (pip install pyyaml)")

VERSION = "2.0"
DEFAULTS = {
    "pages": ["wiki"], "raw": "raw", "raw_exclude": ["raw/inbox/*"], "records": [],
    "instructions": ["AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md", "kac.yaml", "markings.yaml",
                     ".gitattributes",
                     ".mcp.json", "ontology/**/*", "tools/**/*", "skills/**/*", ".agents/skills/**/*",
                     ".claude/settings*.json", ".claude/rules/**/*", ".claude/skills/**/*", ".claude/hooks/**/*",
                     ".claude/agents/**/*", ".claude/commands/**/*", ".codex/config.toml", ".cursor/rules/**/*",
                     ".cursorrules", ".windsurfrules", ".clinerules", ".github/workflows/*",
                     ".github/copilot-instructions.md", ".github/instructions/**/*"],
    "unpinned": [],      # files under the pinned folders that tools rewrite during runs (state, cursors, caches)
    "derived": ["index.md", "index/*", "export/*"], "log": "log.md",
    "tz": "UTC", "tz_label": "", "agent": "agent", "checks": [], "rebuild": [],
    "budgets": {"instructions_lines": 200, "instructions_kb": 12, "index_kb": 16, "card_chars": 200, "page_kb": 12,
                "pack_kb": 60, "log_kb": 256, "start_kb": 40, "partition_entries": 250},
    "start_files": ["AGENTS.md", "CLAUDE.md", "index.md"],      # what every session reads first
    "exclude": ["*/_restricted/*", "proposals/*"],              # never indexed, searched or packed
    "strict": False,     # False: page problems block a commit only in pages the run touched (a ratchet)
    "allowed_comments": r"\s*/?(generated|human|kac)(:[A-Za-z0-9_.\-]+){0,3}\s*$",      # zone markers, nothing after them
    "card_fields": ["description", "summary"], "sensitivity_fields": ["sensitivity", "markings"],
    "stale_fields": ["stale_after", "review_after"],            # the date after which a page must be re-verified
    "history": "git",    # "external": the wiki keeps its history with its own tool (a release archive, a ledger)
    "index": {"skip_types": ["redirect"], "dir": "index", "link": "wikilink"},
    "pack": {"out": "export/context-pack.md", "include": [], "hide_types": ["source"],
             "allow_sensitivity": ["public", "internal"],     # filter IN: only these labels may leave the wiki
             "deny_sensitivity": [],                          # optional extra exclusions (older configs)
             "scrub": [], "forbid": []},
    "backup": {"dir": ".backup", "keep": 14}, "sync": "", "devices": [],
    "inert_pages": True, "lock_stale_hours": 6, "eval_threshold": 0.9,
}
SKIP_NAMES = ("index.md", "log.md", "README.md", "AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md")
FM = re.compile(r"\A\ufeff?---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\\?\|[^\]\n]*)?\]\]")
MDLINK = re.compile(r"(?<!\!)\[[^\]\n]*\]\(([^)\s]+)\)")
GEN = re.compile(r"<!-- generated:start -->.*?<!-- generated:end -->", re.S)
TOKEN = re.compile(r"[^\W_]+")                 # words in any script
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+|[^\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+")
STOP = set("the a an of to in on and or for is are was be with by at it this that from as how why "
           "what do does not".split())
SECRET = re.compile(r"(?<![A-Za-z0-9])(sk-(?:[A-Za-z0-9]{1,8}-){0,3}[A-Za-z0-9_]{20,}|[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|"
                    r"whsec_[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}|gh[pousr]_[A-Za-z0-9]{30,}|"
                    r"github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})|-----BEGIN [A-Z ]*PRIVATE KEY-----")
SYNC_HINTS = {"onedrive": "onedrive", "dropbox": "dropbox", "google drive": "google-drive", "googledrive": "google-drive",
              "icloud": "icloud", "mobile documents": "icloud", "pcloud": "pcloud", "box sync": "box",
              "nextcloud": "nextcloud", "syncthing": "syncthing"}
# ----------------------------------------------------------------------------- inert pages
# Does a page fetch, run or hide something when it is rendered? Two steps, like a renderer followed by a browser:
#   1. to_html(): the block and inline rules of CommonMark, as the reference parsers apply them, reduced to what
#      matters here. Raw HTML (HTML blocks, inline tags) goes out as it is, images and links that could matter go
#      out as private marker tags, every other block as a marker. Code produces nothing.
#   2. browser_items(): reads that text the way a browser's tokenizer does.
# Renderers do not agree on every text. RULES lists the points on which the CommonMark generations and the common
# parsers (cmark, markdown-it, commonmark.js) are known to differ. Where the parser meets such a point, the text is
# read again under the other rule, and one active reading is enough. A few rare constructs on which they disagree
# in ways that are not modelled are reported as they are. How this was tested: guide section 15.5.
PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
ACTIVE_TAGS = {"script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "link", "meta", "base",
               "basefont", "form", "input", "button", "textarea", "select", "datalist", "svg", "math", "image", "picture",
               "video", "audio", "bgsound", "source", "track", "canvas", "template", "dialog", "title", "font", "marquee",
               "portal", "noscript", "noembed", "noframes", "xmp", "plaintext"}
ACTIVE_ATTRS = {"src", "srcset", "background", "poster", "ping", "style", "bgcolor", "text", "color", "srcdoc",
                "formaction"}                                                 # active on any element, with a value
BARE_ATTRS = {"hidden", "popover"}                                           # active with or without a value
REMOTE = re.compile(r"(?:[a-z][a-z0-9+.-]*:)?//|(?:https?|ftp|wss?|file|javascript|vbscript|data|blob)\s*:", re.I)
SCRIPT_URL = re.compile(r"(?:javascript|vbscript|data)\s*:", re.I)
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85\u2028\u2029]")      # renderers disagree about these
LOOSE = True          # a backtick run without a partner: the paragraph is also read with no code spans at all
UNION = True          # also judge the readings of other renderers and dialects

_HTML_SPACE = "\t\n\x0c\r "
_C0 = "".join(map(chr, range(33)))
_WS = r"[ \t\n\x0b\x0c\r]"
_NAME = r"[A-Za-z][A-Za-z0-9-]*"


def _tag_pattern(ws):
    attr = rf"(?:{ws}+[A-Za-z_:][A-Za-z0-9:._-]*(?:{ws}*={ws}*(?:[^\"'=<>`\x00-\x20]+|'[^']*'|\"[^\"]*\"))?)"
    return rf"<{_NAME}{attr}*{ws}*/?>|</{_NAME}{ws}*>"


def _marker(kind, name, value):
    return f'<kac-{kind} {name}="{html.escape(value, quote=True)}">'


_TAG = _tag_pattern(_WS)
WIDE_TAG = re.compile(_tag_pattern(r"[\s\ufeff]"))        # the same with every kind of white space
WIDE_LONE = re.compile(rf"(?:{WIDE_TAG.pattern})[\s\ufeff]*$")
_BLOCK_NAMES = ("address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|"
                "div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|"
                "legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|section|summary|table|tbody|"
                "td|tfoot|th|thead|title|tr|track|ul")
_ODD_SPACE = "[\x1c-\x1f\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"
WIDE_START = re.compile(rf"</?(?:{_BLOCK_NAMES}|script|pre|style|textarea|search|source){_ODD_SPACE}", re.I)
AUTOLINK = re.compile(r"<[A-Za-z][A-Za-z0-9.+-]{1,31}:[^<>\x00-\x20]*>")
EMAIL = re.compile(r"<[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
                   r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*>")
_LABEL = re.compile(r"\[(?:[^\\\[\]]|\\.)*\]", re.S)
_LABEL_START = re.compile(r"\[(?:[^\\\[\]]|\\.)*", re.S)
_BRACES = re.compile(r"<(?:[^<>\n\\\x00]|\\.)*>")
_TITLE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)", re.S)
_SPNL = re.compile(r" *(?:\n *)?")
_EOL = re.compile(r" *(?:\n|\Z)")
_UNESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])|(&(?:#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z][A-Za-z0-9]{1,31});)")
_SPECIAL = re.compile(r"[\\`<\[\]!]")
_WORD = re.compile(r"[^\s<`]+")
_URL_START = re.compile(r"https?://|ftp://|www\.|mailto:|xmpp:", re.I)
_TICKS = re.compile("`+")
_ATX = re.compile(r"#{1,6}(?: +|$)")
_FENCE = re.compile(r"`{3,}|~{3,}")
_SETEXT = re.compile(r"(?:=+|-+) *$")
_BREAK = re.compile(r"(?:\* *){3,}$|(?:_ *){3,}$|(?:- *){3,}$")
_ORDERED = re.compile(r"(\d{1,9})[.)]")
_MARKERS = re.compile(r"(?:[ \t]*(?:>|[-+*](?=[ \t])|\d{1,9}[.)](?=[ \t])))+[ \t]*")
_ODD = {key: _marker("odd", "why", why) for key, why in {
    "tag": "a tag with unusual white space in it",
    "lazy": "an indented line after a quote or list item that could start a block",
    "quote": "a quote marker behind four or more spaces",
    "tab": "a tab after two quote markers",
    "link-tab": "a tab on a line with a link or a link definition",
    "fence-tab": "a tab after a closing code fence",
    "label": "a link label of a thousand characters or more",
    "parens": "a link destination with 32 or more nested parentheses",
    "ticks": "a run of 80 or more backticks in running text",
    "brackets": "20 or more nested square brackets",
    "deep": "quotes and list items nested 20 levels deep (a list item counts two)",
    "many": "too many constructs that renderers read differently"}.items()}
RULES = {                             # the points on which renderers differ; the first value is CommonMark 0.31
    "textarea": (True, False),        # <textarea> starts a block that runs to </textarea> (since 0.30)
    "search": (True, False),          # <search> starts an HTML block (since 0.31)
    "source": (False, True),          # <source> starts an HTML block (until 0.31)
    "comment": (2, 3, 1),             # an inline comment is any text up to "-->" (0.31) | the same, but not ending
                                      # in "--->" (markdown-it, cmark-gfm) | text without "--" (older)
    "declaration": ("any", "spaced", "capitals"),      # inline <!...>: a letter (since 0.30) | letters and white
                                      # space (0.29) | capitals and white space (cmark)
    "capitals": (False, True),        # a <!... block needs a capital letter (0.29, markdown-it, cmark-gfm)
    "lazy_tag": (False, True),        # a tag alone on a line starts a block even where the line could go on
                                      # with a paragraph in a quote or list item above (0.29, cmark-gfm)
    "open_paren": (False, True),      # a link destination may hold an unclosed parenthesis (0.29, cmark-gfm)
    "fresh": (False, True),           # the line after link reference definitions starts a new block (markdown-it)
    "empty_item": (False, True),      # an empty list item goes on after an indented blank line (cmark)
    "dashes": (False, True),          # --- under a paragraph that held only definitions is plain text (cmark)
    "one_line": (False, True),        # a <?...?> instruction must end on its line (0.29)
    "html_gap": (False, True),        # an HTML block in a list item ends at an empty line, whatever its kind (markdown-it)
    "math": (False, True)}            # $$ starts a math block
MAIN = tuple(values[0] for values in RULES.values())
PROFILES = {"commonmark-0.31": {}, "markdown-it": {"comment": 3, "capitals": True, "fresh": True, "html_gap": True},
            "cmark-gfm": {"search": False, "source": True, "comment": 3, "declaration": "capitals", "capitals": True,
                          "lazy_tag": True, "open_paren": True, "empty_item": True, "dashes": True},
            "commonmark-0.29": {"textarea": False, "search": False, "source": True, "comment": 1, "declaration": "spaced",
                                "capitals": True, "lazy_tag": True, "open_paren": True, "one_line": True}}
MAX_READINGS = 16
MAX_PARENS, MAX_TICKS, MAX_BRACKETS, MAX_LEVEL = 32, 80, 20, 20      # where cmark, cmark-gfm and markdown-it give up
MAX_CONTAINERS = 200                  # no quote or list item is opened beyond this (the text is reported at MAX_LEVEL)
MAX_FAILS = 32                        # comment starts in one paragraph that are read to the end without a match
BUDGET = 2_000_000                    # characters read again while looking for definitions at a paragraph's start
_KEYS = list(RULES)
_STARTS, _TAGS = {}, {}
_LONE_TAG = re.compile(rf"(?:{_TAG}) *$")


def profile(name, **more):
    """The rule values of a named renderer family, as to_html() takes them."""
    return tuple({**dict(zip(_KEYS, MAIN)), **PROFILES[name], **more}[key] for key in _KEYS)


def _starts(textarea, search, source, capitals):
    """How HTML blocks start and end: [(start, end; None = at the next blank line)]."""
    key = (textarea, search, source, capitals)
    if key not in _STARTS:
        literal = "script|pre|style" + ("|textarea" if textarea else "")
        names = _BLOCK_NAMES + ("|search" if search else "") + ("|source" if source else "")
        _STARTS[key] = [(re.compile(rf"<(?:{literal})(?: |>|$)", re.I), re.compile(rf"</(?:{literal})>", re.I)),
                        (re.compile(r"<!--"), re.compile(r"-->")),
                        (re.compile(r"<\?"), re.compile(r"\?>")),
                        (re.compile(r"<![A-Z]" if capitals else r"<![A-Za-z]"), re.compile(r">")),
                        (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>")),
                        (re.compile(rf"</?(?:{names})(?: |/?>|$)", re.I), None)]
    return _STARTS[key]


def _tags(comment, declaration, one_line):
    """Inline HTML: tags, comments, processing instructions, declarations, CDATA."""
    if (comment, declaration, one_line) not in _TAGS:
        c = {2: r"<!-->|<!--->|<!--[\s\S]*?-->", 3: r"<!---?>|<!--(?:[^-]|-[^-]|--[^>])*-->",
             1: r"<!---->|<!--(?:-?[^>-])(?:-?[^-])*-->"}[comment]
        d = {"any": r"<![A-Za-z]+[^>]*>", "spaced": rf"<![A-Za-z]+{_WS}+[^>]*>", "capitals": rf"<![A-Z]+{_WS}+[^>]*>"}[declaration]
        pi = r"<\?.*?\?>" if one_line else r"<\?[\s\S]*?\?>"
        _TAGS[comment, declaration, one_line] = re.compile(rf"{_TAG}|{c}|{pi}|{d}|<!\[CDATA\[[\s\S]*?\]\]>")
    return _TAGS[comment, declaration, one_line]


def _resolve(text):
    """A link destination as a renderer hands it on: backslash escapes and character references resolved."""
    return _UNESCAPE.sub(lambda m: m.group(1) or html.unescape(m.group(2)), text)


def _url(value):
    """A URL as a browser reads it: tabs and line ends dropped, outer blanks stripped, backslashes as slashes."""
    return re.sub(r"[\t\n\r]", "", value or "").strip(_C0).replace("\\", "/")


def _label(text):
    """A link label as the most lenient renderer compares it."""
    return re.sub(r"[\s\ufeff]+", " ", text).strip().lower().upper().casefold()


class _Reading:
    """One way of reading the text (a value for every rule), the other ways that would differ somewhere (`also`),
    and the notes about constructs that are not modelled (`flags`)."""

    def __init__(self, values):
        self.values, self.also, self.flags, self.spent = values, set(), [], 0
        self.__dict__.update(zip(_KEYS, values))
        self.starts = _starts(self.textarea, self.search, self.source, self.capitals)
        self.tags = _tags(self.comment, self.declaration, self.one_line)

    def differs(self, key, test=None):
        """Notes the readings in which rule `key` has another value (for which test(value) is true). A reading
        that is noted already is not tested again."""
        i = _KEYS.index(key)
        for v in RULES[key]:
            other = self.values[:i] + (v,) + self.values[i + 1:]
            if v != self.values[i] and other not in self.also and (test is None or test(v)):
                self.also.add(other)

    def block(self, s, in_para, lazy):
        """How an HTML block that starts with s ends: a pattern, None (at a blank line), or False (no block).
        in_para: the line follows a paragraph. lazy: it could go on with a paragraph in a quote or item above."""
        def read(textarea, search, source, capitals, lazy_tag):
            n = next((i for i, (b, e) in enumerate(_starts(textarea, search, source, capitals)) if b.match(s)), -1)
            return n if n >= 0 else (6 if _LONE_TAG.match(s) and not in_para and (not lazy or lazy_tag) else -1)
        now = {key: getattr(self, key) for key in ("textarea", "search", "source", "capitals", "lazy_tag")}
        mine, low = read(**now), s[:10].lower()
        for key, matters in (("textarea", low.startswith("<textarea")), ("search", low.startswith(("<search", "</search"))),
                             ("source", low.startswith(("<source", "</source"))), ("capitals", low.startswith("<!")),
                             ("lazy_tag", lazy)):
            if matters:                           # the other rules cannot change how this line is read
                self.differs(key, lambda v: read(**{**now, key: v}) != mine)
        return False if mine < 0 else None if mine >= 5 else self.starts[mine][1]


def _destination(t, i, n, cx):
    """(link destination that starts at t[i], where it ends), or (None, i)."""
    m = _BRACES.match(t, i)
    if m:
        return m.group(0)[1:-1], m.end()
    if t.startswith("<", i):
        return None, i
    k, depth = i, 0
    while k < n:
        c = t[k]
        if c == "\\" and k + 1 < n and t[k + 1] in PUNCT:
            k += 2
        elif c == "(":
            depth, k = depth + 1, k + 1
            if depth >= MAX_PARENS:               # cmark and markdown-it give up one level on, commonmark.js reads on
                cx.flags.append(_ODD["parens"])
                return None, i
        elif c == ")":
            if depth < 1:
                break
            depth, k = depth - 1, k + 1
        elif c in " \t\n\x0b\x0c\r":
            break
        else:
            k += 1
    if depth:                                     # an open parenthesis: a destination by the older rules only
        cx.differs("open_paren")
    if (depth and not cx.open_paren) or (k == i and not t.startswith(")", k)):
        return None, i
    return t[i:k], k


def _tail(t, i, n, cx):
    """For t[i] == "(": (destination, end, None) of a complete `(destination "title")`, or (None, i, where the
    reading stopped when only the closing parenthesis was missing)."""
    dest, k = _destination(t, _SPNL.match(t, i + 1).end(), n, cx)
    if dest is None:
        return None, i, None
    j = _SPNL.match(t, k).end()
    if j > k:
        m = _TITLE.match(t, j)
        if m:
            j = _SPNL.match(t, m.end()).end()
    return (dest, j + 1, None) if t.startswith(")", j) else (None, i, j)


def _definitions(text, defs, cx, marks=None):
    """Takes the link reference definitions off the start of a paragraph; returns what is left of it.
    marks: a list that receives where each definition starts."""
    pos, n = 0, len(text)
    while text.startswith("[", pos):
        m = _LABEL.match(text, pos)
        if not m or not text.startswith(":", m.end()):
            break
        dest, k = _destination(text, _SPNL.match(text, m.end() + 1).end(), n, cx)
        if dest is None:
            break
        j, end = _SPNL.match(text, k).end(), None
        title = _TITLE.match(text, j) if j > k else None
        if title:
            end = _EOL.match(text, title.end())
        if not end:                                 # no title, or text after it: the destination must end the line
            end = _EOL.match(text, k)
        label = _label(m.group(0)[1:-1])
        if not end or not label:
            break
        if m.end() - pos > 1000:
            cx.flags.append(_ODD["label"])        # a definition for markdown-it, text for the others
        defs.setdefault(label, _resolve(dest))
        if marks is not None:
            marks.append(pos)
        pos = end.end()
    return text[pos:]


def _inline(t, out, cx, literal=False):
    """One paragraph, read left to right as an inline parser does: a backslash escapes punctuation, a code span
    hides what is in it, autolinks and HTML tags are taken whole, each `]` looks back for its opening bracket.
    literal: backticks are plain text. Returns True when renderers may pair the backticks differently: a run had
    no partner, or a bare URL outside a code span runs into a backtick (GitHub takes it into the link)."""
    i, n, stack, lone = 0, len(t), [], False      # stack: [index of the "[", image?, another bracket after it?, len(out)]
    runs, images, gone, fails, urls, u = None, [], set(), 0, [], 0
    if UNION and not literal and "`" in t:        # the bare URLs that run into a backtick
        for m in _WORD.finditer(t):
            if t.startswith("`", m.end()):
                start = _URL_START.search(m.group())
                if start:
                    urls.append(m.start() + start.start())
    while True:
        m = _SPECIAL.search(t, i)
        while u < len(urls) and urls[u] < (m.start() if m else n):
            if urls[u] >= i:                      # the URL begins in plain text, not in what was just skipped
                lone = True
            u += 1
        if not m:
            return lone
        i, c = m.start(), m.group()
        if c == "\\":
            i += 2 if i + 1 < n and t[i + 1] in PUNCT else 1
        elif c == "`":
            j = i
            while j < n and t[j] == "`":
                j += 1
            close = -1
            if not literal:                       # the next run of exactly as many backticks closes the span
                if j - i >= MAX_TICKS:
                    cx.flags.append(_ODD["ticks"])      # cmark-gfm takes no code span from a run this long
                if runs is None:                  # where the runs of each length start
                    runs = {}
                    for run in _TICKS.finditer(t):
                        runs.setdefault(run.end() - run.start(), []).append(run.start())
                starts = runs.get(j - i, ())
                at = bisect.bisect_left(starts, j)
                if at < len(starts):
                    close = starts[at] + j - i
                else:
                    lone = True
            i = close if close >= 0 else j
        elif c == "<":
            m = EMAIL.match(t, i) or AUTOLINK.match(t, i)
            if m:
                if SCRIPT_URL.match(_url(m.group(0)[1:-1])):
                    out.append(_marker("link", "href", m.group(0)[1:-1]))
                i = m.end()
                continue
            if t.startswith(("<!", "<?"), i):     # comments, declarations, instructions: the rules have changed
                ending = ("?>" if t[i + 1] == "?" else "-->" if t.startswith("--", i + 2)
                          else "]]>" if t.startswith("[CDATA[", i + 2) else ">")
                if ending in gone or t.find(ending, i + 2) < 0:
                    gone.add(ending)              # nothing of this kind ends after this point
                    i += 1
                    continue
                m = cx.tags.match(t, i)
                end = m.end() if m else -1
                for key in ("comment", "declaration", "one_line"):
                    def other(v):
                        found = _tags(**{"comment": cx.comment, "declaration": cx.declaration,
                                         "one_line": cx.one_line, key: v}).match(t, i)
                        return (found.end() if found else -1) != end
                    cx.differs(key, other)
                if not m and ending == "-->":
                    fails += 1
                    if fails > MAX_FAILS:         # each of these was read to the end of the paragraph
                        cx.flags.append(_ODD["many"])
                        gone.add(ending)
            else:
                m = cx.tags.match(t, i)
            if m:
                out.append(m.group(0))            # raw HTML goes to the browser as it is
                i = m.end()
                continue
            m = WIDE_TAG.match(t, i)              # a tag for the renderers that take any white space inside one
            if m and re.search(r"[`\[\]!\\<]", m.group(0)[1:]):
                cx.flags.append(_ODD["tag"])      # the two readings part ways here
            elif m:
                out.append(m.group(0))
                i = m.end()
                continue
            i += 1
        elif c == "!":
            if t.startswith("[", i + 1):
                if stack:
                    stack[-1][2] = True
                stack.append([i + 1, True, False, len(out)])
                if len(stack) >= MAX_BRACKETS:
                    cx.flags.append(_ODD["brackets"])   # markdown-it stops reading links one level on
                i += 1
            i += 1
        elif c == "[":
            if stack:
                stack[-1][2] = True
            stack.append([i, False, False, len(out)])
            if len(stack) >= MAX_BRACKETS:
                cx.flags.append(_ODD["brackets"])
            i += 1
        else:                                     # "]"
            i += 1
            if not stack:
                continue
            at, image, nested, mark = stack.pop()
            dest, end, stop = _tail(t, i, n, cx) if t.startswith("(", i) else (None, i, None)
            m = _LABEL.match(t, stop) if UNION and stop is not None else None
            if m and m.end() - stop > 2:          # commonmark.py looks for a reference label from where it stopped
                if image:
                    images.append(len(out))
                out.append(_marker("ref" if image else "lref", "label", _label(t[stop + 1:m.end() - 1])))
            if dest is not None:
                dest, i = _resolve(dest), end
                if image:                         # images in its description are its alternative text, not images
                    while images and images[-1] >= mark:
                        out[images.pop()] = ""
                    images.append(len(out))
                    out.append(_marker("img", "src", dest))
                elif SCRIPT_URL.match(_url(dest)):
                    out.append(_marker("link", "href", dest))
                continue
            m = _LABEL.match(t, i)
            label = t[i + 1:m.end() - 1] if m and m.end() - i > 2 else (None if nested else t[at + 1:i - 1])
            if label is not None and _label(label):      # a reference: the definitions decide, at the end
                if image:
                    images.append(len(out))
                out.append(_marker("ref" if image else "lref", "label", _label(label)))
                if len(label) > 999:
                    cx.flags.append(_ODD["label"])


def _fence(s):
    """The code fence that s opens with, or None (a backtick fence has no backtick after it on its line)."""
    m = _FENCE.match(s)
    return m.group(0) if m and not (s[0] == "`" and "`" in s[m.end():]) else None


def _item(s, ind, in_para):
    """For a line that reads s after ind spaces: (content offset, rest of the line, empty item?) when a list item
    starts there."""
    m = _ORDERED.match(s)
    if m:
        if in_para and int(m.group(1)) != 1:
            return None
        w = m.end()
    elif s[:1] in ("*", "+", "-"):
        w = 1
    else:
        return None
    after = s[w:]
    empty = not after.strip(" ")
    if (after and after[0] != " ") or (in_para and empty):
        return None
    spaces = len(after) - len(after.lstrip(" "))
    if spaces >= 5 or empty:
        return ind + w + 1, after[1:], empty
    return ind + w + spaces, after[spaces:], empty


def to_html(body, rules=MAIN):
    """(text, definitions, side texts, other readings): what a CommonMark renderer hands to the browser, reduced
    to what matters here. rules: a value for every entry of RULES (see profile()). The side texts are judged on
    their own: [(text, where it is from)], the indented code blocks (for renderers that do not take them as code)
    and the notes about constructs that renderers read differently. The other readings are the rule values under
    which some line of this text is read differently."""
    cx = _Reading(rules)
    out, defs, conts, leaf = [], {}, [], None     # conts: the open quotes ["q"] and list items ["i", offset, started]
    side, code, levels = [], [], []               # leaf: ["p", lines] | ["f", closing fence] | ["m"] | ["h", end, lines]
    k, settled, in_para = 0, True, False          # levels: how deep each container is, as markdown-it counts

    def close_leaf():
        nonlocal leaf
        was, leaf = leaf, None
        if was and was[0] == "p":
            text = _definitions("\n".join(was[1]), defs, cx)
            if text.strip(" \n"):
                out.append("<kac-p>")
                lone = _inline(text, out, cx)
                if (lone and LOOSE) or (UNION and text.count("$") > 1):     # renderers pair backticks differently
                    _inline(text, out, cx, True)                            # then, and dollar math can take one away
                if UNION and "|" in text and any("-" in x and not x.strip(" |:-") for x in text.split("\n")):
                    for cell in re.split(r"(?<!\\)\||\n", text):            # a table: every cell is read on its own
                        _inline(cell.replace("\\|", "|"), out, cx)
                out.append("</kac-p>\n")
        elif was and was[0] == "h":
            chunk = "\n".join(was[2])
            if UNION:                             # dialects that read Markdown inside HTML blocks
                found = []
                _inline(chunk, found, cx)
                out.extend(x for x in found if x.startswith("<kac-"))
            if any(item[0] == "open" for item in browser_items(chunk)):
                cx.flags.append("<kac-open>")     # the block stops inside a tag: it would swallow what follows
            out.append(chunk + "\n")

    def close_code():
        if code:
            found = []
            _inline("\n".join(code), found, cx)
            side.append(("".join(x for x in found if x.startswith("<kac-")) + "\n".join(code),
                         " in an indented block (not code for every renderer)"))
            code.clear()

    def begin(more_code=False):                   # a new block starts: what the line did not continue is closed
        nonlocal settled, in_para
        close_leaf()
        if not more_code:
            close_code()
        if not settled:
            out.append("</kac-c>\n" * len(conts[k:]))      # the renderer closes the quote or the item: a tag
            del conts[k:], levels[k:]
        settled, in_para = True, False

    def open_container(entry):
        conts.append(entry)
        levels.append((levels[-1] if levels else 0) + (1 if entry[0] == "q" else 2))
        if levels[-1] >= MAX_LEVEL:
            cx.flags.append(_ODD["deep"])         # markdown-it leaves out what is nested deeper than this
        out.append("<kac-c>\n")

    def after_definitions(line):
        """True when the paragraph so far holds only link reference definitions and this line is not the rest of
        one. cmark goes on with the paragraph, markdown-it starts a new block."""
        lines = leaf[1] if leaf and leaf[0] == "p" else None
        if not lines or not lines[0].startswith("["):
            return False
        if len(leaf) < 3:
            leaf.append([0, "", False])           # lines read | their text, from the last definition on | only
        memo = leaf[2]                            # definitions so far? (None: no, and no later line changes that)
        if memo[2] is not None and memo[0] < len(lines):
            memo[1] += ("\n" if memo[0] else "") + "\n".join(lines[memo[0]:])
            memo[0], marks = len(lines), []
            cx.spent += len(memo[1])
            left = _definitions(memo[1], {}, cx, marks)
            if marks:                             # the definitions before the last one are settled
                memo[1] = memo[1][marks[-1]:]
            memo[2] = not left.strip(" \n")
            if not memo[2]:                       # is the text that is left over there for good?
                if left[0] != "[":                # yes, unless it may become the title of the last definition
                    memo[2] = False if marks and left[0] in "\"'(" else None
                else:                             # yes, when it has a label without a colon or a bracket in the label
                    label = _LABEL.match(left)
                    if label and not left.startswith(":", label.end()):
                        memo[2] = None
                    elif not label and left.startswith("[", _LABEL_START.match(left).end()):
                        memo[2] = None
            if memo[2] is not None and cx.spent > BUDGET:
                cx.flags.append(_ODD["many"])
                memo[2] = None
        if not memo[2]:
            return False
        cx.spent += len(memo[1])
        if not _definitions(memo[1] + "\n" + line, {}, cx).strip(" \n"):
            return False
        cx.differs("fresh")
        return cx.fresh

    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        rest, k, short = line.expandtabs(4), 0, False
        if "\t" in line:
            m = _MARKERS.match(line)              # markdown-it counts the width of a tab after two quote markers
            if m and "\t" in m.group() and m.group().count(">") > 1:      # differently
                cx.flags.append(_ODD["tab"])
            if ("](" in line or "]:" in line) and "\t" in line.lstrip(" \t>"):
                cx.flags.append(_ODD["link-tab"])       # white space in link syntax: a tab counts for some only
        while k < len(conts):                     # 1. the open containers this line continues
            c, ind = conts[k], len(rest) - len(rest.lstrip(" "))
            if c[0] == "q":
                if rest[ind:ind + 1] != ">":
                    break
                if ind > 3:
                    cx.flags.append(_ODD["quote"])      # markdown-it takes the marker at any depth, cmark up to three
                    break
                rest = rest[ind + 2:] if rest[ind + 1:ind + 2] == " " else rest[ind + 1:]
            elif not rest.strip(" "):
                if not c[2]:                      # an item that began with a blank line ends at the next one,
                    if ind >= c[1]:               # though cmark goes on when the blank line is indented enough
                        cx.differs("empty_item")
                    if ind < c[1] or not cx.empty_item:
                        break
                short = short or ind < c[1]       # a blank line that is not indented as far as the item
            elif ind >= c[1]:
                rest, c[2] = rest[c[1]:], True
            else:
                break
            k += 1
        blank, whole, kind = not rest.strip(" "), k == len(conts), leaf[0] if leaf else None
        ind = len(rest) - len(rest.lstrip(" "))
        if whole and kind == "f":                 # 2. code and raw HTML take the line as it is
            if ind <= 3 and leaf[1].match(rest, ind):
                leaf = None
                if "\t" in line[len(line.rstrip(" \t")):]:
                    cx.flags.append(_ODD["fence-tab"])      # the fence goes on for the older commonmark.js
            continue
        if whole and kind == "m":
            if rest.rstrip(" ").endswith("$$"):
                leaf = None
            continue
        if whole and kind == "h" and blank and short and leaf[1] is not None:
            cx.differs("html_gap")
            if cx.html_gap:
                close_leaf()
                continue
        if whole and kind == "h" and not (leaf[1] is None and blank):
            leaf[2].append(rest)
            if leaf[1] is not None and leaf[1].search(rest):
                close_leaf()
            continue
        in_para = whole and kind == "p" and not blank
        settled = whole and (leaf is None or in_para)          # nothing is waiting to be closed
        done = False
        while not done:                           # 3. the blocks that start on this line
            ind = len(rest) - len(rest.lstrip(" "))
            s = rest[ind:]
            c0 = s[:1]
            para = bool(leaf and leaf[0] == "p")
            lazy = para and not settled           # the line may go on with a paragraph in a quote or item above
            if ind >= 4:
                if lazy and (c0 == ">" or _ATX.match(s) or _fence(s) or _BREAK.match(s) or _item(s, 0, False)
                             or cx.block(s, True, True) is not False):
                    cx.flags.append(_ODD["lazy"])       # text for cmark; markdown-it may end the paragraph here
                if s and (not para or after_definitions(s)):      # indented code (it cannot interrupt a paragraph)
                    begin(True)
                    out.append("<kac-pre>\n")
                    if UNION:
                        code.append(s)
                    done = True
                break
            if c0 == ">" and len(conts) < MAX_CONTAINERS:
                begin()
                open_container(["q"])
                rest = s[2:] if s[1:2] == " " else s[1:]
                continue
            m = _ATX.match(s) if c0 == "#" else None
            if m:
                begin()
                out.append("<kac-p>")
                _inline(s[m.end():], out, cx)
                out.append("</kac-p>\n")
                done = True
                break
            fence = _fence(s) if c0 in ("`", "~") else None
            if fence:
                begin()
                leaf = ["f", re.compile(rf"{re.escape(fence[0])}{{{len(fence)},}} *$")]
                out.append("<kac-pre>\n")
                done = True
                break
            if c0 == "$" and s.startswith("$$"):
                cx.differs("math")
                if cx.math:
                    begin()
                    if not (len(s.strip(" ")) > 3 and s.rstrip(" ").endswith("$$")):
                        leaf = ["m"]
                    out.append("<kac-pre>\n")
                    done = True
                    break
            if c0 == "<":
                end = cx.block(s, in_para, lazy)
                if end is False and _LONE_TAG.match(s) and (in_para or lazy) and after_definitions(s):
                    end = None                    # a tag alone on a line cannot interrupt a paragraph
                if end is not False:
                    begin()
                    leaf = ["h", end, [rest]]
                    if end is not None and end.search(s):
                        close_leaf()
                    done = True
                    break
                if WIDE_START.match(s) or (WIDE_LONE.match(s) and not _LONE_TAG.match(s) and not in_para):
                    cx.flags.append(_ODD["tag"])        # an HTML block for renderers that take any white space in a tag
            if in_para and _SETEXT.match(s):      # the paragraph above becomes a heading and ends here
                text = _definitions("\n".join(leaf[1]), defs, cx)
                del leaf[2:]
                if text.strip(" \n"):
                    leaf[1] = [text]
                    begin()
                    done = True
                    break
                leaf[1] = []                      # it held only definitions, so there is no heading: the line
                cx.differs("fresh")               # starts a new block (markdown-it), or is read as a line after a
                if cx.fresh:                      # paragraph (commonmark.js), or is that paragraph's text (cmark)
                    begin()
                    continue
                if _BREAK.match(s):
                    cx.differs("dashes")
                    if cx.dashes:
                        break
            if c0 in ("*", "-", "_") and _BREAK.match(s):
                begin()
                out.append("<kac-c></kac-c>\n")
                done = True
                break
            item = _item(s, ind, in_para)
            if not item and in_para and _item(s, ind, False) and after_definitions(s):
                item = _item(s, ind, False)
            if not item or len(conts) >= MAX_CONTAINERS:
                break
            begin()
            open_container(["i", item[0], not item[2]])
            rest = item[1]
        if done:
            continue
        blank = not rest.strip(" ")
        if leaf and leaf[0] == "p" and not blank and (in_para or (not settled and not after_definitions(rest.lstrip(" ")))):
            leaf[1].append(rest.lstrip(" "))      # the paragraph goes on (also when its quote or item was not continued,
        else:                                     # unless it held only definitions and the reading is markdown-it's)
            begin()
            if not blank:
                leaf = ["p", [rest.lstrip(" ")]]
    k, settled = 0, False
    begin()
    return "".join(out), defs, side + [("".join(dict.fromkeys(cx.flags)), "")], cx.also


def _tag(text, k, n):
    """Reads the tag whose name starts at text[k] as a browser does: (end, name, [(attribute, value or None)]).
    end is -1 when the text stops inside the tag."""
    a = k
    while k < n and text[k] not in _HTML_SPACE and text[k] not in "/>":
        k += 1
    name, attrs = text[a:k].lower(), []
    while True:
        while k < n and (text[k] in _HTML_SPACE or text[k] == "/"):
            k += 1
        if k >= n:
            return -1, name, attrs
        if text[k] == ">":
            return k + 1, name, attrs
        a, k = k, k + 1                           # the first character belongs to the name, whatever it is
        while k < n and text[k] not in _HTML_SPACE and text[k] not in "/>=":
            k += 1
        key, value = text[a:k].lower(), None
        while k < n and text[k] in _HTML_SPACE:
            k += 1
        if k < n and text[k] == "=":
            k += 1
            while k < n and text[k] in _HTML_SPACE:
                k += 1
            if k >= n:
                return -1, name, attrs
            if text[k] in "\"'":
                e = text.find(text[k], k + 1)
                if e < 0:
                    return -1, name, attrs
                value, k = text[k + 1:e], e + 1
            elif text[k] == ">":
                value = ""
            else:
                e = k
                while e < n and text[e] not in _HTML_SPACE and text[e] != ">":
                    e += 1
                value, k = text[k:e], e
        attrs.append((key, html.unescape(value) if value is not None else None))


def browser_items(text):
    """What a browser's tokenizer finds in the text: ("tag", name, attributes), ("comment", its text),
    ("hidden-rest", text) for a comment that never ends and has markup after it, ("open",) when the text stops
    inside a tag."""
    items, i, n, bang = [], 0, len(text), -2      # bang: where the next "--!>" is (-1: nowhere, -2: not looked for)
    while True:
        i = text.find("<", i)
        if i < 0 or i + 1 >= n:
            return items
        c, after = text[i + 1], text[i + 2:i + 3]
        if text.startswith("<!--", i):
            if bang != -1 and bang < i + 4:
                bang = text.find("--!>", i + 4)
            ends = [e for e in ((text.find("-->", i + 2), 3), (bang, 4)) if e[0] >= 0]
            if not ends:                          # a comment that never ends hides everything after it
                rest = text[i + 4:]
                items.append(("hidden-rest" if re.search(r"<[A-Za-z]", rest) else "comment", rest))
                return items
            j, width = min(ends)
            items.append(("comment", text[i + 4:j]))
            i = j + width
        elif c in "!?" or (c == "/" and not (after.isascii() and after.isalpha())):
            if c == "/" and after in ("", ">"):   # "</>" is dropped, "</" at the end is text
                i += 3
                continue
            j = text.find(">", i + 2)             # <!DOCTYPE ...>, <?...>, </3...>: skipped up to the next ">"
            if not re.match(r"<!doctype", text[i:i + 9], re.I):
                items.append(("comment", text[i + 2:j if j >= 0 else n]))
            i = j + 1 if j >= 0 else n
        elif c == "/" or (c.isascii() and c.isalpha()):
            end, name, attrs = _tag(text, i + 1 + (c == "/"), n)
            if end < 0:
                items.append(("open",))
                return items
            if c != "/":
                items.append(("tag", name, attrs))
            i = end
        else:
            i += 1


def _judge(text, defs, out, where=""):
    why = []
    for item in browser_items(text):
        if item[0] == "hidden-rest":
            why.append("an HTML comment that never ends hides what follows it")
        if item[0] != "tag":
            continue
        name, attrs = item[1], item[2]
        at = dict(reversed(attrs))                # the first of two attributes with one name counts
        if name == "kac-img":
            if REMOTE.match(_url(at.get("src"))):
                why.append("an image from another host")
        elif name in ("kac-ref", "kac-lref"):
            dest = _url(defs.get(at.get("label") or "", ""))
            if name == "kac-ref" and REMOTE.match(dest):
                why.append("an image from another host (through a reference definition)")
            elif name == "kac-lref" and SCRIPT_URL.match(dest):
                why.append("a link with a script URL (through a reference definition)")
        elif name == "kac-link":
            why.append("a link with a script URL")
        elif name == "kac-open":
            why.append("an HTML block that stops inside a tag")
        elif name == "kac-odd":
            why.append(f"{at.get('why')} (renderers read it differently)")
        elif name in ACTIVE_TAGS:
            why.append(f"<{name}>")
        else:
            for a, v in attrs:
                if name == "img" and a == "src":
                    if REMOTE.match(_url(v)):
                        why.append("an image from another host")
                elif a in BARE_ATTRS or (v and (a in ACTIVE_ATTRS or (a.startswith("on") and len(a) > 2))):
                    why.append(f"<{name} {a}>")
                elif v and a in ("href", "xlink:href") and SCRIPT_URL.match(_url(v)):
                    why.append(f"<{name} {a}> with a script URL")
    out.extend(w + where for w in why)


def _readings(body):
    """Every way of reading the text that differs somewhere: [(main text, definitions, side texts)]."""
    todo, seen, found = [MAIN], set(), []
    while todo:
        rules = todo.pop()
        if rules in seen:
            continue
        if len(seen) >= MAX_READINGS:
            found.append(("", {}, [(_ODD["many"], "")]))
            break
        seen.add(rules)
        text, defs, side, also = to_html(body, rules)
        found.append((text, defs, side))
        if UNION:
            todo.extend(also)
    return found


def active_findings(body):
    """Why a page is not inert: a list of short reasons (empty when it is)."""
    why = []
    m = CONTROL.search(body)
    if m:
        why.append(f"a control character, U+{ord(m.group()):04X} (renderers read it differently)")
    for text, defs, side in _readings(body):
        _judge(text, defs, why)
        for chunk, where in side:
            _judge(chunk, defs, why, where)
    return list(dict.fromkeys(why))


def active_content(body):
    """True when a page would fetch, run or hide something when rendered: an image from another host (inline or
    through a reference definition), an HTML element that loads, submits, runs script or hides text, a link with
    a script URL, a comment that never ends. Fenced code and code spans are not rendered, so they do not count.
    A filter for what CommonMark renderers and a browser do with the text, not a proof: show pages in a viewer
    that loads nothing remote."""
    return bool(active_findings(body))


def raw_html(body):
    """True when the text contains any HTML tag or comment that a renderer would pass on (for input that must be
    plain Markdown)."""
    own = ("kac-img", "kac-ref", "kac-lref", "kac-link", "kac-p", "kac-pre", "kac-c")
    return any(item[0] != "tag" or item[1] not in own for text, defs, side in _readings(body)
               for item in browser_items(text))


def hidden_comments(body):
    """The texts of the HTML comments a browser would hide (comments inside code do not count)."""
    return [item[1] for item in browser_items(to_html(body)[0]) if item[0] in ("comment", "hidden-rest")]


def nfc(x):
    return unicodedata.normalize("NFC", x)       # macOS lists file names decomposed; git reports them composed


# ----------------------------------------------------------------------------- small helpers
def die(msg, code=2):
    print(f"kac: {msg}", file=sys.stderr)
    sys.exit(code)


def find_root(start=None):
    given = start or os.environ.get("KAC_ROOT")
    if given:                                # an explicit root is used as given, never a folder above it
        if not Path(given).is_dir():
            die(f"--root {given}: not a folder")
        return Path(given).resolve()
    for base in (Path(os.getcwd()).resolve(), Path(__file__).resolve().parent):
        for d in (base, *base.parents):      # the nearest kac.yaml wins over a .git or AGENTS.md further down
            if (d / "kac.yaml").exists():
                return d
        for d in (base, *base.parents):      # no kac.yaml yet: the nearest folder that holds .git or AGENTS.md
            if (d / ".git").exists() or (d / "AGENTS.md").exists():
                return d
    die("no wiki found (looked for kac.yaml, .git or AGENTS.md in this folder and above); name the folder with --root")


def rel(root, p):
    try:
        return Path(p).relative_to(root).as_posix()
    except ValueError:
        return Path(p).resolve().relative_to(root).as_posix()


def sh(cmd, cwd, shell=False, timeout=1800):
    if shell and isinstance(cmd, str):          # {python} = the interpreter that runs this kit (portable)
        cmd = cmd.replace("{python}", f'"{sys.executable}"')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env.setdefault("PYTHONPYCACHEPREFIX", str(cache_dir(Path(cwd)) / "pyc"))    # never tools/__pycache__: a cached
    r = subprocess.run(cmd, cwd=str(cwd), shell=shell, text=True, capture_output=True, env=env,   # .pyc is unreviewed code
                       timeout=timeout, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


_IDENT = {}
_TOP = {}


def repo_top(root):
    """The folder that holds .git (git wants this one in safe.directory), found without running git."""
    key = str(root)
    if key not in _TOP:
        _TOP[key] = next((d for d in (root, *root.parents) if (d / ".git").exists()), root).as_posix()
    return _TOP[key]


def git(root, *args, readonly=True, env_add=None):
    env = dict(os.environ, **(env_add or {}))
    if readonly:
        env["GIT_OPTIONAL_LOCKS"] = "0"      # a read never touches .git/index (safe next to another writer)
    cmd = ["git", "-c", f"safe.directory={repo_top(root)}", "-c", "core.quotepath=off", "-c", "gc.auto=0"]
    if not readonly:
        if "ok" not in _IDENT:
            r = subprocess.run(cmd + ["config", "user.email"], cwd=str(root), text=True, capture_output=True)
            _IDENT["ok"] = bool(r.stdout.strip())
        if not _IDENT["ok"]:                 # a sandbox without a git identity can still commit
            cmd += ["-c", "user.name=kac", "-c", "user.email=kac@localhost"]
    r = subprocess.run(cmd + list(args), cwd=str(root), text=True, capture_output=True, env=env,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def git_show(root, rev, path):
    """Exact bytes of <path> (relative to the wiki folder) at commit <rev>, or None when it does not exist there."""
    r = subprocess.run(["git", "-c", f"safe.directory={repo_top(root)}", "cat-file", "blob", f"{rev}:./{path}"],
                       cwd=str(root), capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    return r.stdout if r.returncode == 0 else None


_PREFIX = {}


def prefix(root):
    """'' when the wiki folder is the top of its git repository, else its path inside it, e.g. 'docs/kb/'."""
    key = str(root)
    if key not in _PREFIX:
        rc, out, _ = git(root, "rev-parse", "--show-prefix")
        _PREFIX[key] = out.strip() if rc == 0 else ""
    return _PREFIX[key]


def has_head(root):
    return git(root, "rev-parse", "--verify", "-q", "HEAD")[0] == 0


def tracked_mode(root, rev, path):
    """'100755', '100644', ... as stored in git, or '' when the path is not in that commit."""
    out = git(root, "ls-tree", rev, "--", f"./{path}")[1].split()
    return out[0] if out else ""


def is_repo(root):
    return git(root, "rev-parse", "--git-dir")[0] == 0


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_UMASK = os.umask(0)
os.umask(_UMASK)


def atomic_write(path, data, retries=8):
    """Write bytes/str via a same-folder *.tmp file + fsync + replace. LF newlines. Retries on Windows locks."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.replace("\r\n", "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".kac-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:                                 # mkstemp makes the file private (0600): give it the mode of the file
            mode = stat.S_IMODE(os.stat(path).st_mode) if path.exists() else (0o666 & ~_UMASK)   # it replaces
            os.chmod(tmp, mode)
        except OSError:
            pass
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.05 * 2 ** attempt)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def append_text(path, text):
    path = Path(path)
    old = path.read_bytes() if path.exists() else b""
    if old and not old.endswith(b"\n"):
        old += b"\n"
    atomic_write(path, old + text.replace("\r\n", "\n").encode("utf-8"))


def canon(rec):
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def jl_read(path):
    """Return (rows, problems). Tolerates a torn last line and reports it."""
    rows, bad = [], []
    if not Path(path).exists():
        return rows, bad
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad.append(n)
    return rows, bad


def now(cfg=None):
    tzname = (cfg or {}).get("tz") or "UTC"
    if tzname.upper() == "UTC":
        return dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(tzname))
    except Exception:                       # Windows without the tzdata package: use the computer's own zone
        return dt.datetime.now().astimezone()


def new_run_id(cfg):
    t = now(cfg)
    return "r-" + t.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha256(os.urandom(16)).hexdigest()[:4]


def kb(nbytes):
    return f"{nbytes} B" if nbytes < 2048 else f"{nbytes / 1024:.0f} KB"


def est_tokens(nbytes):
    return nbytes // 3          # conservative for 2026 tokenizers (about 3 bytes per token of English prose)


INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md")   # agents load these from any folder
ANYWHERE = {n.lower() for n in INSTRUCTION_NAMES + ("AGENTS.override.md", ".gitattributes")}      # pinned in any folder
WALK_CAP = 20000                         # folders visited while looking for them (a link can lead to a huge tree)
DEP_DIRS = {"node_modules", ".venv", "venv"}                    # dependency folders: adopt proposes them as unpinned


def glob_many(root, patterns, skip=()):
    """Files matching the patterns. `folder/**/*` is walked with symbolic links followed (an agent follows them
    too); a folder already walked is not entered twice, and the folders listed in `skip` are never entered."""
    out = []
    for pat in patterns or []:
        base = pat[:-5]
        if pat.endswith("/**/*") and base and not any(ch in base for ch in "*?["):
            walked = set()
            for folder, subdirs, names in os.walk(root / base, followlinks=True):
                real = os.path.realpath(folder)
                if real in walked:                # a link back into a folder that was walked already
                    subdirs[:] = []
                    continue
                walked.add(real)
                here_ = Path(folder).relative_to(root).as_posix()
                subdirs[:] = sorted(d for d in subdirs if d != ".git" and not matches_any(f"{here_}/{d}", skip))
                out += [q for q in (Path(folder) / n for n in sorted(names)) if q.is_file()]
        else:
            out += [p for p in sorted(root.glob(pat)) if p.is_file()]
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# ----------------------------------------------------------------------------- config and pages
NOT_PAGES = {"raw", "proposals", "export", "exports", "templates", "tools", "tests", "evals", "objects", "ledger",
             "sources", "state", "registry", "schemas", "node_modules", "index", "log", "skills", "ontology"}


def detect(root):
    """Guess the kit settings from what is on disk. Known folder names first (the layouts of the earlier
    editions); in any other layout, the top-level folders whose Markdown files mostly start with front matter."""
    d = {}
    pages = [n for n in ("wiki", "pages", "docs", "knowledge") if (root / n).is_dir()]
    for n in ("feedback", "briefs"):
        if (root / n).is_dir() and any(_starts_fm(p) for p in list((root / n).glob("*.md"))[:5]):
            pages.append(n)
    if not pages:
        try:
            tops = sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith((".", "_"))
                          and x.name.lower() not in NOT_PAGES)
        except OSError:
            tops = []
        for x in tops:
            mds = []
            for q in x.rglob("*.md"):
                mds.append(q)
                if len(mds) >= 40:
                    break
            if mds and 2 * sum(_starts_fm(q) for q in mds) >= len(mds):
                pages.append(x.name)
    d["pages"] = pages or ["wiki"]
    recs = []
    for n in ("claims", "evidence", "relations", "events", "lineage"):
        recs += [rel(root, p) for p in sorted((root / n).glob("*.jsonl"))] if (root / n).is_dir() else []
    d["records"] = recs
    chain = ["{python} tools/" + n for n in ("render.py", "build_index.py", "lint.py", "eval.py")
             if (root / "tools" / n).exists()]
    d["checks"] = chain
    d["rebuild"] = [c for c in chain if "render" in c or "build_index" in c]
    low = root.as_posix().lower()
    d["sync"] = next((name for hint, name in SYNC_HINTS.items() if hint in low), "")
    return d


def _starts_fm(p):
    try:
        with open(p, "rb") as f:
            return f.read(6).lstrip(b"\xef\xbb\xbf").startswith(b"---")
    except OSError:
        return False


def load_cfg(root):
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg.update(detect(root))
    raw = {}
    f = Path(os.environ["KAC_CONFIG"]) if os.environ.get("KAC_CONFIG") else root / "kac.yaml"
    if os.environ.get("KAC_CONFIG") and not f.is_file():
        die(f"configuration file {f} not found (--config / KAC_CONFIG)")
    if f.exists():
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            die(f"kac.yaml is not valid YAML: {e}")
    if not isinstance(raw, dict):
        die("kac.yaml must be a mapping")
    kit = raw.get("kit") or {}
    if not isinstance(kit, dict):
        die("kac.yaml: `kit:` must be a mapping (key: value lines)")
    for k, v in kit.items():
        if v is None and k in ("checks", "rebuild"):
            v = []                                # every entry commented out: nothing is run
        if v is None:
            continue                              # another key whose lines are all commented out keeps its default
        if isinstance(cfg.get(k), dict):
            if not isinstance(v, dict):
                die(f"kac.yaml: `kit: {k}:` must be a mapping")
            cfg[k].update({kk: vv for kk, vv in v.items() if vv is not None})
        elif k in ("instructions", "exclude", "derived"):
            cfg[k] = list(dict.fromkeys(DEFAULTS[k] + [str(x) for x in as_list(v)]))    # these extend the defaults
        else:
            cfg[k] = v
    if str(cfg["history"]).lower() == "external":
        for k in ("checks", "rebuild"):           # the wiki's own scripts are started only when the configuration names them
            if k not in kit:
                cfg[k] = []
    for k in ("pages", "records", "checks", "rebuild", "unpinned", "raw_exclude", "devices", "start_files",
              "card_fields", "sensitivity_fields", "stale_fields"):
        cfg[k] = [str(x) for x in as_list(cfg.get(k))]              # `pages: wiki` means the same as `pages: [wiki]`
    pk = cfg["pack"]
    for k in ("include", "hide_types", "allow_sensitivity", "deny_sensitivity", "forbid"):
        pk[k] = [str(x) for x in as_list(pk.get(k))]
    pk["scrub"] = [r for r in as_list(pk.get("scrub")) if isinstance(r, dict) and r.get("pattern")]
    for pat in pk["forbid"] + [r["pattern"] for r in pk["scrub"]] + [cfg["allowed_comments"]]:
        try:
            re.compile(str(pat))
        except re.error as e:
            die(f"kac.yaml: {pat!r} is not a valid regular expression ({e})")
    cfg["index"]["skip_types"] = [str(x) for x in as_list(cfg["index"].get("skip_types"))]
    for k, v in list(cfg["budgets"].items()):
        try:
            cfg["budgets"][k] = float(v) if float(v) != int(float(v)) else int(float(v))
        except (TypeError, ValueError):
            die(f"kac.yaml: `kit: budgets: {k}:` must be a number")
    cfg["_root"] = root
    cfg["raw"] = str(cfg["raw"]).replace("\\", "/").strip("/") or "raw"
    cfg["name"] = str(raw.get("name") or root.name)
    cfg["spec"] = str(raw.get("spec") or "")
    cfg["has_kit"] = bool(kit)
    if "timezone" in raw and "tz" not in kit:
        cfg["tz"] = raw["timezone"]
    cfg["default_sensitivity"] = ""               # top-level `sensitivity: {default: internal}` (Spec v1: `markings:`)
    for key in ("sensitivity", "markings"):       # labels the pages that carry no label of their own
        sens = raw.get(key)
        if isinstance(sens, dict) and isinstance(sens.get("default"), str) and not cfg["default_sensitivity"]:
            cfg["default_sensitivity"] = sens["default"].lower()
    return cfg


def parse_page(text):
    """Return (meta or None, body, error). CRLF and BOM tolerant."""
    m = FM.match(text)
    if not m:
        return None, text, "no front matter"
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        return None, text[m.end():], "front matter is not valid YAML: " + str(e).splitlines()[0]
    if not isinstance(meta, dict):
        return None, text[m.end():], "front matter is not a mapping"
    return meta, text[m.end():], None


def load_page(path):
    try:
        return parse_page(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        return None, "", str(e)


def iter_pages(root, cfg):
    for d in cfg["pages"]:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            r = p.relative_to(root).as_posix()
            if p.name in SKIP_NAMES or "/." in "/" + r or any(fnmatch.fnmatch(r, x) for x in cfg["exclude"]):
                continue
            if p.is_symlink() or not p.is_file():
                continue
            yield p


def card_of(meta, cfg, limit=None, body=None):
    """The one-line description of a page: its card field, else its title, else (for listings only) the first
    heading or first line of its text."""
    for f in cfg["card_fields"]:
        if meta.get(f):
            text = " ".join(str(meta[f]).split())
            break
    else:
        text = " ".join(str(meta.get("title") or "").split())
        if not text and body:
            lines = [x.strip() for x in GEN.sub("", body).splitlines() if x.strip() and not x.strip().startswith(("<!--", "---"))]
            head = next((x.lstrip("#").strip() for x in lines if x.startswith("#")), lines[0] if lines else "")
            text = " ".join(head.split())
    limit = limit or cfg["budgets"]["card_chars"]
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:- ")
        text = cut + "…"
    return text


def page_id(path, meta):
    return str(meta.get("id") or Path(path).stem)


def state_of(meta):
    for k, v in meta.items():
        if str(k).endswith("_status") and v:
            return str(v)
    return str(meta.get("state") or meta.get("status") or meta.get("verdict") or "")


def as_list(v):
    """Front matter that should be a list but is a single value (`tags: 2026`) is treated as one item."""
    return [] if v is None else (list(v) if isinstance(v, (list, tuple)) else [v])


def sensitivity_of(meta, cfg):
    vals = []
    for f in cfg["sensitivity_fields"]:
        v = meta.get(f)
        if isinstance(v, str):
            vals.append(v.lower())
        elif isinstance(v, (list, tuple)):
            vals += [str(x).lower() for x in v]
        elif v is not None:                       # a mapping, a number, true/false: not a label this kit can read
            vals.append("unreadable-label")       # ... and that name is on no allow-list, so the page does not leave
    return vals or ([cfg["default_sensitivity"]] if cfg.get("default_sensitivity") else [])


def may_leave(labels, cfg, allow, deny=()):
    """Filter in, not out: every label of the page must be on the allow-list. An unlabelled page counts as
    the wiki's default label (kac.yaml `sensitivity: {default: ...}`), or `internal` when none is declared."""
    have = {str(x).lower() for x in (labels or [cfg.get("default_sensitivity") or "internal"])}
    return have <= {str(x).lower() for x in allow} and not (have & {str(x).lower() for x in deny})


# ----------------------------------------------------------------------------- lock and runs
def lock_files(root):
    """The writer lock is one token file that is either 'held' or 'free'. Taking and releasing it are renames,
    so it works where deleting is not permitted. *.tmp: git ignores it and OneDrive does not upload it."""
    d = root / ".kac"
    return d / "lock-held.tmp", d / "lock-free.tmp"


def read_lock(root):
    held, _ = lock_files(root)
    if not held.exists():
        return None
    try:
        info = json.loads(held.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        info = None
    if not isinstance(info, dict) or not info.get("run"):     # a run died in the instant it took the lock
        return {"run": "unknown", "started": 0, "unreadable": True}
    return info


def take_lock(root, cfg, op, title, steal=False, dirty=None):
    held, free = lock_files(root)
    held.parent.mkdir(parents=True, exist_ok=True)
    cur = read_lock(root)
    if cur and not steal:
        age_h = (time.time() - float(cur.get("started", 0))) / 3600
        hint = " (stale: a person may take it over with --steal)" if age_h > cfg["lock_stale_hours"] else ""
        print(f"kac: writer lock held by run {cur.get('run')} ({cur.get('op')} | {cur.get('title')}), "
              f"host {cur.get('host')}, {age_h:.1f} h old{hint}", file=sys.stderr)
        sys.exit(3)
    if not cur:
        try:
            os.rename(free, held)                 # atomic test-and-set
        except FileNotFoundError:                 # first use: there is no token yet
            try:
                os.close(os.open(str(held), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                die("another run took the writer lock a moment ago", 3)
        except OSError:
            die("another run took the writer lock a moment ago", 3)
    info = {"run": new_run_id(cfg), "op": op, "title": title, "host": socket.gethostname(),
            "pid": os.getpid(), "started": time.time(), "head": git(root, "rev-parse", "-q", "--verify", "HEAD")[1].strip()}
    if dirty:
        info["dirty"] = list(dirty)               # changes that were there before this run: abort leaves them alone
    held.write_text(json.dumps(info), encoding="utf-8")
    return info


def drop_lock(root):
    held, free = lock_files(root)
    try:
        if held.exists():
            held.write_text("", encoding="utf-8")     # the token names no run while it is free
    except OSError:
        pass
    for attempt in range(6):
        try:
            os.replace(held, free)
            return
        except FileNotFoundError:
            return
        except PermissionError:                   # Windows: a scanner or sync client holds the file for a moment
            time.sleep(0.05 * 2 ** attempt)


def side_dirs(cfg=None):
    """Kit-owned side folders: never part of a run, even when git does not ignore them."""
    out = [".backup/", "_to_delete/"]
    root = (cfg or {}).get("_root")
    if root is not None:                          # the backup folder, when it lies inside the wiki (KAC_BACKUP included)
        try:
            inside = backup_dir(root, cfg).relative_to(root).as_posix().strip("/")
            if inside and inside != ".":
                out.append(inside + "/")
        except (ValueError, OSError):
            pass
    return tuple(dict.fromkeys(out))


def unmanaged(path, cfg):
    """Paths the kit leaves alone: its side folders, *.tmp, and the drop zone (raw_exclude, e.g. raw/inbox/*)."""
    return (path.endswith(".tmp") or path.startswith(side_dirs(cfg))
            or bool(cfg) and any(fnmatch.fnmatch(path, pat) for pat in cfg["raw_exclude"]))


def set_trash_ignored(root):
    """_to_delete/ holds files the kit moved out of the way. Keep git from seeing it (local exclude file)."""
    if git(root, "check-ignore", "-q", "_to_delete/x")[0] != 0:
        ex = Path(git(root, "rev-parse", "--git-path", "info/exclude")[1].strip())
        ex = ex if ex.is_absolute() else root / ex
        ex.parent.mkdir(parents=True, exist_ok=True)
        with open(ex, "a", encoding="utf-8") as f:
            f.write("\n_to_delete/\n")


def remove_file(root, path, label):
    """Delete a file; where deleting is not permitted (some agent sandboxes), move it to _to_delete/<label>/."""
    src = root / path
    try:
        src.unlink()
        return "deleted"
    except FileNotFoundError:
        return "absent"
    except OSError:
        set_trash_ignored(root)
        dst = root / "_to_delete" / label / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        return "moved"


def restore_blob(root, path, blob, rev="HEAD"):
    """Write a committed version back as git recorded it: a regular file (with its executable bit), or a
    symbolic link where git stores one and this copy uses links (core.symlinks)."""
    no_link_above(root, path)
    mode = tracked_mode(root, rev, path)
    if mode == "120000" and git(root, "config", "--get", "core.symlinks")[1].strip().lower() != "false":
        tmp = root / f"{path}.kac-link-{os.getpid()}.tmp"
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.fsdecode(blob), tmp)    # the blob of a link is its target
            os.replace(tmp, root / path)
            return
        except (OSError, NotImplementedError):    # no privilege for links (Windows): a plain file, as git writes it there
            pass
    atomic_write(root / path, blob)
    if mode == "100755":
        try:
            os.chmod(root / path, 0o755)
        except OSError:
            pass


def refresh_stat(root, paths, guard=False):
    """Tell git that these files, just written with the bytes it stores, are unchanged. Needed because git
    reports a file whose size changed as modified without comparing its content (a copy that had CRLF line
    endings is the usual case). With `guard`, anything this would stage is unstaged again."""
    for i in range(0, len(paths), 100):
        chunk = paths[i:i + 100]
        git(root, "add", "--", *chunk, readonly=False)
        if guard and git(root, "diff", "--cached", "--quiet", "--", *chunk)[0] != 0:
            git(root, "reset", "-q", "--", *chunk, readonly=False)


def set_aside(root, cfg, label, keep=()):
    """Bring the working tree back to the last commit WITHOUT deleting anything: each changed file is moved to
    _to_delete/<label>/ and the committed version is written back. Paths in `keep` (changes that were there
    before the run began) and unmanaged paths are left exactly as they are, staged versions included. Nothing
    is read or written through a symbolic link. Returns the number of items set aside."""
    set_trash_ignored(root)
    trash, keep, n, restored = root / "_to_delete" / label, set(keep), 0, []
    kit_dir = Path(__file__).resolve().parent
    filemode = git(root, "config", "--get", "core.filemode")[1].strip().lower() != "false"

    def move(src, relpath):
        dst = trash / relpath
        while dst.exists() or dst.is_symlink():
            dst = dst.with_name(dst.name + "-2")
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    for st, path, _ in porcelain(root):           # a version that exists only in git's index would vanish with the
        if st[0] in "MARC" and not unmanaged(path, cfg) and path not in keep:      # unstaging below: keep it as a file
            staged, f = git_show(root, "", path), root / path
            cur = f.read_bytes() if f.is_file() and not f.is_symlink() else None
            if staged is not None and staged != cur and staged != git_show(root, "HEAD", path):
                dst = trash / (path + ".staged")
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(staged)
                n += 1
    git(root, "reset", "-q", "--", ".", *[f":(exclude,literal){k}" for k in sorted(keep)], readonly=False)   # unstage only
    for st, path, _ in porcelain(root):
        if unmanaged(path, cfg) or path in keep:
            continue
        src = root / path
        if "?" in st and src.name in ("kac.py", "kac_mcp.py") and src.parent.resolve() == kit_dir and not src.is_symlink():
            continue                              # the kit itself, copied in but not committed yet: it stays
        blob = git_show(root, "HEAD", path)
        mode = tracked_mode(root, "HEAD", path) if blob is not None else ""
        blocked = next((up for up in reversed(Path(path).parents)
                        if str(up) != "." and ((root / up).is_symlink() or (root / up).is_file())), None)
        if blocked is not None and blob is None:
            continue                              # an untracked path below something that is not a real folder
        if blocked is not None:                   # a file or a link stands where a folder of this path belongs:
            move(root / blocked, blocked.as_posix())      # it is moved as it is, never followed
            n += 1
        if blob is not None and mode != "120000" and src.is_file() and not src.is_symlink() and src.read_bytes() == blob:
            if filemode and mode in ("100644", "100755"):      # the committed bytes already: at most the executable
                try:                                           # bit differs
                    bits = stat.S_IMODE(os.stat(src).st_mode)
                    want = bits | 0o111 if mode == "100755" else bits & ~0o111
                    if want != bits:
                        os.chmod(src, want)
                except OSError:
                    pass
            restored.append(path)
            continue
        if src.is_file() or src.is_symlink():
            move(src, path)
            n += 1
        if blob is not None:
            if src.is_dir() and not src.is_symlink():          # a folder took the place of a committed file
                move(src, path)
                n += 1
            restore_blob(root, path, blob)
            restored.append(path)
    refresh_stat(root, restored, guard=True)
    set_trash_ignored(root)                       # again: a restored .gitignore may no longer list _to_delete/
    return n


def porcelain(root, *paths):
    """[(XY, path, old_path_or_None)] from `git status`, paths relative to the wiki folder. NUL-separated
    output, so any file name is parsed correctly; entries outside the wiki folder are left out."""
    rc, out, _ = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *(paths or (".",)))
    pre = prefix(root)
    rows, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        st, path, old = entry[:2], entry[3:], None
        if st[0] in "RC" or st[1] in "RC":        # the next field is the old name of a rename/copy
            old = parts[i] if i < len(parts) else None
            i += 1
        if pre:                                   # git reports paths from the top of the repository
            if not path.startswith(pre):
                continue
            path = path[len(pre):]
            old = old[len(pre):] if old and old.startswith(pre) else old
        rows.append((st, path, old))
    return rows


def raw_violations(root, cfg):
    """Tracked files under raw/ that were modified, deleted, renamed or type-changed. Evidence is immutable and an
    ordinary run has no exception: a copy that differs from git in its line endings is settled by a person with
    `kac normalize`, never by a commit."""
    if not is_repo(root):
        return []
    excluded = lambda x: bool(x) and any(fnmatch.fnmatch(x, pat) for pat in cfg["raw_exclude"])   # noqa: E731
    bad, eol, erased = [], None, None
    for st, path, old in porcelain(root, cfg["raw"]):
        if "?" in st or st[0] == "A":             # new evidence is welcome; changing old evidence is not
            continue
        if excluded(path) or excluded(old):
            continue                              # the drop zone, or a file moved from it into raw/
        if "D" in st and not (root / path).exists():
            if erased is None:                    # an erasure: the file is gone and the manifest says so (section 5.9)
                live, rows, _ = load_manifest(root)
                erased = {r.get("path") for r in rows if r.get("erased")} - set(live)
            if path in erased:
                continue
        if eol is None:
            eol = set(converted_checkout(root, cfg["raw"])) if has_head(root) else set()
        bad.append(f"{st.strip()} {path}" + (" (only its line endings differ from git's copy: settle that with `kac normalize`, "
                                             "not with git restore)" if path in eol else ""))
    return bad


# ----------------------------------------------------------------------------- manifest and pins
def manifest_path(root):
    return root / ".kac" / "manifest.jsonl"


def raw_files(root, cfg):
    base = root / cfg["raw"]
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.name.endswith(".tmp") or p.name.startswith("._") or \
                p.name in (".keep", ".gitkeep", "desktop.ini", ".DS_Store", "Thumbs.db"):
            continue
        r = rel(root, p)
        if any(fnmatch.fnmatch(r, pat) for pat in cfg["raw_exclude"]):
            continue
        out.append(p)
    return out


def load_manifest(root):
    rows, bad = jl_read(manifest_path(root))
    live = {}
    for r in rows:
        if r.get("erased") or r.get("removed"):      # tombstones: purged from history / withdrawn by an undo
            live.pop(r["path"], None)
        else:
            live[r["path"]] = r
    return live, rows, bad


def ignored_by_git(root, paths):
    """The subset of paths (relative to the wiki folder) that git ignores, i.e. files kept outside git."""
    if not paths or not is_repo(root):
        return set()
    ask = {}
    for x in paths:                               # git refuses a path beyond a symbolic link: ask about the link
        link = next((up.as_posix() for up in reversed(Path(x).parents) if str(up) != "." and (root / up).is_symlink()), x)
        ask.setdefault(link, []).append(x)
    r = subprocess.run(["git", "-c", f"safe.directory={repo_top(root)}", "check-ignore", "-z", "--stdin"],
                       cwd=str(root), input="\0".join(ask).encode("utf-8"), capture_output=True)
    if r.returncode in (0, 1):
        hit = {x for x in r.stdout.decode("utf-8", errors="replace").split("\0") if x}
    else:                                         # one path git cannot take stops the whole list: ask one by one
        hit = {x for x in ask if git(root, "check-ignore", "-q", "--", x)[0] == 0}
    return {x for q in hit for x in ask.get(q, [])}


def register_raw(root, cfg, run_id):
    """Append manifest lines for raw files that are not registered yet: new files, and tracked files whose copy
    here is exactly what git stores. A file that an attempt registered but never committed is hashed again, so
    that the manifest holds the bytes that are finally committed. Returns the number of lines added."""
    live, _, _ = load_manifest(root)
    fresh, differs = set(), set()
    if is_repo(root):
        for st, path, _ in porcelain(root, cfg["raw"]):
            (fresh if "?" in st or st[0] == "A" else differs).add(path)
        if has_head(root):
            differs |= set(converted_checkout(root, cfg["raw"]))
    todo = []
    for p in raw_files(root, cfg):
        r = rel(root, p)
        if r in differs:
            continue                              # tracked, but this copy is not what git stores: it is registered once
        if r not in live or (r in fresh and (live[r].get("bytes") != p.stat().st_size or live[r].get("sha256") != sha256_file(p))):      # that is settled
            todo.append((r, p))
    outside = ignored_by_git(root, [r for r, _ in todo])
    add = []
    for r, p in todo:
        line = {"path": r, "sha256": sha256_file(p), "bytes": p.stat().st_size,
                "added": now(cfg).isoformat(timespec="seconds"), "run": run_id}
        if r in outside:
            line["external"] = True               # kept outside git (large media): a clone will not have it
        add.append(line)
    if add or not manifest_path(root).exists():
        append_text(manifest_path(root), "".join(canon(a) + "\n" for a in add))
    return len(add)


def some(items, n=8):
    items = list(items)
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def instruction_files(root, cfg):
    """Every file an agent may take instructions or code from: what kit.instructions matches, plus AGENTS.md,
    CLAUDE.md and GEMINI.md in any folder (agents load those wherever they work), without the bytecode caches
    in __pycache__ folders and without the paths under kit.unpinned (which is itself a pinned file, so every
    exception is reviewed). A compiled file anywhere else counts: Python would import it in place of a module."""
    nested, walked = [], set()
    for folder, subdirs, names in os.walk(root, followlinks=True):      # links are followed, as an agent follows them
        real = os.path.realpath(folder)
        if real in walked or len(walked) >= WALK_CAP:
            subdirs[:] = []
            continue
        walked.add(real)
        here_ = Path(folder).relative_to(root).as_posix()
        pre = "" if here_ == "." else here_ + "/"
        subdirs[:] = sorted(d for d in subdirs if d != ".git" and not (pre + d + "/").startswith(side_dirs(cfg))
                            and not matches_any(pre + d, cfg["unpinned"]))
        nested += [Path(folder) / n for n in names if n.lower() in ANYWHERE]      # also claude.md, Agents.md
    hits = dict.fromkeys(glob_many(root, cfg["instructions"], cfg["unpinned"]) + nested)
    return [p for p in hits if p.is_file() and not p.name.endswith((".tmp", ".DS_Store"))
            and not (p.name.endswith((".pyc", ".pyo")) and p.parent.name == "__pycache__")
            and not matches_any(rel(root, p), cfg["unpinned"])]


def sha256_text(p):
    """Hash of an instruction file with line endings normalized, so that a checkout that converts them
    (Windows) and one that does not produce the same pin and the same fingerprint."""
    data = Path(p).read_bytes()
    if b"\0" not in data[:8000]:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def split_pins(root, cfg):
    """({path: hash} shared, {path: hash} local). Shared: instruction files that git tracks or would track; they
    go into .kac/pins.json. Local: instruction files that git ignores (CLAUDE.local.md, local settings); they
    exist in this copy only, so they are pinned for this copy only, inside the .git folder."""
    files = instruction_files(root, cfg)
    rels = [rel(root, p) for p in files]
    ign = ignored_by_git(root, rels)
    hashes = {r: sha256_text(p) for r, p in zip(rels, files)}
    return {r: h for r, h in hashes.items() if r not in ign}, {r: h for r, h in hashes.items() if r in ign}


def compute_pins(root, cfg):
    return split_pins(root, cfg)[0]


def pins_path(root):
    return root / ".kac" / "pins.json"


def local_pins_path(root):
    """Where the pins of this copy's git-ignored instruction files are kept: inside .git, so they are never
    committed, never show up as a change, and never travel to another copy."""
    if not is_repo(root):
        return None
    tag = "-" + hashlib.sha256(prefix(root).encode()).hexdigest()[:8] if prefix(root) else ""
    out = git(root, "rev-parse", "--git-path", f"kac-pins-local{tag}.json")[1].strip()
    return (Path(out) if os.path.isabs(out) else root / out) if out else None


def read_pins(path):
    """The `files` mapping of a pins file; None when the file is unreadable."""
    try:
        files = json.loads(Path(path).read_text(encoding="utf-8")).get("files", {})
        return files if isinstance(files, dict) else None
    except (ValueError, AttributeError, OSError):
        return None


def pin_diff(root, cfg):
    """None when nothing is pinned yet, else (changed, added, removed) instruction files."""
    p = pins_path(root)
    if not p.exists():
        return None
    old = read_pins(p)
    if old is None:
        return [".kac/pins.json (unreadable: restore it from git)"], [], []
    lp = local_pins_path(root)
    old_local = (read_pins(lp) or {}) if lp and lp.exists() else {}
    shared, local = split_pins(root, cfg)
    changed = sorted([k for k in shared if k in old and old[k] != shared[k]]
                     + [k for k in local if k in old_local and old_local[k] != local[k]])
    added = sorted([k for k in shared if k not in old] + [k for k in local if k not in old_local])
    removed = sorted(k for k in {**old, **old_local} if k not in shared and k not in local)
    return changed, added, removed


def pins_digest(files, local=None):
    """One short fingerprint of all instruction files; keep a copy where the agent cannot write. `local`
    (what only this copy has, see local_surface) is added only when it is not empty, so a plain repository
    has the same fingerprint in every copy."""
    return hashlib.sha256((canon(files) + (canon(local) if local else "")).encode()).hexdigest()[:16]


RUNS_CODE = re.compile(r"(core\.(fsmonitor|hookspath|sshcommand|editor|pager|askpass|gitproxy|alternaterefscommand)"
                       r"|sequence\.editor|diff\.external|diff\..+\.(textconv|command)|merge\..+\.driver"
                       r"|(mergetool|difftool)\..+\.(cmd|path)|filter\..+\.(clean|smudge|process)|alias\..+"
                       r"|credential\.(.+\.)?helper|gpg\.(.+\.)?(program|defaultkeycommand)|pager\..+"
                       r"|include\.path|includeif\..+\.path"
                       r"|uploadpack\.packobjectshook|remote\..+\.(uploadpack|receivepack|proxy|vcs)"
                       r"|submodule\..+\.update|trailer\..+\.(command|cmd)|interactive\.difffilter"
                       r"|(browser|man)\..+\.(cmd|path))", re.I)
SENDS_DATA = re.compile(r"(remote\..+\.(url|pushurl)|url\..+\.(insteadof|pushinsteadof)|submodule\..+\.url"
                        r"|http\.(.+\.)?proxy)", re.I)


def local_surface(root):
    """What can run a program or redirect a push in THIS copy of the repository without any review: active git
    hooks, and the git settings of this repository (its config, included files, worktree config) that name a
    program or a remote. Nothing of this is versioned, and an agent that can write to .git can change it.
    Returns {} for a plain repository."""
    out = {}
    if not is_repo(root):
        return out
    hd = git(root, "rev-parse", "--git-path", "hooks")[1].strip()
    hd = Path(hd) if os.path.isabs(hd) else root / hd
    try:
        hooks = sorted(f for f in hd.iterdir() if f.is_file() and not f.name.endswith(".sample")) if hd.is_dir() else []
    except OSError:
        hooks = []
    for f in hooks:
        out[f"hook:{f.name}"] = sha256_text(f)
    parts = git(root, "config", "--list", "--show-scope", "--includes", "-z")[1].split("\0")
    for scope, item in zip(parts[0::2], parts[1::2]):          # scope NUL key LF value NUL
        if scope not in ("local", "worktree"):
            continue                              # the user's and the system's settings are not this repository's
        key, _, val = item.partition("\n")
        if RUNS_CODE.fullmatch(key) or SENDS_DATA.fullmatch(key):
            name = "setting:" + key.lower()
            out[name] = (out[name] + "\n" if name in out else "") + val
    return out


def fingerprint(root, cfg):
    """(fingerprint, names of the items it covers that exist in this copy only)."""
    shared, local = split_pins(root, cfg)
    extra = local_surface(root)
    extra.update({f"file:{k}": v for k, v in local.items()})
    return pins_digest(shared, extra), sorted(extra)


def write_pins(root, cfg):
    shared, local = split_pins(root, cfg)
    atomic_write(pins_path(root), json.dumps({"kit": VERSION, "files": shared}, indent=1, sort_keys=True) + "\n")
    lp = local_pins_path(root)
    if lp and (local or lp.exists()):
        atomic_write(lp, json.dumps({"kit": VERSION, "files": local}, indent=1, sort_keys=True) + "\n")
    return len(shared) + len(local)


def missing_but_tracked(root, cfg):
    """Kit files that git has but the folder lacks. Removing one must never switch a check off."""
    if not is_repo(root) or not has_head(root):
        return []
    names = [".kac/pins.json", ".kac/manifest.jsonl"] + ([cfg["log"]] if cfg["log"] else [])
    return [n for n in names if not (root / n).exists() and git_show(root, "HEAD", n) is not None]


# ----------------------------------------------------------------------------- built-in lint
def invisible_report(text):
    """Count hidden characters. Tag-block, variation-selector-supplement and bidi-override characters are
    treated as critical: a person cannot see them and they can carry instructions."""
    tags = bidi = zero = 0
    for ch in text:
        o = ord(ch)
        if o < 0xAD:
            continue
        if 0xE0000 <= o <= 0xE007F or 0xE0100 <= o <= 0xE01EF:
            tags += 1
        elif o in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069):
            bidi += 1
        elif o in (0xAD, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x180E, 0x034F) \
                or 0x2061 <= o <= 0x2064 or 0xFE00 <= o <= 0xFE0F:
            zero += 1
    return tags, bidi, zero


def builtin_lint(root, cfg, allow_raw=False, lenient=False, since=None, removing=()):
    """Safety problems are always errors. Page problems are errors in pages this run touched (or everywhere
    when kit.strict is true) and warnings in the rest, so an older wiki can adopt the kit without a big cleanup."""
    errs, warns = [], []
    b = cfg["budgets"]
    for v in ([] if allow_raw else raw_violations(root, cfg)):
        errs.append(f"raw is immutable, but git shows: {v}" + ("" if "kac normalize" in v else " (restore it with `git restore -- <path>`)"))
    for n in missing_but_tracked(root, cfg):
        if n not in removing:                     # an undo of the adopt run takes the pins away on purpose
            errs.append(f"{n} is in git but missing from the folder; restore it (`git restore -- {n}`)")
    d = pin_diff(root, cfg)
    if d and (d[0] or d[2]):
        errs.append("instruction files changed since they were pinned: " + some(d[0] + d[2])
                    + " (review `git diff`, then `kac pin` or `kac commit ... --repin`)")
    if d and d[1]:
        deps = sorted({"/".join(x.split("/")[:i + 1]) for x in d[1] for i, part in enumerate(x.split("/")) if part in DEP_DIRS})
        errs.append("new instruction files that are not pinned: " + some(d[1])
                    + " (review them, then `kac pin` or `kac commit ... --repin`"
                    + (f"; a dependency folder such as {deps[0]} is better listed under `kit: unpinned:` in kac.yaml" if deps else "") + ")")
    touched = {nfc(x) for x in changed_paths(root, cfg)} if is_repo(root) else set()
    if since:                                     # CI: pages changed on this branch count as touched
        rc, out, err = git(root, "diff", "--name-only", "--relative", f"{since}...HEAD")
        if rc != 0:
            errs.append(f"--since {since}: git cannot compare with that ref ({err.strip().splitlines()[-1][:120] if err.strip() else 'unknown ref'})")
        touched |= {nfc(x) for x in out.splitlines() if x}
    strict = lambda r: (cfg["strict"] or nfc(r) in touched) and not lenient      # noqa: E731
    ids = {}
    for p in iter_pages(root, cfg):
        r = rel(root, p)
        hard = errs if strict(r) else warns
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            hard.append(f"{r}: {e}")
            continue
        tags, bidi, zero = invisible_report(text)
        if tags or bidi:
            hard.append(f"{r}: hidden Unicode ({tags} tag or variation-selector, {bidi} bidi-override characters); quarantine and inspect")
        elif zero > 20:
            warns.append(f"{r}: {zero} zero-width characters")
        if re.search(r"^<{7} ", text, re.M) and re.search(r"^>{7} ", text, re.M):
            errs.append(f"{r}: unresolved merge-conflict markers")
        if SECRET.search(text):
            hard.append(f"{r}: looks like it contains a secret")
        meta, body, err = parse_page(text)
        if err:
            hard.append(f"{r}: {err}")
            continue
        if not meta.get("type"):
            hard.append(f"{r}: front matter has no `type`")
        if meta.get("id") and not str(meta["id"]).endswith(p.stem):
            warns.append(f"{r}: id {meta['id']!r} does not match the file name")
        pid = page_id(p, meta)
        if pid in ids and meta.get("type") != "redirect":          # an error when either of the two is in this run
            (errs if strict(r) or strict(ids[pid]) else warns).append(f"{r}: duplicate id {pid!r} (also {ids[pid]})")
        ids.setdefault(pid, r)
        why = active_findings(body) if cfg["inert_pages"] else []
        if why:
            hard.append(f"{r}: active content ({some(dict.fromkeys(why), 3)}); pages must be inert")
        odd = [c for c in hidden_comments(body) if not re.match(cfg["allowed_comments"], c)] \
            + re.findall(r"^\[//\]:[ \t]*#[ \t]*(.+)$", body, re.M)             # [//]: # (text) is a comment too
        if odd:
            warns.append(f"{r}: {len(odd)} HTML comment(s) that people do not see but agents read: {odd[0].strip()[:60]!r}")
        raw_card = next((str(meta[f]) for f in cfg["card_fields"] if meta.get(f)), "")
        if not raw_card and meta.get("type") not in ("redirect", "index"):
            warns.append(f"{r}: no card ({' or '.join(cfg['card_fields'])})")
        elif len(raw_card) > 2 * b["card_chars"]:
            warns.append(f"{r}: card is {len(raw_card)} chars (budget {b['card_chars']}); listings cut it")
        if len(text.encode()) > b["page_kb"] * 1024:
            warns.append(f"{r}: {len(text.encode()) // 1024} KB (budget {b['page_kb']} KB); split it or move detail to raw/")
        when = stale_date(stale_value(meta, cfg))
        if when == "bad":
            warns.append(f"{r}: {'/'.join(cfg['stale_fields'])} is not an ISO date or datetime")
        elif when and dt.datetime.now(dt.timezone.utc) >= when:
            warns.append(f"{r}: stale since {stale_value(meta, cfg)}; re-verify against its sources")
    for pat in cfg["records"]:
        for f in glob_many(root, [pat]):
            r = rel(root, f)
            rows, bad = jl_read(f)
            if bad:
                errs.append(f"{r}: invalid JSON on line(s) {bad[:5]}")
            seen = Counter(str(x.get("id")) for x in rows if isinstance(x, dict) and x.get("id"))
            dup = [k for k, n in seen.items() if n > 1]
            if dup:
                errs.append(f"{r}: duplicate ids {dup[:5]}")
            tags, bidi, _ = invisible_report(f.read_text(encoding="utf-8", errors="replace"))
            if tags or bidi:
                (errs if strict(r) else warns).append(f"{r}: hidden Unicode in records ({tags} tag, {bidi} bidi-override characters)")
    for name in ("AGENTS.md", "CLAUDE.md"):
        f = root / name
        if f.exists():
            text = f.read_text(encoding="utf-8", errors="replace")
            n, size = len(text.splitlines()), len(text.encode())
            if n > b["instructions_lines"] or size > b["instructions_kb"] * 1024:
                warns.append(f"{name}: {n} lines, {size / 1024:.0f} KB (budget {b['instructions_lines']} lines and "
                             f"{b['instructions_kb']} KB); move procedures to skills or docs")
    idx = root / "index.md"
    if idx.exists() and idx.stat().st_size > b["index_kb"] * 1024:
        warns.append(f"index.md: {idx.stat().st_size // 1024} KB (budget {b['index_kb']} KB); see the guide, section 7.3")
    if cfg["log"] and (root / cfg["log"]).is_file() and is_repo(root) and has_head(root):
        old = git_show(root, "HEAD", cfg["log"])
        cur = (root / cfg["log"]).read_bytes().replace(b"\r\n", b"\n")
        if old is not None and not cur.startswith(old.replace(b"\r\n", b"\n").rstrip(b"\n")):
            warns.append(f"{cfg['log']}: earlier entries changed; the log is append-only")
    return errs, warns


def stale_value(meta, cfg):
    return next((meta[f] for f in cfg["stale_fields"] if meta.get(f)), None)


def stale_date(value):
    """An aware datetime, None when unset, or "bad"."""
    if not value:
        return None
    try:
        if isinstance(value, dt.datetime):
            when = value
        elif isinstance(value, dt.date):
            when = dt.datetime(value.year, value.month, value.day)
        else:
            when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        return "bad"


def cmd_check(root, cfg, a=None, quiet=False, allow_raw=False, lenient=False, removing=()):
    errs, warns = builtin_lint(root, cfg, allow_raw, lenient or bool(a and getattr(a, "lenient", False)),
                               getattr(a, "since", None) if a else None, removing)
    for w in warns[:40]:
        if not quiet:
            print("WARN ", w)
    if len(warns) > 40 and not quiet:
        print(f"WARN  ... {len(warns) - 40} more warnings")
    for e in errs:
        print("ERROR", e)
    ran = 0
    if not errs:
        for c in cfg["checks"]:
            rc, out = sh(c, root, shell=True)
            ran += 1
            tail = "\n".join(out.strip().splitlines()[-6:])
            if rc != 0:
                print(f"ERROR check failed ({rc}): {c}\n{tail}")
                errs.append(c)
                break
            if not quiet and tail:
                print(f"ok    {c}: {tail.splitlines()[-1][:120]}")
    print(f"check: {len(errs)} errors, {len(warns)} warnings, {ran}/{len(cfg['checks'])} configured checks run")
    return 1 if errs else 0


# ----------------------------------------------------------------------------- commit, abort, undo
def log_entry(cfg, op, title, bullets):
    t = now(cfg)
    stamp = t.strftime("%Y-%m-%d %H:%M ") + (cfg["tz_label"] or t.strftime("%Z") or "UTC")
    lines = [f"\n## [{stamp}] {op} | {title}"] + [f"- {b}" for b in bullets]
    return "\n".join(lines) + "\n"


INDEX_MARK = "_Generated by `kac index`"
INDEX_STUB = "_Superseded. See index.md._\n"      # what replaces a card list where deleting is not permitted


def index_head(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(400).replace("\r\n", "\n")
    except OSError:
        return ""


def kit_generated(path):
    """True for an index file that `kac index` wrote: it carries the marker in its first lines, or it is a stub."""
    head = index_head(path)
    return INDEX_MARK in head or head == INDEX_STUB


def refresh_index(root, cfg):
    """If index.md was written by `kac index`, keep it current (a stale map sends agents to the wrong place).
    Files that the kit did not write are never replaced here."""
    if kit_generated(root / "index.md"):
        for name, text in render_index(root, cfg).items():
            f = root / name
            if f.exists() and not kit_generated(f):
                continue
            if not f.exists() or f.read_text(encoding="utf-8") != text:
                atomic_write(f, text)


def git_busy(root):
    """A sentence when git is in the middle of a merge, rebase, cherry-pick or revert, else ''. A commit or an
    abort in that state would record or drop one side of it."""
    for name, what in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
                       ("rebase-merge", "rebase"), ("rebase-apply", "rebase")):
        g = git(root, "rev-parse", "--git-path", name)[1].strip()
        if g and (Path(g) if os.path.isabs(g) else root / g).exists():
            return f"git is in the middle of a {what}. Finish it or take it back with git (`git {what} --abort`) first."
    return ""


def require_branch(root):
    """A commit made on a detached HEAD (during a bisect, after checking out a tag) is lost at the next checkout."""
    if git(root, "symbolic-ref", "-q", "HEAD")[0] != 0:
        die("HEAD is detached (a bisect or a checked-out tag?). Switch back to a branch before writing.")
    busy = git_busy(root)
    if busy:
        die(busy)


def cmd_begin(root, cfg, a):
    if not is_repo(root):
        die("not a git repository (run `git init` first)")
    require_branch(root)
    dirty = changed_paths(root, cfg)
    if dirty and not (a.allow_dirty or a.steal):
        if not has_head(root):
            die("this repository has no commit yet. Make the first one with `kac commit schema \"start\"` "
                "(add --lenient when the folder already holds older pages).")
        die(f"{len(dirty)} uncommitted change(s) from an earlier run, e.g. {dirty[:3]}. A person decides what they "
            "are: `kac commit recover \"<what they are>\"` keeps them, `kac abort` sets them aside.")
    info = take_lock(root, cfg, a.op, a.title, a.steal, dirty=dirty if a.allow_dirty and not a.steal else None)
    print(json.dumps({"run": info["run"], "op": a.op, "title": a.title}))
    return 0


def changed_paths(root, cfg=None):
    """Paths with uncommitted changes, relative to the wiki folder, without what the kit does not manage."""
    return [p for st, p, _ in porcelain(root) if not unmanaged(p, cfg)]


def altered_evidence(root, cfg):
    """Staged evidence files whose blob is not the file's bytes (git converted line endings on the way in)."""
    names = [x for x in git(root, "diff", "--cached", "--name-only", "--diff-filter=AM", "--relative", "-z", "--",
                            cfg["raw"])[1].split("\0") if x and (root / x).is_file() and not (root / x).is_symlink()]
    bad = []
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        staged = {}
        for item in git(root, "ls-files", "-s", "-z", "--", *chunk)[1].split("\0"):
            meta, _, path = item.partition("\t")
            if path:
                staged[path] = meta.split()[1]
        plain = git(root, "hash-object", "--no-filters", "--", *chunk)[1].split()
        filtered = filtered_paths(root, chunk)
        bad += [x for x, oid in zip(chunk, plain) if staged.get(x) not in (None, oid) and x not in filtered]
    return bad


def filtered_paths(root, paths):
    """Paths that go through a configured clean/smudge filter (Git LFS and the like): git stores something else
    for them on purpose. A `filter=` attribute whose driver is not configured does nothing and does not count."""
    out, drivers = set(), {}
    for i in range(0, len(paths), 100):
        attrs = git(root, "check-attr", "-z", "filter", "--", *paths[i:i + 100])[1].split("\0")
        for j in range(0, len(attrs) - 2, 3):
            name = attrs[j + 2]
            if name in ("unspecified", "unset", "set"):
                continue
            if name not in drivers:
                drivers[name] = any(git(root, "config", "--get", f"filter.{name}.{k}")[1].strip() for k in ("clean", "process"))
            if drivers[name]:
                out.add(attrs[j])
    return out


def git_commit(root, cfg, lines):
    """Stage everything the kit manages and commit with the given message lines. Returns (rc, output)."""
    cands = ["*.tmp"] + [d.rstrip("/") for d in side_dirs(cfg)] + [pat[:-2] for pat in cfg["raw_exclude"] if pat.endswith("/*")]
    excl = []
    for c in cands:                               # git refuses an exclude that names a path it already ignores
        if git(root, "check-ignore", "-q", "--", "probe.tmp" if c == "*.tmp" else c + "/probe")[0] != 0:
            excl.append(f":(exclude){c}")
    rc, out, err = git(root, "add", "-A", "--", ".", *excl, readonly=False)
    if rc != 0:                                   # e.g. a nested repository: never commit half of a run
        return rc, "git add failed: " + (out + err).strip()
    altered = altered_evidence(root, cfg)
    if altered:                                   # e.g. core.autocrlf=true, or `* text=auto`, without the raw rule
        git(root, "reset", "-q", "--", ".", readonly=False)
        return 1, (f"git would store {len(altered)} evidence file(s) with converted line endings, for example {altered[0]}. "
                   f"Evidence is stored byte for byte: `kac adopt --apply` adds `{cfg['raw']}/** -text` to .gitattributes; "
                   "then run the commit again with --repin.")
    fd, name = tempfile.mkstemp(prefix="kac-msg-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    stamp = now(cfg).isoformat(timespec="seconds")                # commit times in the wiki's own time zone
    only = ["--", "."] if prefix(root) else []                    # inside a larger repository: this folder only
    rc, out, err = git(root, "commit", "-q", "-F", name, *only, readonly=False,
                       env_add={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    try:
        os.unlink(name)
    except OSError:
        pass
    return rc, out + err


def clean_trailers(items):
    extra = [x.strip() for x in items if x and x.strip()]
    bad = [x for x in extra if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*: \S.*", x)]
    if bad:                                       # free text here would hide the Run-Id trailer from git
        die(f"not a trailer: {bad[0]!r} (use `Key: value`, for example -t \"Model: opus-5.5\")")
    return extra


def cmd_commit(root, cfg, a):
    if not is_repo(root):
        die("not a git repository (run `git init` first)")
    require_branch(root)
    extra = clean_trailers(list(a.trailer or []) + os.environ.get("KAC_TRAILERS", "").splitlines())
    if a.op:
        a.op = re.sub(r"[^A-Za-z0-9_-]+", "-", a.op).strip("-") or "edit"
    if a.title:
        a.title = " ".join(a.title.split())       # one line: the title is the commit subject
    info = read_lock(root)
    if a.run and (not info or info.get("run") != a.run):
        die(f"run {a.run} does not hold the writer lock (was it aborted?)", 3)
    if info and not a.steal:
        age_h = (time.time() - float(info.get("started", 0))) / 3600
        if info.get("unreadable"):
            print("kac: the writer lock is unreadable (a run died while taking it). A person decides: `kac abort` sets "
                  "the uncommitted changes aside, `kac commit OP TITLE --steal` keeps them.", file=sys.stderr)
            return 3
        same = a.op == info.get("op") and a.title == info.get("title")       # the same command again is a retry
        if (a.op or a.title) and not (a.run or same):
            print(f"kac: writer lock held by run {info.get('run')} ({info.get('op')} | {info.get('title')}), "
                  f"{age_h:.1f} h old. If it is your run, finish it with plain `kac commit` (or `kac commit --run "
                  f"{info.get('run')}`). If it is dead, a person decides: `kac commit OP TITLE --steal` or `kac abort`.",
                  file=sys.stderr)
            return 3
        info["op"], info["title"] = a.op or info.get("op"), a.title or info.get("title")
    else:
        if not (a.op and a.title):
            die('usage: kac commit OP "TITLE"   (or run `kac begin OP TITLE` first)')
        info = take_lock(root, cfg, a.op, a.title, a.steal)
    op, title, run_id = info["op"], info["title"], info["run"]
    if manifest_path(root).exists():
        n = register_raw(root, cfg, run_id)
        if n:
            print(f"manifest: registered {n} new raw file(s)")
    refresh_index(root, cfg)
    if a.repin:
        write_pins(root, cfg)
    if cmd_check(root, cfg, quiet=True, lenient=getattr(a, "lenient", False)) != 0:
        print(f"kac: run {run_id} NOT committed. Fix the errors and run `kac commit` again, or `kac abort`."
              + ("" if has_head(root) else " For the first commit of a folder that already holds older pages, "
                 "`kac commit --lenient` turns page problems into warnings."))
        return 1
    changed = changed_paths(root, cfg)
    if not changed:
        print("nothing to commit")
        drop_lock(root)
        return 0
    logf = cfg["log"]
    if logf:
        mark = f"- run: {run_id}"
        tail = (root / logf).read_text(encoding="utf-8", errors="replace").rstrip().splitlines()[-1:] \
            if (root / logf).exists() else []
        if tail != [mark]:
            if logf in changed and (root / logf).exists():      # the agent wrote its own entry: attach the run id
                append_text(root / logf, mark + "\n")
            else:
                by = Counter(p.split("/")[0] for p in changed)
                summary = ", ".join(f"{k} {v}" for k, v in sorted(by.items()))
                append_text(root / logf, log_entry(cfg, op, title, [f"changed: {summary}", f"run: {run_id}"]))
    rc, out = git_commit(root, cfg, [f"{op}: {title}", "", f"Run-Id: {run_id}", f"Op: {op}",
                                f"Agent: {os.environ.get('KAC_AGENT') or cfg['agent']}"] + extra)
    if rc != 0:
        print(f"kac: git commit failed (the lock is kept):\n{out}")
        return 1
    drop_lock(root)
    print(json.dumps({"run": run_id, "commit": git(root, "rev-parse", "--short", "HEAD")[1].strip(),
                      "files": len(changed), "op": op, "title": title}))
    return 0


def cmd_abort(root, cfg, a):
    if not is_repo(root):
        die("not a git repository")
    busy = git_busy(root)
    if busy:
        die(busy)
    info = read_lock(root) or {}
    if not has_head(root):                        # nothing to go back to: touch no file, only free the lock
        drop_lock(root)
        print("this repository has no commit yet, so no file was touched; the writer lock is free")
        return 0
    label = f"abort-{info.get('run') if info.get('run') not in (None, 'unknown') else now(cfg).strftime('%Y%m%d-%H%M%S')}"
    keep = [x for x in (info.get("dirty") or []) if isinstance(x, str)]
    eol = [x for x in converted_checkout(root, cfg["raw"]) if x not in keep]      # evidence: a person decides which copy is the original
    n = set_aside(root, cfg, label, keep + eol) if changed_paths(root, cfg) else 0
    drop_lock(root)
    print(f"set aside {n} changed file(s) in _to_delete/{label}/ (nothing was deleted); the wiki is back at its "
          "last commit." if n else "no changes to set aside")
    if keep:
        print(f"left as they were, because they were changed before the run began: {', '.join(keep[:5])}")
    if eol:
        print(f"left as they are: {len(eol)} evidence file(s) whose line endings differ from git's copy ({some(eol, 3)}). "
              "Settle them with `kac normalize` (guide section 5.7).")
    same = [x for x in changed_paths(root, cfg) if x not in keep and x not in eol and (root / x).is_file()
            and (root / x).read_bytes() == git_show(root, "HEAD", x)]
    if same:                                      # git stores them with CRLF while .gitattributes asks for LF
        print(f"note: {len(same)} file(s) have the committed bytes but git still lists them as changed ({some(same, 3)}), "
              "because it stores them with CRLF while .gitattributes asks for LF (doctor D07). Settle that once:\n"
              f"  git add --renormalize -- . \":(exclude){cfg['raw']}\" && python3 tools/kac.py commit schema "
              "\"normalize line endings\" --lenient")
    return 0


def run_commits(root, run_id):
    rc, out, _ = git(root, "log", "--format=%H%x1f%s%x1f%(trailers:key=Run-Id,valueonly,separator=%x2C)")
    hits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3 and run_id in [x.strip() for x in parts[2].split(",")]:
            hits.append((parts[0], parts[1]))
    return hits          # newest first


def matches_any(path, patterns):
    """True if path equals a pattern, matches it as a glob, or lies under it when it names a folder."""
    for pat in patterns:
        base = pat.rstrip("/*")
        if path == pat or fnmatch.fnmatch(path, pat) or (base and path.startswith(base + "/")):
            return True
    return False


def records_revert(root, sha, path, cur_bytes):
    """Undo one commit's changes to a JSONL store record by record (matched on `id`), not line by line.
    Records are compared as data, so a run that only re-serialized a record did not "change" it; a restored
    record gets its earlier line back byte for byte. Returns (new_bytes, ids_changed_again_later), or None
    when the store cannot be handled this way."""
    before, after = git_show(root, f"{sha}^", path), git_show(root, sha, path)
    if after is None or cur_bytes is None:
        return None

    def by_id(blob):
        rows = {}
        for line in blob.decode("utf-8", errors="surrogateescape").split("\n"):      # odd bytes survive unchanged
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                rid = obj.get("id")
            except (ValueError, AttributeError):
                return None
            if rid is None or str(rid) in rows:
                return None
            rows[str(rid)] = (canon(obj), line.rstrip("\r"))      # canonical JSON: 1 and true are different
        return rows

    b, a, c = by_id(before or b""), by_id(after), by_id(cur_bytes)
    if b is None or a is None or c is None:
        return None
    data = lambda rows, rid: rows[rid][0] if rid in rows else None      # noqa: E731
    out, again = {rid: line for rid, (_, line) in c.items()}, []
    for rid in sorted(set(a) | set(b)):
        if data(a, rid) == data(b, rid):
            continue                              # the run did not change this record's content
        if data(c, rid) != data(a, rid):
            again.append(rid)                     # a later run changed it again: do not guess
        elif rid in b:
            out[rid] = b[rid][1]                  # restore the earlier version, byte for byte
        else:
            out.pop(rid, None)                    # the run created it
    order = sorted(out) if list(c) == sorted(c) else list(out)
    return "".join(out[r] + "\n" for r in order).encode("utf-8", errors="surrogateescape"), again


def file_revert(root, sha, path, cur):
    """Undo one commit's change to one file. cur = current bytes or None.
    Returns (new bytes or None for "no file", reason) where reason is None unless the undo must not guess."""
    before, after = git_show(root, f"{sha}^", path), git_show(root, sha, path)
    if cur is not None and after is not None and b"\r\n" in cur and b"\r\n" not in after and b"\0" not in cur[:8000]:
        cur = cur.replace(b"\r\n", b"\n")         # a CRLF working copy of a file that git stores with LF
    if cur == after:
        return before, None                       # untouched since the run: put back what was there before
    if before is None:                            # the run created the file, and it is no longer what the run left
        return cur, ("created by this run, then moved or removed by a later one" if cur is None
                     else "created by this run and changed again by a later one")
    if cur == before:
        return cur, None                          # already as it was before the run
    if cur is None or after is None:
        return cur, "removed on one side and changed on the other"
    if b"\0" in cur[:8000] or b"\0" in after[:8000]:
        return cur, "binary file changed again by a later run"
    d = tempfile.mkdtemp(prefix="kac-merge-")      # three-way merge that takes the run's change out again
    try:
        names = []
        for n, blob in (("current", cur), ("run", after), ("before", before)):
            names.append(os.path.join(d, n))
            with open(names[-1], "wb") as f:
                f.write(blob)
        r = subprocess.run(["git", "merge-file", "-p", *names], capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)      # where deleting is not permitted the temp files simply stay
    return (r.stdout, None) if r.returncode == 0 else (cur, "changed again in the same place by a later run")


def cmd_undo(root, cfg, a):
    """Undo one run as a new commit. It plans first and writes only when every file can be undone cleanly.
    It uses no `git revert`, `checkout` or `stash`, so it also works where deleting files is not permitted."""
    if not is_repo(root):
        die("not a git repository")
    require_branch(root)
    extra = clean_trailers(a.trailer or [])
    if read_lock(root):
        take_lock(root, cfg, "undo", a.run_id)        # reports the holder and exits with code 3
    if changed_paths(root, cfg):
        die("the working tree has uncommitted changes; commit or `kac abort` them first")
    commits = run_commits(root, a.run_id)
    if not commits:
        die(f"no commit carries Run-Id {a.run_id} (see `kac runs`)")
    subject = commits[-1][1]
    info = take_lock(root, cfg, "undo", subject)
    label = f"undo-failed-{info['run']}"
    keep = [x for x in (cfg["log"], ".kac/manifest.jsonl", None if a.with_raw else cfg["raw"]) if x]
    def current(path):                            # never read through a link that replaced a folder of the wiki
        linked = any((root / up).is_symlink() for up in Path(path).parents if str(up) != ".")
        return (root / path).read_bytes() if not linked and (root / path).is_file() else None
    wrote = False
    try:
        plan, modes, want_mode, conflicts = {}, {}, {}, []       # path -> new bytes, or None for "no file"
        filemode = git(root, "config", "--get", "core.filemode")[1].strip().lower() != "false"
        for sha, _ in commits:                    # newest first
            parents = len(git(root, "rev-list", "--parents", "-n1", sha)[1].split()) - 1
            if parents != 1:
                conflicts.append(f"{sha[:7]} is " + ("the first commit of the repository; it cannot be undone" if parents == 0
                                                     else "a merge commit (undo it by hand with `git revert -m 1`)"))
                continue
            names = git(root, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "--relative", "-r", "-z", sha)[1]
            for path in [x for x in names.split("\0") if x]:
                if matches_any(path, keep) or matches_any(path, cfg["derived"]):
                    continue                      # never undone / regenerated below
                cur = plan[path] if path in plan else current(path)
                res = records_revert(root, sha, path, cur) \
                    if any(path == pat or fnmatch.fnmatch(path, pat) for pat in cfg["records"]) else None
                if res is not None:
                    if res[1]:
                        conflicts.append(f"{path}: records changed again later: " + ", ".join(res[1][:8]))
                    else:
                        plan[path] = res[0]
                    continue
                new, why = file_revert(root, sha, path, cur)
                if why:
                    conflicts.append(f"{path}: {why}")
                else:
                    plan[path] = new
                    mb, ma = tracked_mode(root, f"{sha}^", path), tracked_mode(root, sha, path)
                    modes[path] = mb
                    if filemode and mb != ma and {mb, ma} == {"100644", "100755"} and tracked_mode(root, "HEAD", path) == ma:
                        want_mode[path] = mb      # the run changed the executable bit, and nobody changed it since
        if conflicts:
            drop_lock(root)
            print("kac: cannot undo automatically:\n  " + "\n  ".join(conflicts) + "\nNothing was changed. Make a "
                  "correcting edit instead (supersede or retract), or undo the later runs first.")
            return 1
        todo = {path: data for path, data in plan.items() if data != current(path) or path in want_mode}
        if not todo:
            drop_lock(root)
            print(f"kac: nothing to undo: every file run {a.run_id} changed is already as it was before that run "
                  "(or the run changed only the log, evidence or derived files).")
            return 1
        wrote, gone = True, []
        for path, data in todo.items():
            if data is None:
                remove_file(root, path, f"undo-{info['run']}")
                gone.append(path)
            else:
                no_link_above(root, path)
                if data != current(path):
                    atomic_write(root / path, data)
                try:
                    bits = stat.S_IMODE(os.stat(root / path).st_mode)
                    if (want_mode.get(path) or modes.get(path)) == "100755":
                        os.chmod(root / path, bits | 0o111)
                    elif want_mode.get(path) == "100644":
                        os.chmod(root / path, bits & ~0o111)
                except OSError:
                    pass
        raw_gone = [x for x in gone if x.startswith(cfg["raw"] + "/")]
        if raw_gone and manifest_path(root).exists():   # withdrawn evidence gets a tombstone line in the manifest
            append_text(manifest_path(root), "".join(canon({"path": x, "removed": True, "run": info["run"],
                        "reason": a.reason or f"undo of {a.run_id}"}) + "\n" for x in raw_gone))
        cfg = load_cfg(root)                      # the undo may have brought back an earlier kac.yaml
        for c in cfg["rebuild"]:
            rc, out = sh(c, root, shell=True)
            if rc != 0:
                print(f"kac: rebuild step failed: {c}\n{out[-400:]}")
        refresh_index(root, cfg)
        if cfg["log"]:
            append_text(root / cfg["log"], log_entry(cfg, "undo", subject,
                        [f"reverts: {a.run_id} ({', '.join(s[:7] for s, _ in commits)})"]
                        + ([f"reason: {' '.join(a.reason.split())}"] if a.reason else []) + [f"run: {info['run']}"]))
        if cmd_check(root, cfg, quiet=True, allow_raw=a.with_raw, lenient=True, removing=gone) != 0:   # restored content was in history
            n = set_aside(root, cfg, label)
            drop_lock(root)
            print(f"kac: the wiki fails its checks after the undo, so nothing was committed. The {n} file(s) the undo "
                  f"produced are in _to_delete/{label}/ and the wiki is back at its last commit.")
            return 1
        rc, out = git_commit(root, cfg, [f"undo: {subject}", "", f"Run-Id: {info['run']}", "Op: undo",
                                         f"Reverts-Run: {a.run_id}",
                                         f"Agent: {os.environ.get('KAC_AGENT') or cfg['agent']}"] + extra)
        if rc != 0:
            n = set_aside(root, cfg, label)
            drop_lock(root)
            print(f"kac: git commit failed, so the undo was taken back ({n} file(s) in _to_delete/{label}/):\n{out}")
            return 1
        drop_lock(root)
        print(json.dumps({"undone": a.run_id, "run": info["run"], "files": len(todo),
                          "commit": git(root, "rev-parse", "--short", "HEAD")[1].strip()}))
        return 0
    except BaseException as e:                    # a full disk, a file in use, Ctrl-C: leave the last commit in place
        if wrote:
            try:
                n = set_aside(root, cfg, label)
                print(f"kac: the undo stopped ({type(e).__name__}: {e}); {n} file(s) it had written are in "
                      f"_to_delete/{label}/ and the wiki is back at its last commit.", file=sys.stderr)
            except Exception:                     # noqa: BLE001  (report the first problem, not the cleanup's)
                pass
        drop_lock(root)
        if isinstance(e, OSError):
            if not wrote:
                print(f"kac: the undo stopped before it changed anything ({type(e).__name__}: {e}).", file=sys.stderr)
            return 1
        raise


def here(root):
    """Limit a `git log` to this folder when the wiki is a subfolder of a larger repository."""
    return ["--", "."] if prefix(root) else []


def open_proposals(root):
    """Proposals that still wait for a decision: proposals/*.md whose `status` is absent or open."""
    d = root / "proposals"
    out = []
    for p in (sorted(d.glob("*.md")) if d.is_dir() else []):
        meta, _, err = load_page(p)
        if err or str(meta.get("status") or "open").lower() in ("open", "proposed", "new", "pending"):
            out.append(p)
    return out


def cmd_status(root, cfg, a):
    """Everything a session needs before it starts work, in one small answer (instead of reading index and log)."""
    docs = build_search(root, cfg)
    first = f"wiki: {cfg['name']} | {len(docs)} pages | spec {cfg['spec'] or 'unset'}"
    if not is_repo(root):                         # nothing is known about commits, changes or runs: say so
        own = str(cfg["history"]).lower() == "external"
        print(first + " | history: " + ("kept by the wiki's own tool, not read by the kit" if own else "none (not a git repository)"))
    else:
        head = git(root, "log", "-1", "--date=format:%Y-%m-%d %H:%M", "--format=%h %ad")[1].strip() or "no commits"
        dirty, lk = changed_paths(root, cfg), read_lock(root)
        print(first + f" | head {head} | tree {'clean' if not dirty else str(len(dirty)) + ' uncommitted change(s)'} | "
              f"lock {'free' if not lk else 'HELD by ' + str(lk.get('run')) + ' (' + str(lk.get('title')) + ')'}")
        print("last runs:")
        fmt = "%ad%x1f%(trailers:key=Run-Id,valueonly,separator=%x2C)%x1f%s"
        for line in git(root, "log", f"-n{a.n}", "--date=format:%Y-%m-%d %H:%M", f"--format={fmt}", *here(root))[1].splitlines():
            d, run, subj = (line.split("\x1f") + ["", ""])[:3]
            print(f"  {d}  {(run.strip() or '-'):<26} {subj[:80]}")
    by = defaultdict(Counter)
    for d in docs.values():
        by[d["type"]][d["state"]] += 1
    parts = []
    for t, c in sorted(by.items(), key=lambda x: -sum(x[1].values())):
        states = ", ".join(f"{k} {n}" for k, n in c.most_common(3) if k) if len(c) > 1 else "".join(c)
        parts.append(f"{t} {sum(c.values())}" + (f" ({states})" if states else ""))
    print("pages: " + "; ".join(parts))
    now_ = dt.datetime.now(dt.timezone.utc)
    stale = sum(1 for d in docs.values() if (w := stale_date(stale_value(d["meta"], cfg))) and w != "bad" and now_ >= w)
    props = len(open_proposals(root))
    trash = (root / "_to_delete").is_dir() and any((root / "_to_delete").iterdir())
    detached = is_repo(root) and git(root, "symbolic-ref", "-q", "HEAD")[0] != 0
    note = [f"{stale} page(s) past their review date" if stale else "", f"{props} proposal(s) waiting for review" if props else "",
            "_to_delete/ is not empty (a person can delete it)" if trash else "",
            "HEAD is detached: switch to a branch before writing" if detached else ""]
    if any(note):
        print("attention: " + "; ".join(x for x in note if x))
    hubs = sorted((r, d) for r, d in docs.items() if d["type"] == "hub")[:5]
    for r, d in hubs:
        print(f"start here: {r} — {d['card']}")
    print('next: kac search "words" | kac list --type T --where key=value | '
          + ("kac history PAGE | " if is_repo(root) else "") + "then read ONE page")
    return 0


def cmd_runs(root, cfg, a):
    if not is_repo(root):
        die("not a git repository: `runs`, `history` and `show` read git history")
    fmt = "%h%x1f%ad%x1f%s%x1f%(trailers:key=Run-Id,valueonly,separator=%x2C)%x1f%(trailers:key=Reverts-Run,valueonly,separator=%x2C)"
    rc, out, _ = git(root, "log", f"-n{a.n}", "--date=format:%Y-%m-%d %H:%M", f"--format={fmt}", *here(root))
    for line in out.splitlines():
        h, d, s, run, rev = (line.split("\x1f") + [""] * 5)[:5]
        print(f"{d}  {h}  {(run.strip() or '-'):<26} {s[:70]}" + (f"  (reverts {rev.strip()})" if rev.strip() else ""))
    return 0


def resolve_page(root, cfg, page):
    """Path of a page given its id or its path; also finds pages that were deleted later."""
    if (root / page).is_file():
        return rel(root, root / page)
    pages = list(iter_pages(root, cfg))
    hits = [q for q in pages if q.stem == page] or [q for q in pages if page.endswith(q.stem)]
    if len(hits) == 1:
        return rel(root, hits[0])
    gone = [x for x in git(root, "log", "--format=", "--name-only", "--diff-filter=D", "--relative", "--",
                           f"*/{page}.md")[1].splitlines() if x]
    return gone[0] if gone else None


def resolve_ref(root, ref):
    """A commit for a run id, a tag or commit, or a date (the state at the end of that day)."""
    if ref.startswith("r-"):
        hits = run_commits(root, ref)
        return hits[0][0] if hits else None
    rc, out, _ = git(root, "rev-parse", "--verify", "-q", ref + "^{commit}")
    if rc == 0:
        return out.strip()
    when = ref + " 23:59:59" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ref) else ref
    return git(root, "rev-list", "-1", f"--before={when}", "HEAD")[1].strip() or None


def cmd_history(root, cfg, a):
    if not is_repo(root):
        die("not a git repository: `runs`, `history` and `show` read git history")
    path = resolve_page(root, cfg, a.page)
    if not path:
        die(f"no page {a.page!r} (now or in history)")
    fmt = "%ad%x1f%(trailers:key=Run-Id,valueonly,separator=%x2C)%x1f%s"
    out = git(root, "log", "--follow", f"-n{a.n}", "--date=format:%Y-%m-%d %H:%M", f"--format={fmt}", "--", path)[1]
    print(path)
    for line in out.splitlines():
        d, run, subj = (line.split("\x1f") + ["", ""])[:3]
        print(f"  {d}  {(run.strip() or '-'):<26} {subj[:80]}")
    return 0


def cmd_show(root, cfg, a):
    if not is_repo(root):
        die("not a git repository: `runs`, `history` and `show` read git history")
    path, sha = resolve_page(root, cfg, a.page), resolve_ref(root, a.at)
    if not path or not sha:
        die(f"cannot resolve page {a.page!r} at {a.at!r}")
    blob = git_show(root, sha, path)
    if blob is None:
        die(f"{path} did not exist at {a.at} ({sha[:7]})", 1)
    when = git(root, "log", "-1", "--date=format:%Y-%m-%d %H:%M", "--format=%ad", sha)[1].strip()
    print(f"[{path} as of {when}, commit {sha[:7]}]")
    sys.stdout.write(blob.decode("utf-8", errors="replace"))
    return 0


# ----------------------------------------------------------------------------- index, search, eval
def collect_cards(root, cfg):
    cards = []
    for p in iter_pages(root, cfg):
        meta, body, err = load_page(p)
        if err or not meta.get("type"):
            continue
        cards.append({"id": page_id(p, meta), "path": rel(root, p), "type": str(meta["type"]),
                      "title": str(meta.get("title") or p.stem), "card": card_of(meta, cfg, 160, body),
                      "state": state_of(meta), "bytes": p.stat().st_size,
                      "sens": sensitivity_of(meta, cfg)})
    return cards


def _index_line(c, cfg):
    name = f"[[{c['id']}]]" if cfg["index"]["link"] == "wikilink" else \
        (f"[{c['id']}]({c['path']})" if cfg["index"]["link"] == "path" else c["id"])
    return f"- {name} — {c['card']}" + (f" · **{c['state']}**" if c["state"] else "")


def _where(items):
    dirs = sorted({str(Path(c["path"]).parent.as_posix()) for c in items})
    return f"`{dirs[0]}/<id>.md`" if len(dirs) == 1 else "several folders (find a page with `kac search <id>`)"


def render_index(root, cfg):
    """One flat catalog while it fits the budget; otherwise a small map plus one card list per type.
    Never a second level of summaries: the only descriptions are the pages' own cards."""
    cards = [c for c in collect_cards(root, cfg) if c["type"] not in cfg["index"]["skip_types"]]
    by = defaultdict(list)
    for c in cards:
        by[" ".join(c["type"].split())[:60] or "untyped"].append(c)
    for t in by:
        by[t].sort(key=lambda c: c["id"])
    head = ["# Index", f"{INDEX_MARK} from front matter: {len(cards)} pages. Do not edit._", "",
            "Each line: page id, its one-sentence card, its state. To find something, search first "
            "(`kac search \"words\"`); for exact lists and counts use `kac list`.", ""]
    flat = list(head)
    for t in sorted(by):
        flat += [f"## {t} ({len(by[t])}) in {_where(by[t])}"] + [_index_line(c, cfg) for c in by[t]] + [""]
    text = "\n".join(flat)
    if len(text.encode()) <= cfg["budgets"]["index_kb"] * 1024:
        return {"index.md": text}
    d, per = cfg["index"]["dir"].strip("/"), int(cfg["budgets"]["partition_entries"])
    files, rows, used = {}, [], set()
    room = cfg["budgets"]["index_kb"] * 1024 - 200
    for t in sorted(by):
        slug = re.sub(r"[^\w.-]+", "-", t).strip(".-") or "untyped"      # a type is data: never a path,
        if f"{slug}.md".lower() in {n.lower() for n in SKIP_NAMES + INSTRUCTION_NAMES}:      # and never a file that
            slug = "type-" + slug                                         # agents load as instructions (AGENTS.md)
        while slug.lower() in used:
            slug += "-x"
        used.add(slug.lower())
        chunks, size = [[]], 0
        for c in by[t]:                           # each card list stays under the byte and entry budgets
            n = len(_index_line(c, cfg).encode()) + 1
            if chunks[-1] and (size + n > room or len(chunks[-1]) >= per):
                chunks.append([])
                size = 0
            chunks[-1].append(c)
            size += n
        for n, chunk in enumerate(chunks, 1):
            name = f"{d}/{slug}.md" if len(chunks) == 1 else f"{d}/{slug}-{n:02d}.md"
            span = "" if len(chunks) == 1 else f"{chunk[0]['id']} … {chunk[-1]['id']}"
            lines = [f"# {t} ({len(chunk)} of {len(by[t])}) {span}".rstrip(),
                     f"{INDEX_MARK}. Do not edit. Pages live in {_where(chunk)}._", ""]
            lines += [_index_line(c, cfg) for c in chunk]
            files[name] = "\n".join(lines) + "\n"
            rows.append(f"| {t} | {len(chunk)} | {name} | {len(files[name].encode()) // 1024 + 1} KB | {span} |")
    top = head + ["| Type | Pages | Card list | Size | Ids |", "|---|---|---|---|---|", *rows, ""]
    hubs = by.get("hub", [])
    if hubs:
        top += ["## Start here"] + [_index_line(c, cfg) for c in hubs] + [""]
    files["index.md"] = "\n".join(top)
    return files


def cmd_index(root, cfg, a):
    files = render_index(root, cfg)
    stale = [f for f, text in files.items()
             if not (root / f).exists() or (root / f).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n") != text]
    foreign = [f for f in files if (root / f).exists() and not kit_generated(root / f)]
    d = root / cfg["index"]["dir"]
    extra = [rel(root, p) for p in sorted(d.glob("*.md"))                 # lists the kit wrote that are no longer needed
             if rel(root, p) not in files and kit_generated(p) and index_head(p) != INDEX_STUB] if d.is_dir() else []
    if a.check:
        print("index is up to date" if not (stale or extra) else f"index is stale: {stale + extra}")
        return 1 if (stale or extra) else 0
    if foreign and not getattr(a, "force", False):
        die(f"{', '.join(foreign[:3])} was not written by `kac index`. To replace it, run `kac index --force` "
            "(the present file stays in git history if it is committed).", 1)
    for f in stale:
        atomic_write(root / f, files[f])
    for f in extra:                               # only lists that the kit itself generated earlier
        try:
            (root / f).unlink()
        except OSError:                           # deleting is not permitted here: leave a one-line stub instead
            atomic_write(root / f, INDEX_STUB)
    b = cfg["budgets"]
    big = [f for f, t in files.items() if f == "index.md" and len(t.encode()) > b["index_kb"] * 1024]
    print(f"index: {len(files)} file(s), {len(stale)} rewritten, {len(extra)} removed"
          + (f"; the map itself is over {b['index_kb']} KB: merge or retire page types" if big else ""))
    return 0


def tokens(text):
    out = []
    words = []
    for t in TOKEN.findall(str(text).lower()):
        words += [x for x in CJK_RUN.findall(t)] if CJK.search(t) else [t]      # "windows11の設定" -> windows11, の設定
    for t in words:
        if CJK.search(t):                    # no spaces between words: index overlapping pairs of characters
            out += [t[i:i + 2] for i in range(len(t) - 1)] or [t]
            continue
        if t in STOP or len(t) < 2:
            continue
        if len(t) > 3 and t.endswith("ies"):
            t = t[:-3] + "y"
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]                       # light plural folding; no other stemming
        out.append(t)
    return out


def cache_dir(root):
    key = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    try:
        base = Path(os.environ.get("KAC_CACHE") or (Path.home() / ".cache" / "kac"))
    except RuntimeError:                     # an account without a home folder
        base = Path(tempfile.gettempdir()) / "kac-cache"
    return base / f"{root.name}-{key}"       # outside the wiki: caches never live in a synced folder


def _plain(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


def build_search(root, cfg):
    """Incremental BM25 corpus + front matter. Only files whose size or mtime changed are read again."""
    cfile = cache_dir(root) / f"search-v{VERSION}-6.json"
    try:
        cache = json.loads(cfile.read_text(encoding="utf-8")) if cfile.exists() else {}
    except (ValueError, OSError):
        cache = {}
    docs, dirty = {}, False
    for p in iter_pages(root, cfg):
        r = rel(root, p)
        st = p.stat()
        sig = [st.st_size, st.st_mtime_ns]
        old = cache.get(r)
        if old and old["sig"] == sig:
            docs[r] = old
            continue
        dirty = True
        meta, body, err = load_page(p)
        if err:
            continue
        tf = Counter()
        head = " ".join([p.stem.replace("-", " "), str(meta.get("id", "")), str(meta.get("title", "")),
                         " ".join(map(str, as_list(meta.get("aliases"))))])
        mid = " ".join([str(meta.get(f, "")) for f in cfg["card_fields"]] + list(map(str, as_list(meta.get("tags")))))
        for w in tokens(head):
            tf[w] += 3
        for w in tokens(mid):
            tf[w] += 2
        for w in tokens(body):                    # generated zones too: in a rendered wiki they are the page
            tf[w] += 1
        docs[r] = {"sig": sig, "tf": dict(tf), "len": sum(tf.values()), "id": page_id(p, meta),
                   "type": str(meta.get("type")), "card": card_of(meta, cfg, 160, body), "state": state_of(meta),
                   "bytes": st.st_size, "sens": sensitivity_of(meta, cfg), "meta": _plain(meta)}
    if dirty or set(docs) != set(cache):
        try:
            atomic_write(cfile, json.dumps(docs, ensure_ascii=False))
        except OSError:
            pass                              # a read-only home folder only costs speed
    for d in docs.values():                   # labels follow today's configuration, not the day the page was cached
        d["sens"] = sensitivity_of(d["meta"], cfg)
    return docs


def bm25(docs, query, k=8, type_filter=None, state=None, deny=()):
    pool = {r: d for r, d in docs.items()
            if d["type"] != "redirect" and (not type_filter or d["type"] == type_filter)
            and (not state or d["state"] == state) and not (set(d.get("sens", [])) & set(deny))}
    if not pool:
        return []
    n = len(pool)
    avg = sum(d["len"] for d in pool.values()) / n or 1
    q = tokens(query)
    df = Counter(w for d in pool.values() for w in set(q) if w in d["tf"])
    out = []
    for r, d in pool.items():
        s = 0.0
        for w in q:
            f = d["tf"].get(w, 0)
            if f:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * f * 2.4 / (f + 1.4 * (0.25 + 0.75 * d["len"] / avg))
        if s > 0:
            out.append((s, r, d))
    out.sort(key=lambda x: (-x[0], x[1]))
    floor = out[0][0] * 0.2 if out else 0     # drop the long tail of weak matches: fewer wasted tokens
    return [x for x in out if x[0] >= floor][:k]


def cmd_search(root, cfg, a):
    hits = bm25(build_search(root, cfg), a.query, a.k, a.type, a.state)
    if a.json:
        print(json.dumps([{"score": round(s, 2), "id": d["id"], "path": r, "type": d["type"], "state": d["state"],
                           "card": d["card"], "tokens": est_tokens(d["bytes"])} for s, r, d in hits], ensure_ascii=False))
    else:
        for s, r, d in hits:
            st = f" **{d['state']}**" if d["state"] else ""
            print(f"{s:6.2f}  {r}  [{d['type']}]{st}  ~{est_tokens(d['bytes'])} tok\n        {d['card']}")
        if not hits:
            print("no matches (try other words, or `rg -i` for exact strings)")
    return 0


def cmd_list(root, cfg, a):
    """Exact answers from front matter: filter, project, count. No page bodies are read (cache)."""
    docs = build_search(root, cfg)
    rows = []
    for r, d in sorted(docs.items()):
        m = dict(d["meta"], path=r, id=d["id"], state=d["state"])
        if a.type and str(m.get("type")) != a.type:
            continue
        ok = True
        for cond in a.where or []:
            k, _, v = cond.partition("=")
            have = m.get(k)
            if isinstance(have, list):
                ok = ok and v.lower() in [str(x).lower() for x in have]
            else:
                ok = ok and ("" if have is None else str(have).lower()) == v.lower()
        if ok:
            rows.append(m)
    if a.count_by:
        c = Counter(", ".join(map(str, m.get(a.count_by))) if isinstance(m.get(a.count_by), list)
                    else str(m.get(a.count_by)) for m in rows)
        out = dict(sorted(c.items(), key=lambda x: (-x[1], x[0])))
        print(json.dumps(out, ensure_ascii=False) if a.json else
              "\n".join(f"{n:5d}  {k}" for k, n in out.items()) + f"\n{len(rows):5d}  total")
        return 0
    fields = [f for f in (a.fields or "id,type,state").split(",") if f]
    if a.json:
        print(json.dumps([{f: m.get(f) for f in fields} for m in rows[:a.limit]], ensure_ascii=False))
    else:
        for m in rows[:a.limit]:
            print(" | ".join(str(m.get(f, "")) for f in fields))
        print(f"-- {len(rows)} page(s)" + (f", first {a.limit} shown" if len(rows) > a.limit else ""))
    return 0


def cmd_eval(root, cfg, a):
    """Page-retrieval check: is every page a question needs among the top results? Entries of other kinds
    (checks on claims, on messages, on answers) are counted as skipped and left to the wiki's own eval."""
    f = root / "evals" / "golden.yaml"
    if not f.exists():                       # a new wiki has no questions yet: nothing to fail, but say so
        print("golden retrieval: no evals/golden.yaml yet; nothing checked (add questions that name the pages they need)")
        return 0
    try:
        gold = yaml.safe_load(f.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        die(f"evals/golden.yaml is not valid YAML: {str(e).splitlines()[0]}", 1)
    if not isinstance(gold, list):
        die("evals/golden.yaml must be a list of questions", 1)
    docs = build_search(root, cfg)           # built once for all questions
    done = hits = skipped = 0
    for g in gold:
        if not isinstance(g, dict):
            continue
        q = g.get("question") or g.get("q") or g.get("query")
        need = g.get("must_retrieve") or (g.get("ref") if g.get("kind") in (None, "page", "retrieval") else None) or []
        need = [str(x) for x in as_list(need)]
        if not q or not need:
            skipped += 1
            continue
        done += 1
        top = bm25(docs, q, a.k)
        found = {d["id"] for _, _, d in top} | {r for _, r, _ in top} | {Path(r).stem for _, r, _ in top}
        ok = all(any(x in (n, n + ".md") or x.endswith("/" + n) or x.endswith("/" + n + ".md") for x in found) for n in need)
        hits += ok
        if not ok:
            print(f"MISS {g.get('id', '')} {q}\n     need {need}; got {[d['id'] for _, _, d in top]}")
    other = f"; {skipped} entries of other kinds are left to the wiki's own eval" if skipped else ""
    if not done:
        print(f"golden retrieval: no page-retrieval questions in evals/golden.yaml{other}")
        return 0
    rate = hits / done
    print(f"golden retrieval: {hits}/{done} = {rate:.0%} (top {a.k}){other}")
    return 0 if rate >= cfg["eval_threshold"] else 1


# ----------------------------------------------------------------------------- pack, snapshot
def cmd_pack(root, cfg, a):
    pk = cfg["pack"]
    budget = cfg["budgets"]["pack_kb"] * 1024
    allow, deny = pk["allow_sensitivity"], pk["deny_sensitivity"]

    def scrub(text):                              # applied to cards and page bodies; never to ids or the header
        for rule in pk["scrub"]:
            text = re.sub(rule["pattern"], rule.get("replace", "[removed]"), text)
        return text

    head = git(root, "log", "-1", "--format=%h %cd", "--date=format:%Y-%m-%d %H:%M")[1].strip() if is_repo(root) else "no git"
    every = collect_cards(root, cfg)
    cards = [c for c in every if may_leave(c["sens"], cfg, allow, deny)]
    hidden = len(every) - len(cards)
    unlisted = Counter(c["type"] for c in cards if c["type"] in pk["hide_types"])
    notes = f"{hidden} page(s) are left out because of their sensitivity label."
    if unlisted:
        notes += " Not listed here: " + ", ".join(f"{n} {t} page(s)" for t, n in sorted(unlisted.items())) + "."
    out = [f"# {cfg['name']}: context pack", "",
           f"Snapshot of the wiki at commit {head}. It is a copy: for anything newer, or for full pages, use the wiki itself.",
           "Each line is `id — card · state`. Facts marked candidate or disputed are not settled. " + notes, ""]
    used = sum(len(x.encode()) + 1 for x in out)
    room = budget - 160                           # the closing note must fit as well
    for inc in glob_many(root, pk["include"]):
        r_ = rel(root, inc)
        if "/." in "/" + r_ or any(fnmatch.fnmatch(r_, x) for x in cfg["exclude"]):
            continue                              # excluded folders are never packed, whatever the include says
        meta, body, err = load_page(inc)
        if err or not may_leave(sensitivity_of(meta, cfg), cfg, allow, deny):
            continue
        block = f"## {scrub(str(meta.get('title') or inc.stem))}\n\n{scrub(GEN.sub('', body).strip())}\n"
        if used + len(block.encode()) + 1 > budget * 0.6:
            continue
        out.append(block)
        used += len(block.encode()) + 1
    by = defaultdict(list)
    for c in cards:
        if c["type"] not in pk["hide_types"]:
            by[c["type"]].append(c)
    dropped = 0
    for t in sorted(by, key=lambda t: (len(by[t]), t)):
        lines = [f"## {t} ({len(by[t])})"]
        cost = len(lines[0].encode()) + 2         # the heading and the blank line after the list
        if used + cost > room:
            dropped += len(by[t])
            continue
        used += cost
        for c in sorted(by[t], key=lambda c: c["id"]):
            line = f"- {c['id']} — {scrub(c['card'])}" + (f" · {c['state']}" if c["state"] else "")
            if used + len(line.encode()) + 1 > room:
                dropped += 1
                continue
            lines.append(line)
            used += len(line.encode()) + 1
        out += lines + [""]
    if dropped:
        out.append(f"_{dropped} cards did not fit the {cfg['budgets']['pack_kb']} KB budget; search the wiki for them._")
    text = "\n".join(out) + "\n"
    for pat in pk["forbid"]:                      # the last gate: a failed export is better than a leaked one
        m = re.search(pat, text)
        if m:
            print(f"ERROR pack: forbidden pattern {pat!r} still matches near: ...{text[max(0, m.start() - 30):m.end() + 10]!r}")
            return 1
    body_sha = hashlib.sha256("\n".join(text.split("\n")[3:]).encode()).hexdigest()   # without the snapshot line
    side = root / (pk["out"].rsplit(".", 1)[0] + ".json")
    try:
        old = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if old.get("body_sha256") == body_sha and (root / pk["out"]).exists():
        print(json.dumps(dict(old, unchanged=True)))        # same content: keep the file, keep caches warm
        return 0
    atomic_write(root / pk["out"], text)
    meta = {"file": pk["out"], "bytes": len(text.encode()), "tokens_est": est_tokens(len(text.encode())),
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "body_sha256": body_sha, "commit": head,
            "cards": len(cards) - sum(unlisted.values()) - dropped, "left_out_sensitive": hidden,
            "not_listed": dict(unlisted)}
    atomic_write(side, json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta))
    return 0


def backup_dir(root, cfg):
    where = os.environ.get("KAC_BACKUP") or cfg["backup"]["dir"]       # the env var wins (paths differ per machine)
    return Path(where) if os.path.isabs(where) else (root / where).resolve()


def bundle_name(cfg, root):
    """<name>-<first commit>: two wikis that share a name and a backup folder never prune each other's bundles."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(cfg["name"])).strip("-.") or "wiki"
    first = git(root, "rev-list", "--max-parents=0", "HEAD")[1].split()
    return f"{name}-{first[-1][:8]}" if first else name


def own_bundles(bdir, name):
    """This wiki's snapshot bundles, oldest first. `kb-x-…` is not a bundle of `kb`."""
    pat = re.compile(re.escape(name) + r"-\d{8}-\d{6}\.bundle")
    return sorted(f for f in bdir.glob(f"{name}-*.bundle") if pat.fullmatch(f.name)) if bdir.is_dir() else []


def cmd_snapshot(root, cfg, a):
    if not is_repo(root):
        die("not a git repository")
    if not has_head(root):
        die("this repository has no commit yet")
    if changed_paths(root, cfg):
        die("uncommitted changes: commit the run first, then snapshot")
    bdir, name = backup_dir(root, cfg), bundle_name(cfg, root)
    if bdir == root:
        die("the backup folder must not be the wiki folder itself (set kit.backup.dir or KAC_BACKUP)")
    stamp = now(cfg).strftime("%Y%m%d-%H%M%S")
    tag = a.name or f"snap-{stamp}"
    rc, out, err = git(root, "tag", "-a", tag, "-m", f"kac snapshot {stamp}", readonly=False)
    if rc != 0:
        die(f"git tag failed: {err.strip()}")
    bdir.mkdir(parents=True, exist_ok=True)
    tmp, final = bdir / f"{name}-{stamp}.bundle.tmp", bdir / f"{name}-{stamp}.bundle"
    rc, out, err = git(root, "bundle", "create", str(tmp), "--all", readonly=False)
    if rc == 0:
        rc, out, err = git(root, "bundle", "verify", str(tmp))
    if rc != 0:
        for cleanup in (lambda: tmp.unlink(), lambda: git(root, "tag", "-d", tag, readonly=False)):
            try:
                cleanup()
            except OSError:
                pass
        die(f"the bundle could not be created or did not verify: {err.strip()}")
    os.replace(tmp, final)
    keep, pruned = max(1, int(cfg["backup"]["keep"])), 0
    for f in own_bundles(bdir, name)[:-keep]:
        if f == final:
            continue
        try:
            f.unlink()
            pruned += 1
        except OSError:
            pass                                  # not allowed to delete here: prune by hand now and then
    print(json.dumps({"tag": tag, "bundle": str(final), "bytes": final.stat().st_size, "pruned": pruned,
                      "bundles_kept": len(own_bundles(bdir, name))}))
    return 0


# ----------------------------------------------------------------------------- verify, pin, adopt
def cmd_verify(root, cfg, a):
    errs, notes = [], []
    live, rows, bad = load_manifest(root)
    if bad:
        errs.append(f".kac/manifest.jsonl: invalid JSON on line(s) {bad[:5]}")
    if not manifest_path(root).exists():
        notes.append("no manifest yet (run `kac adopt --apply`)" if is_repo(root) else
                     "no manifest (the kit's manifest and pins need git; the fingerprint below works without them)")
    else:
        on_disk = {rel(root, p): p for p in raw_files(root, cfg)}
        eol = set(converted_checkout(root, cfg["raw"])) if is_repo(root) and has_head(root) else set()
        for r, p in on_disk.items():
            m = live.get(r)
            if not m:
                notes.append(f"unregistered raw file {r} " + ("(its line endings differ from git's copy: settle that with "
                             "`kac normalize`)" if r in eol else "(the next `kac commit` registers it)"))
            elif m.get("bytes") != p.stat().st_size or (a.deep and m.get("sha256") != sha256_file(p)):
                errs.append(f"raw file changed after it was registered: {r}")
        absent = 0
        for r, m in live.items():
            if r not in on_disk:
                if m.get("external"):             # evidence kept outside git: a clone does not have it
                    absent += 1
                else:
                    errs.append(f"raw file listed in the manifest is missing: {r}")
        notes.append(f"manifest: {len(live)} files, {'hashes' if a.deep else 'sizes'} checked"
                     + (f"; {absent} file(s) kept outside git are not in this folder" if absent else ""))
        if is_repo(root) and has_head(root):      # git must hold the registered bytes too, or a clone gets other evidence
            sizes = {}
            for item in git(root, "ls-tree", "-r", "-l", "-z", "HEAD", "--", cfg["raw"])[1].split("\0"):
                meta, _, path = item.partition("\t")
                if path and meta.split()[-1].isdigit():
                    sizes[path[len(prefix(root)):] if path.startswith(prefix(root)) else path] = int(meta.split()[-1])
            other = sorted(r for r, m in live.items() if r in sizes and m.get("bytes") != sizes[r])
            other = [r for r in other if r not in filtered_paths(root, other)]      # a filter (Git LFS) stores a pointer
            if other:
                errs.append(f"git stores {len(other)} evidence file(s) with other bytes than were registered (line endings "
                            f"converted when they were added?): {some(other, 3)}. See `kac normalize`.")
    for v in raw_violations(root, cfg):
        errs.append(f"git shows a changed raw file: {v}")
    for n in missing_but_tracked(root, cfg):
        errs.append(f"{n} is in git but missing from the folder; restore it (`git restore -- {n}`)")
    d = pin_diff(root, cfg)
    if d is None:
        notes.append("instruction files are not pinned" + (" (run `kac adopt --apply`)" if is_repo(root) else
                     "; keep the fingerprint below outside the wiki and compare it with `kac verify --expect`"))
    else:
        if d[0] or d[2]:
            errs.append("instruction files differ from their pins: " + some(d[0] + d[2]))
        if d[1]:
            errs.append("new instruction files that nobody pinned: " + some(d[1]))
    digest, local = fingerprint(root, cfg)
    if a.expect and a.expect != digest:
        errs.append(f"instruction fingerprint is {digest}, expected {a.expect}: rules, tools, skills, git hooks or "
                    "git settings changed since a person last approved them")
    notes.append(f"instruction fingerprint {digest}" + (f" (it also covers this copy's {', '.join(local)})" if local else ""))
    for pat in cfg["records"]:
        for f in glob_many(root, [pat]):
            _, badl = jl_read(f)
            if badl:
                errs.append(f"{rel(root, f)}: invalid JSON on line(s) {badl[:5]}")
    if a.deep and is_repo(root):
        rc, out, err = git(root, "fsck", "--no-dangling")
        if rc != 0:
            errs.append("git fsck reported problems: " + (out + err).strip()[:300])
    for n in notes:
        print("note ", n)
    for e in errs:
        print("ERROR", e)
    print(f"verify: {len(errs)} errors")
    return 1 if errs else 0


def cmd_pin(root, cfg, a):
    if not is_repo(root):
        die("pins are kept with git. Without git, keep the fingerprint that `kac verify` prints outside the wiki "
            "and compare it with `kac verify --expect`.")
    d = pin_diff(root, cfg)
    n = write_pins(root, cfg)
    if d:
        print(f"pinned {n} files; changed: {some(d[0]) or '-'}; new: {some(d[1]) or '-'}; removed: {some(d[2]) or '-'}")
    else:
        print(f"pinned {n} files")
    digest, local = fingerprint(root, cfg)
    print(f"instruction fingerprint {digest}  (for unattended runs: `kac verify --expect <fingerprint>`)"
          + (f"\nit also covers this copy's {', '.join(local)}" if local else ""))
    return 0


GITATTRIBUTES = """# Knowledge-as-Code: byte-stable checkouts on every operating system
*               -text
*.md            text eol=lf
*.jsonl         text eol=lf
*.json          text eol=lf
*.yaml          text eol=lf
*.yml           text eol=lf
*.toml          text eol=lf
*.txt           text eol=lf
*.py            text eol=lf
*.sh            text eol=lf
*.js            text eol=lf
.gitattributes  text eol=lf
.gitignore      text eol=lf
# append-only logs: keep both sides' new lines when two branches are merged
log.md          text eol=lf merge=union
.kac/manifest.jsonl text eol=lf merge=union
"""
RAW_RULE = "# immutable evidence: stored byte for byte, never auto-merged (must stay last)\n{pattern}          -text -merge\n"
GITIGNORE_LINES = ["*.tmp", ".backup/", "_to_delete/", "__pycache__/", "*.pyc", ".DS_Store", "Thumbs.db", "desktop.ini"]


def converted_checkout(root, under=""):
    """Tracked files that have CRLF line endings in the folder although git stores them with LF. Either a
    checkout converted the folder copy (Git for Windows, core.autocrlf=true), or the file arrived with CRLF and
    git converted its own copy when the file was added. Files whose attributes ask for CRLF are not listed."""
    out = []
    for item in git(root, "ls-files", "--eol", "-z", "--", under or ".")[1].split("\0"):
        meta, _, path = item.partition("\t")
        f = meta.split()
        if path and len(f) >= 2 and f[0] == "i/lf" and f[1] == "w/crlf" and "eol=crlf" not in meta:
            out.append(path)
    return out


def first_registered(root):
    """{path: manifest line} of the FIRST registration of each evidence file in the COMMITTED manifest (a
    tombstone starts over). Later lines, and lines that a run has written but not committed, cannot redefine
    what the evidence was."""
    first, rows = {}, []
    blob = git_show(root, "HEAD", ".kac/manifest.jsonl") if is_repo(root) and has_head(root) else None
    for line in (blob or b"").decode("utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    for r in rows:
        if not isinstance(r, dict) or "path" not in r:
            continue
        if r.get("erased") or r.get("removed"):
            first.pop(r["path"], None)
        else:
            first.setdefault(r["path"], r)
    return first


def evidence_eol(root, cfg):
    """Evidence files whose copy in the folder and copy in git differ in line endings only, sorted into
    {"git": [...], "folder": [...], "unknown": [...]} by which copy has the bytes that were registered."""
    out = {"git": [], "folder": [], "unknown": []}
    if not (is_repo(root) and has_head(root) and (root / cfg["raw"]).is_dir()):
        return out
    reg = first_registered(root)
    for path in converted_checkout(root, cfg["raw"]):
        f, blob = root / path, git_show(root, "HEAD", path)
        cur = f.read_bytes() if f.is_file() and not f.is_symlink() else None
        if blob is None or cur is None or cur.replace(b"\r\n", b"\n") != blob:
            continue                              # more than line endings differs: the ordinary raw check reports it
        want = (reg.get(path) or {}).get("sha256")
        kind = "folder" if want == hashlib.sha256(cur).hexdigest() else \
            "git" if want == hashlib.sha256(blob).hexdigest() else "unknown"
        out[kind].append(path)
    return out


def no_link_above(root, path):
    """Refuse to write through a symbolic link: a folder of the wiki that was replaced by a link must not lead
    a write to a place outside the wiki."""
    for up in Path(path).parents:
        if str(up) != "." and (root / up).is_symlink():
            raise OSError(f"{up.as_posix()} is a symbolic link; nothing is written through it")


def store_evidence(root, cfg, paths, apply):
    """Make git store these evidence files exactly as they are in the folder (git had converted its own copy
    when they were added). This is the one place where the kit changes a committed evidence blob, so it is a
    change unit of its own (Op: repair) that a person starts, and it touches nothing else."""
    paths = sorted(paths)
    if not paths:
        return 0
    if git(root, "diff", "--cached", "--quiet")[0] != 0:
        die("something is staged in git's index. Unstage it first (`git reset -q`): the repair commits only the "
            "evidence files.", 1)
    out = git(root, "check-attr", "-z", "text", "--", *paths)[1].split("\0")
    again = [out[j] for j in range(0, len(out) - 2, 3) if out[j + 2] != "unset"]
    if again:
        die(f"git would convert {again[0]} again at the next change: the evidence rule is missing in .gitattributes. "
            "Run `kac adopt --apply` (it adds the rule), then this command again.", 1)
    if not apply:
        print(f"evidence: git would be made to store {len(paths)} file(s) byte for byte, as one commit of its own: "
              f"{some(paths, 3)}. Re-run with --apply.")
        return 0
    require_branch(root)
    info = take_lock(root, cfg, "repair", "store evidence byte for byte")
    done, rc, out = [], 1, ""
    logf = cfg["log"] if cfg["log"] and not porcelain(root, cfg["log"]) else None      # only a log that has no other changes
    before = (root / logf).read_bytes() if logf and (root / logf).is_file() else None
    manf = ".kac/manifest.jsonl" if git_show(root, "HEAD", ".kac/manifest.jsonl") is not None \
        and not porcelain(root, ".kac/manifest.jsonl") else None                       # the same for the manifest
    man_before = (root / manf).read_bytes() if manf else None
    try:
        for path in paths:
            data = (root / path).read_bytes()
            mode = (git(root, "ls-files", "-s", "--", path)[1].split() or ["100644"])[0]
            oid = git(root, "hash-object", "-w", "--no-filters", "--", path, readonly=False)[1].strip()
            done.append(path)
            if not oid or git(root, "update-index", "--cacheinfo", f"{mode},{oid},{prefix(root)}{path}", readonly=False)[0] != 0 \
                    or git_show(root, "", path) != data:
                raise OSError(f"git did not take {path} as it is")
        subject = f"store {len(done)} evidence file(s) byte for byte"
        if manf:                                  # register what is not registered with these bytes yet
            live = load_manifest(root)[0]
            add = [{"path": x, "sha256": sha256_file(root / x), "bytes": (root / x).stat().st_size,
                    "added": now(cfg).isoformat(timespec="seconds"), "run": info["run"]} for x in done]
            add = [x for x in add if (live.get(x["path"]) or {}).get("sha256") != x["sha256"]]
            if add:
                append_text(root / manf, "".join(canon(x) + "\n" for x in add))
                git(root, "add", "--", manf, readonly=False)
        if logf and before is not None:
            append_text(root / logf, log_entry(cfg, "repair", subject, [f"changed: {some(done, 5)}", f"run: {info['run']}"]))
            git(root, "add", "--", logf, readonly=False)
        fd, name = tempfile.mkstemp(prefix="kac-msg-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join([f"repair: {subject}", "", f"Run-Id: {info['run']}", "Op: repair",
                               f"Agent: {os.environ.get('KAC_AGENT') or cfg['agent']}"]) + "\n")
        stamp = now(cfg).isoformat(timespec="seconds")
        rc, o, e = git(root, "commit", "-q", "-F", name, readonly=False,
                       env_add={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        out = (o + e).strip()
        try:
            os.unlink(name)
        except OSError:
            pass
    except OSError as e:
        out = str(e)
    finally:
        if rc != 0:                               # leave git exactly as it was: the folder copies were never touched
            git(root, "reset", "-q", "--", *(done + [x for x in (logf, manf) if x]), readonly=False)
            if logf and before is not None:
                atomic_write(root / logf, before)
            if manf and man_before is not None:
                atomic_write(root / manf, man_before)
        drop_lock(root)
    if rc != 0:
        print(f"kac: the repair was not committed and git is as it was: {out}")
        return 1
    refresh_stat(root, done)
    print(json.dumps({"run": info["run"], "op": "repair", "files": len(done),
                      "commit": git(root, "rev-parse", "--short", "HEAD")[1].strip()}))
    return 0


def cmd_normalize(root, cfg, a):
    """Settle files whose copy in the folder has CRLF line endings while git stores LF. A file is handled only
    when nothing but its line endings differs. Text files other than evidence get the line endings git stores;
    the copy as it was is kept in _to_delete/. Evidence is never touched without a decision, because one of the
    two copies was converted and only a person may know which:
      --raw git      the folder copies were converted (a converting checkout): rewrite them from git
      --raw folder   git converted its own copy when the files were added: make git store the folder's bytes
    Evidence that is registered in the committed manifest is only ever settled in the direction of the
    registered bytes."""
    if not is_repo(root) or not has_head(root):
        die("not a git repository with at least one commit")
    busy = git_busy(root)
    if busy:
        die(busy)
    choice = getattr(a, "raw", None)
    held = read_lock(root)
    if held and a.apply and choice == "folder":   # that mode makes a commit of its own
        die(f"run {held.get('run')} is open, and `--raw folder` makes a commit of its own. Finish the run with "
            "`kac commit` or take it back with `kac abort` first (abort leaves the evidence as it is).", 3)
    if held and a.apply:
        print(f"note: run {held.get('run')} is open; what normalize rewrites becomes part of that run")
    ev = evidence_eol(root, cfg)
    raw_all = set(ev["git"]) | set(ev["folder"]) | set(ev["unknown"])
    raw_take = (set(ev["git"]) | set(ev["unknown"])) if choice == "git" else set()
    conv = converted_checkout(root)
    others = sum(1 for x in conv if not x.startswith(cfg["raw"] + "/"))
    todo, left = [], []
    for path in conv:
        if path.startswith(cfg["raw"] + "/") and path not in raw_take:
            if path not in raw_all:
                left.append(path)
            continue
        f, blob = root / path, git_show(root, "", path)          # the version in git's index
        cur = f.read_bytes() if f.is_file() and not f.is_symlink() else None
        if blob is not None and cur is not None and cur != blob and cur.replace(b"\r\n", b"\n") == blob:
            todo.append((path, blob))
        else:
            left.append(path)                                    # also changed in content: a person looks at it
    label = "normalize-" + now(cfg).strftime("%Y%m%d-%H%M%S")
    if a.apply and todo:
        set_trash_ignored(root)
        for path, blob in todo:                                  # the copy as it was is kept: nothing is lost
            no_link_above(root, path)
            bits = stat.S_IMODE(os.stat(root / path).st_mode)
            dst = root / "_to_delete" / label / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(root / path, dst)
            atomic_write(root / path, blob)
            try:
                os.chmod(root / path, bits)                      # the same permissions as before (the executable bit)
            except OSError:
                pass
        refresh_stat(root, [path for path, _ in todo], guard=True)      # same content as git stores: nothing is staged
    n_raw = sum(1 for path, _ in todo if path in raw_all)
    verb = "rewritten" if a.apply else "would be rewritten"
    print(f"normalize: {len(todo)} file(s) {verb} with the line endings git stores"
          + (f" ({n_raw} of them evidence)" if n_raw else "") + (f", for example {todo[0][0]}" if todo else "")
          + (f"; their previous copies are in _to_delete/{label}/" if a.apply and todo else "")
          + ("" if a.apply or not todo else ". Re-run with --apply to write."))
    if left:
        print(f"left alone, because more than line endings differs: {some(left, 3)}")
    rc = 0
    if choice == "folder":
        skipped = ev["git"]
        rc = store_evidence(root, cfg, ev["folder"] + ev["unknown"], a.apply)
        if skipped:
            print(f"evidence: {len(skipped)} file(s) are registered with the bytes git stores ({some(skipped, 3)}); for "
                  "those the folder copy is the converted one: `kac normalize --apply --raw git`.")
    elif choice == "git":
        if ev["folder"]:
            print(f"evidence: {len(ev['folder'])} file(s) are registered with the bytes in the folder ({some(ev['folder'], 3)}); "
                  "for those git holds the converted copy: `kac normalize --apply --raw folder`.")
    elif raw_all:
        if ev["git"]:
            print(f"evidence: {len(ev['git'])} file(s) are registered with the bytes git stores, so the folder copy was "
                  f"converted ({some(ev['git'], 3)}): `kac normalize --apply --raw git` rewrites the folder copy.")
        if ev["folder"]:
            print(f"evidence: {len(ev['folder'])} file(s) are registered with the bytes in the folder, so git holds a "
                  f"converted copy ({some(ev['folder'], 3)}): `kac normalize --apply --raw folder` makes git store them.")
        if ev["unknown"]:
            print(f"evidence: {len(ev['unknown'])} file(s) that are not registered yet differ from git in line endings "
                  f"({some(ev['unknown'], 3)}). One copy was converted and the kit cannot tell which.\n"
                  f"  - A checkout converted the folder (typical sign: other text files are affected too; here {others}): "
                  "git holds the originals. Run `kac normalize --apply --raw git`.\n"
                  "  - The files arrived with CRLF and git converted its own copy when they were added: the folder holds "
                  "the originals. Run `kac normalize --apply --raw folder`.")
    return rc


def dependency_dirs(root, cfg):
    """Dependency folders (node_modules, virtual environments) under the pinned folders: thousands of files that
    no person reviews. `adopt` lists them under kit.unpinned, where the exception is visible."""
    out = []
    for pat in cfg["instructions"]:
        base = pat[:-5]
        if pat.endswith("/**/*") and base and (root / base).is_dir():
            for folder, subdirs, _ in os.walk(root / base):
                for d in [d for d in subdirs if d in DEP_DIRS]:
                    out.append((Path(folder) / d).relative_to(root).as_posix())
                subdirs[:] = [d for d in subdirs if d not in DEP_DIRS and d != ".git"]
    return sorted(set(out))


def kit_yaml(root, cfg):
    d = detect(root)

    def block(key, items, note):
        if not items:
            return [f"  {key}: []    # {note}"]
        return [f"  {key}:    # {note}"] + [f"    - \"{c}\"" for c in items]

    lines = ["kit:    # read by tools/kac.py (Knowledge-as-Code kit v2); everything here has a default",
             f"  pages: {json.dumps(d['pages'])}",
             f"  records: {json.dumps(d['records'])}",
             f"  tz: {cfg['tz']}    # IANA time zone used in log.md and run ids",
             f"  tz_label: \"{cfg['tz_label']}\"    # label written in log.md; empty = automatic (EDT, CET, ...)",
             "  agent: claude"]
    lines += block("checks", d["checks"], "run in order by `kac check` and `kac commit`; {python} = this interpreter")
    lines += block("rebuild", d["rebuild"], "run after `kac undo` to regenerate derived files")
    lines += block("unpinned", dependency_dirs(root, cfg), "under the pinned folders but not reviewed: dependency folders, tool state")
    lines += [f"  sync: \"{d['sync']}\"    # onedrive | dropbox | icloud | \"\" when the folder is not synced",
              "  devices: []    # computer names, to spot sync conflict copies such as page-NAME.md",
              "  start_files: [AGENTS.md, CLAUDE.md, index.md]    # what every session reads first",
              "  backup: {dir: .backup, keep: 14}"]
    return "\n".join(lines) + "\n"


def cmd_adopt(root, cfg, a):
    if not getattr(a, "explicit_root", True) and Path(os.getcwd()).resolve() != root:
        die(f"adopt would set up {root}, which is not the current folder. Run it from the top folder of the wiki, "
            "or name the folder with --root.")
    if os.environ.get("KAC_CONFIG"):
        die("adopt writes kac.yaml inside the wiki; run it without --config / KAC_CONFIG.")
    if not is_repo(root):
        die("this folder is not a git repository. The files adopt writes and the change units need one: run "
            "`git init` here first (guide section 13.3). If this wiki keeps its history with its own tool, do not "
            "adopt it; use the read-only commands with --config instead (guide section 13.15).")
    plan = []
    kf = root / "kac.yaml"
    if not cfg["has_kit"]:
        plan.append(("kac.yaml", "append the `kit:` section" if kf.exists() else "create with a `kit:` section"))
    pattern = cfg["raw"] + "/**"
    raw_rule = RAW_RULE.format(pattern=json.dumps(pattern) if re.search(r"\s", pattern) else pattern)
    if not (root / ".gitattributes").exists():
        plan.append((".gitattributes", "create (line endings, union merge for logs, evidence stored byte for byte)"))
    else:                                         # an existing file: evidence of every kind must come out as `-text`
        probes = [f"{cfg['raw']}/probe{ext}" for ext in ("", ".txt", ".md", ".csv", ".json", ".log", ".html", ".xml", ".yaml")]
        vals = git(root, "check-attr", "-z", "text", "--", *probes)[1].split("\0")
        if any(vals[j + 2] != "unset" for j in range(0, len(vals) - 2, 3)):
            plan.append((".gitattributes+", "append the evidence rule (evidence is stored byte for byte)"))
    gi = (root / ".gitignore").read_text(encoding="utf-8").splitlines() if (root / ".gitignore").exists() else []
    missing = [x for x in GITIGNORE_LINES if x not in gi]
    if missing:
        plan.append((".gitignore", f"append {missing}"))
    if not manifest_path(root).exists():
        plan.append((".kac/manifest.jsonl", f"hash {len(raw_files(root, cfg))} raw files"))
    if not pins_path(root).exists():
        plan.append((".kac/pins.json", f"pin {len(instruction_files(root, cfg))} instruction files"))
    print(f"adopt: {root}")
    if not plan:
        print("adopt: nothing to add; this wiki already has the kit v2 safety files")
        return 0
    for f, what in plan:
        print(("write " if a.apply else "would ") + f"{f.rstrip('+')}: {what}")
    if not a.apply:
        print(("\nProposed kit section:\n" + kit_yaml(root, cfg) if not cfg["has_kit"] else "")
              + "\nRe-run with --apply to write. Nothing existing is modified except by appending.")
        return 0
    for f, _ in plan:
        if f == "kac.yaml":
            if kf.exists():
                append_text(kf, "\n" + kit_yaml(root, cfg))
            else:
                atomic_write(kf, f"name: {json.dumps(root.name)}\n" + kit_yaml(root, cfg))
        elif f == ".gitattributes":
            atomic_write(root / f, GITATTRIBUTES + raw_rule)
        elif f == ".gitattributes+":
            append_text(root / ".gitattributes", "\n" + raw_rule)
        elif f == ".gitignore":
            append_text(root / f, "\n".join(missing) + "\n")
    cfg = load_cfg(root)
    if not manifest_path(root).exists():
        register_raw(root, cfg, "adopt")
    if not pins_path(root).exists():
        write_pins(root, cfg)
    ev = evidence_eol(root, cfg)
    unsettled = ev["unknown"] + ev["folder"] + ev["git"]
    if unsettled:
        print(f"\nnote: {len(unsettled)} evidence file(s) differ from git's copy in their line endings ({some(unsettled, 3)}). "
              "They are not in the manifest yet, and no run will commit while git lists them as changed. Run "
              "`kac normalize`: it shows them and the two ways to settle this.")
    local = sorted(split_pins(root, cfg)[1])
    if local:
        print(f"\nnote: {len(local)} instruction file(s) are ignored by git ({some(local, 3)}). They are pinned for this "
              "copy only. If a tool rewrites them during runs, list them under `kit: unpinned:` in kac.yaml.")
    print("\nDone. Review `git status` and kac.yaml (time zone, checks), then make the change unit:\n"
          "  python3 tools/kac.py commit schema \"adopt kit v2\" --repin      (add --lenient for a wiki with older pages)")
    return 0


# ----------------------------------------------------------------------------- doctor
def cmd_doctor(root, cfg, a):
    F = []          # (priority, code, title, detail, fix)
    info = {}

    def add(prio, code, title, detail="", fix=""):
        F.append((prio, code, title, detail, fix))

    b = cfg["budgets"]
    sync = (a.sync or cfg["sync"] or "").lower()
    repo = is_repo(root)
    info["layout"] = ("records + pages (Spec v1 style)" if cfg["records"] else "pages (folder style)") + \
                     f"; spec={cfg['spec'] or 'unset'}; kit section={'yes' if cfg['has_kit'] else 'no'}"
    # --- git and storage
    if not repo and str(cfg["history"]).lower() == "external":
        info["history"] = "kept by the wiki's own tool; the kit does not check it (guide section 6.13)"
    elif not repo:
        signs = [n for n, hit in (("ledger/", (root / "ledger").is_dir()), ("objects/", (root / "objects").is_dir()),
                                  ("a release pointer in state/", any(root.glob("state/*release*.json"))),
                                  ("HISTORY.md", (root / "HISTORY.md").exists()),
                                  ("CHANGELOG.md", (root / "CHANGELOG.md").exists())) if hit]
        add("P0" if not signs else "P1", "D01", "Not a git repository",
            "The kit's change units, undo and snapshots need git."
            + (f" This folder shows signs of a history mechanism of its own ({', '.join(signs)}). If it has one, set "
               "`history: external` in the kit configuration and check that mechanism against guide section 6.13."
               if signs else " Without another mechanism there is no history and no undo."),
            "git init && a first commit (guide section 13.3); or `history: external`")
    else:
        gitdir = (root / ".git")
        if prefix(root):
            info["repository"] = "this folder is `" + prefix(root) + "` inside a larger repository"
        if gitdir.is_file():
            info["git dir"] = "separate (" + gitdir.read_text(encoding="utf-8", errors="replace").strip()[:80] + ")"
        if sync and gitdir.is_dir():
            add("P0", "D02", f"The .git folder is inside a {sync} sync folder",
                "Git's own FAQ says a sync service must not sync any part of a repository (risk: missing objects, broken refs).",
                "Guide section 5.6: move the wiki out of the sync folder, or at least keep a verified bundle (kac snapshot).")
        elif not sync:
            info["sync"] = "unknown (pass --sync onedrive if this folder is inside a sync folder)"
        remotes = git(root, "remote")[1].split()
        bdir = backup_dir(root, cfg)                         # the same place `kac snapshot` writes to (KAC_BACKUP wins)
        bundles = own_bundles(bdir, bundle_name(cfg, root)) if has_head(root) else []      # this wiki's own, oldest first
        inside = bdir == root or root in bdir.parents
        if not remotes and not bundles:
            add("P0", "D03", "No copy of the history outside the repository", "No git remote and no bundle of this wiki in "
                f"{bdir}.", "kac snapshot (a verified bundle), and/or add a private remote; a sync folder is not a backup.")
        elif not remotes:
            info["backup"] = f"{len(bundles)} bundle(s), newest {bundles[-1].name}"
            if inside and not sync:
                add("P1", "D03", "The only copies of the history are inside this folder",
                    f"{len(bundles)} bundle(s) in {bdir.name}/; a lost disk or a deleted folder takes them along.",
                    "Point KAC_BACKUP or kit.backup.dir to a folder on another disk or in a synced folder, or copy the "
                    "newest bundle there after each snapshot.")
        if not git(root, "tag")[1].strip():
            add("P2", "D04", "No tags", "Named restore points make 'go back to before X' a one-liner.", "kac snapshot")
        co = dict(l.split(": ", 1) for l in git(root, "count-objects", "-v")[1].splitlines() if ": " in l)
        info["git"] = f"{git(root, 'rev-list', '--count', 'HEAD')[1].strip() or 0} commits; loose objects {co.get('count')}, packs {co.get('packs')}"
        if int(co.get("count", 0)) > (1000 if sync else 5000):
            add("P2", "D05", f"{co.get('count')} loose git objects ({int(co.get('size', 0)) // 1024} MB)",
                "Thousands of small files: slower, and each one is a separate upload for a sync client.",
                "Run `git gc` while no agent is writing and the sync client is paused.")
        ga = root / ".gitattributes"
        if not ga.exists():
            add("P1", "D06", "No .gitattributes", "Without it a Windows checkout can rewrite line endings, which changes file hashes.",
                "kac adopt --apply")
        crlf = []
        for line in git(root, "ls-files", "--eol")[1].splitlines():
            meta_, _, path_ = line.partition("\t")
            if path_.startswith(cfg["raw"] + "/") or "-text" in meta_:
                continue                                  # evidence and binaries keep their bytes
            if "crlf" in meta_.split()[0] or "mixed" in meta_.split()[0]:
                crlf.append(path_)
        if crlf:
            add("P1", "D07", f"{len(crlf)} text file(s) are stored in git with CRLF or mixed line endings",
                ", ".join(crlf[:5]), f"With .gitattributes in place: git add --renormalize -- . \":(exclude){cfg['raw']}\" && "
                "kac commit schema \"normalize line endings\" (evidence is left out: its bytes must not change)")
        conv = [x for x in converted_checkout(root) if not x.startswith(cfg["raw"] + "/")]
        if conv:
            add("P2", "D30", f"{len(conv)} text file(s) have CRLF line endings in the folder but LF in git",
                some(conv, 5) + ". A checkout converted them (Git for Windows), an editor saved them that way, or they "
                "were renormalized in git and not yet rewritten here.", "kac normalize --apply (the previous copies are kept)")
        ev = evidence_eol(root, cfg)
        if ev["git"] or ev["folder"] or ev["unknown"]:
            n = len(ev["git"]) + len(ev["folder"]) + len(ev["unknown"])
            add("P1", "D33", f"{n} evidence file(s) differ between the folder and git in their line endings",
                some(ev["unknown"] + ev["folder"] + ev["git"], 5) + ". One of the two copies was converted, so one of them "
                "is not the evidence as it arrived.", "kac normalize (it lists them and says what to do; guide section 5.7)")
        if git(root, "symbolic-ref", "-q", "HEAD")[0] != 0:
            add("P1", "D31", "HEAD is detached", "A commit made now belongs to no branch and is lost at the next checkout.",
                "git switch <branch> (after a bisect: git bisect reset)")
        dirty = changed_paths(root, cfg)
        if dirty:
            add("P1", "D19", f"{len(dirty)} uncommitted change(s)", ", ".join(dirty[:5]),
                "Every run should end in a commit: kac commit OP \"TITLE\"")
        rc, out, _ = git(root, "log", "-n30", "--format=%(trailers:key=Run-Id,valueonly,separator=%x2C)|")
        last = [x for x in out.replace("\n", "").split("|")][:-1]
        with_id = sum(1 for x in last if x.strip())
        info["traceability"] = f"{with_id}/{len(last)} of the last commits carry a Run-Id"
        if last and with_id < len(last) * 0.5:
            add("P2", "D20", "Commits are not change units yet", info["traceability"],
                "Finish runs with `kac commit OP \"TITLE\"` so each run can be listed and undone by id.")
        lk = read_lock(root)
        if lk:
            age = (time.time() - float(lk.get("started", 0))) / 3600
            info["lock"] = f"held by {lk.get('run')} ({age:.1f} h)"
            if age > cfg["lock_stale_hours"]:
                add("P1", "D28", f"Stale writer lock ({age:.0f} h)", str(lk.get("title")),
                    "A person decides: kac abort (sets the changes aside) or kac commit OP TITLE --steal (keeps them)")
        tracked = git(root, "ls-files")[1].splitlines()
        restricted = [t for t in tracked if {"restricted", "_restricted", "secret", "secrets", "private"}
                      & set(t.lower().split("/")[:-1])]
        if restricted:
            add("P1" if remotes else "P2", "D29", f"{len(restricted)} restricted file(s) are tracked in git",
                "Every clone, bundle and remote copy of this repository contains them, in all past versions.",
                "Keep every copy private and encrypted, or move the restricted store to its own repository (guide section 5.5).")
    # --- conflict copies and stray databases
    devices = [x.lower() for x in (a.device or []) + list(cfg["devices"])]
    conflict, dbs = [], []
    for folder, subdirs, names in os.walk(root):
        relf = Path(folder).relative_to(root).as_posix()
        if relf == ".git" and not a.deep:
            subdirs[:] = [x for x in subdirs if x != "objects"]      # content-addressed; slow on mounts
        subdirs[:] = [x for x in subdirs if x not in ("node_modules", "__pycache__")]
        for fname in names:
            name, stem = fname.lower(), Path(fname).stem.lower()
            r = (relf + "/" if relf != "." else "") + fname
            if "conflicted copy" in name or ".sync-conflict-" in name or any(
                    stem.endswith("-" + d) or re.search(rf"-{re.escape(d)}-\d+$", stem) for d in devices):
                conflict.append(r)
            if Path(fname).suffix.lower() in (".db", ".sqlite", ".sqlite3", ".duckdb") and not r.startswith(".git/"):
                dbs.append(r)
    if conflict:
        inside_git = [c for c in conflict if c.startswith(".git/")]
        add("P0" if inside_git else "P1", "D18", f"{len(conflict)} sync conflict copies", ", ".join(conflict[:5]),
            "Compare each copy with its original, keep one, delete the other; inside .git restore from a bundle.")
    if dbs:
        add("P1" if sync else "P2", "D23", f"{len(dbs)} database file(s) inside the wiki folder", ", ".join(dbs[:4]),
            "Caches belong on local disk outside the wiki (SQLite needs real file locking); delete or relocate.")
    # --- raw evidence
    rf = raw_files(root, cfg)
    live, rows, bad = load_manifest(root)
    info["raw"] = f"{len(rf)} files, {sum(p.stat().st_size for p in rf) // (1 << 20)} MB"
    if rf and not manifest_path(root).exists() and not repo:
        info["evidence"] = "no kit manifest (it needs git); check that the wiki's own tool records a hash for every source"
    elif rf and not manifest_path(root).exists():
        add("P1", "D08", "No hash manifest for raw evidence", "Tampering, sync damage or a bad restore would go unnoticed.",
            "kac adopt --apply (writes .kac/manifest.jsonl); kac verify --deep after any restore")
    elif rf:
        unreg = [rel(root, p) for p in rf if rel(root, p) not in live]
        changed = [rel(root, p) for p in rf if rel(root, p) in live and
                   (live[rel(root, p)].get("bytes") != p.stat().st_size or
                    (a.deep and live[rel(root, p)].get("sha256") != sha256_file(p)))]
        if changed:
            add("P0", "D08", f"{len(changed)} raw file(s) changed after registration", ", ".join(changed[:5]),
                "Restore from git or a backup; raw evidence must never change.")
        if unreg:
            add("P2", "D08", f"{len(unreg)} raw file(s) not in the manifest", ", ".join(unreg[:3]), "The next kac commit registers them.")
    for v in raw_violations(root, cfg)[:5]:
        add("P0", "D09", "Git shows a changed raw file", v, "git restore -- <path>")
    # --- instruction files
    for name in ("AGENTS.md", "CLAUDE.md"):
        f = root / name
        if f.exists():
            text = f.read_text(encoding="utf-8", errors="replace")
            n = len(text.splitlines())
            info[name] = f"{n} lines, {len(text.encode()) / 1024:.1f} KB"
            if n > b["instructions_lines"] or len(text.encode()) > b["instructions_kb"] * 1024:
                add("P1", "D10", f"{name} is long ({n} lines, {len(text.encode()) // 1024} KB)",
                    "It is loaded in every session; Codex stops reading at 32 KiB combined.",
                    "Keep rules and pointers; move procedures to skills or docs/ (guide section 7.9).")
    if not pins_path(root).exists() and not repo:
        info["instructions"] = (f"fingerprint {fingerprint(root, cfg)[0]} (no pins file without git; keep this value "
                                "outside the wiki and compare it with `kac verify --expect`)")
    elif not pins_path(root).exists():
        add("P1", "D11", "Instruction files are not pinned",
            "AGENTS.md, skills, tools and the schema are code; an unreviewed change should stop an unattended run.",
            "kac adopt --apply, then `kac verify` at the start of scheduled runs")
    else:
        d = pin_diff(root, cfg)
        if d and (d[0] or d[2]):
            add("P0", "D11", "Instruction files differ from their pins", ", ".join((d[0] + d[2])[:8]),
                "Review the change (`git diff`; a file that git ignores has no diff), then kac pin. "
                "Tool state that changes in every run belongs under `kit: unpinned:`.")
        if d and d[1]:
            add("P1", "D11", f"{len(d[1])} new instruction file(s) are not pinned", ", ".join(d[1][:5]),
                "Review them, then kac pin (or kac commit ... --repin)")
    only_here = sorted(split_pins(root, cfg)[1])
    if only_here:
        info["local files"] = f"{len(only_here)} instruction file(s) that git ignores, pinned for this copy only: {some(only_here, 4)}"
    runs_code = [k for k in local_surface(root) if k.startswith("hook:") or RUNS_CODE.fullmatch(k[len("setting:"):])]
    if runs_code:
        add("P2", "D32", f"{len(runs_code)} git hook(s) or setting(s) in this copy run a program", ", ".join(runs_code[:6]),
            "They are not versioned and no review sees them. Keep only what you put there yourself; "
            "`kac verify --expect` notices when one changes (guide section 9.4).")
    # --- pages
    sizes, nocard, longcard, badfm, hidden, active, secrets, stale, types = [], [], [], [], [], [], [], [], Counter()
    for p in iter_pages(root, cfg):
        r = rel(root, p)
        meta, body, err = load_page(p)
        sizes.append((p.stat().st_size, r))
        if err:
            badfm.append(f"{r} ({err})")
            continue
        types[str(meta.get("type"))] += 1
        raw_card = next((str(meta[f]) for f in cfg["card_fields"] if meta.get(f)), "")
        if not raw_card and meta.get("type") not in ("redirect", "index"):
            nocard.append(r)
        elif len(raw_card) > b["card_chars"]:
            longcard.append((len(raw_card), r))
        text = p.read_text(encoding="utf-8", errors="replace")
        t, bi, z = invisible_report(text)
        if t or bi:
            hidden.append(r)
        if active_content(body):
            active.append(r)
        if SECRET.search(text):
            secrets.append(r)
        when = stale_date(stale_value(meta, cfg))
        if when and when != "bad" and dt.datetime.now(dt.timezone.utc) >= when:
            stale.append(r)
    sizes.sort()
    if sizes:
        n = len(sizes)
        tot = sum(s for s, _ in sizes)
        info["pages"] = (f"{n} pages, {kb(tot)} (~{est_tokens(tot) / 1000:.0f}k tokens); median {kb(sizes[n // 2][0])}, "
                         f"p90 {kb(sizes[int(n * 0.9)][0])}, largest {kb(sizes[-1][0])}")
        info["types"] = ", ".join(f"{k} {v}" for k, v in types.most_common(12))
        big = [(s, r) for s, r in sizes if s > b["page_kb"] * 1024]
        if big:
            add("P2", "D12", f"{len(big)} page(s) over {b['page_kb']} KB",
                ", ".join(f"{r} ({s // 1024} KB)" for s, r in big[-5:]), "Split by topic, or move detail into raw/ and link it.")
    if badfm:
        add("P1", "D13", f"{len(badfm)} page(s) with missing or invalid front matter", "; ".join(badfm[:4]), "Fix the YAML block.")
    if nocard:
        add("P1", "D14", f"{len(nocard)} page(s) have no card", ", ".join(nocard[:5]),
            "Add a one-sentence `description` (or `summary`): it is the index line and the search snippet.")
    if longcard:
        longcard.sort(reverse=True)
        avg = sum(n for n, _ in longcard) // len(longcard)
        add("P2", "D15", f"{len(longcard)} card(s) longer than {b['card_chars']} characters (average {avg})",
            f"longest: {longcard[0][1]} ({longcard[0][0]})", "Listings should show one short sentence; cut in the generator, not by hand.")
    if hidden:
        add("P0", "D16", f"Hidden Unicode in {len(hidden)} page(s)", ", ".join(hidden[:5]), "Inspect, remove, and find the source it came from.")
    if active and cfg["inert_pages"]:
        add("P1", "D17", f"{len(active)} page(s) are not inert", ", ".join(active[:5]) + ". They would fetch, run or hide "
            "something when rendered, or hold markup that renderers read differently; `kac check` names the reason for each.",
            "Download remote images into raw/assets and link the local file; remove HTML embeds; put samples in fenced blocks.")
    if secrets:
        add("P0", "D21", f"Possible secrets in {len(secrets)} page(s)", ", ".join(secrets[:5]), "Rotate the secret, then remove it from history (guide section 5.9).")
    if stale:
        add("P2", "D22", f"{len(stale)} page(s) are past their review date", ", ".join(stale[:5]), "Re-verify against sources or mark deprecated.")
    # --- entry-point sizes
    for f in ("index.md", cfg["log"]):
        fp = root / f
        if fp.exists():
            size_kb = fp.stat().st_size / 1024
            info[f] = f"{size_kb:.0f} KB (~{est_tokens(fp.stat().st_size) / 1000:.1f}k tokens)"
            lim = b["index_kb"] if f == "index.md" else b["log_kb"]
            if size_kb > lim:
                add("P1" if f == "index.md" else "P2", "D24", f"{f} is {size_kb:.0f} KB (budget {lim} KB)",
                    "A file that agents read at the start of every session should be a small map.",
                    "Guide section 7.3: shorten cards, list live items only, search for the rest."
                    if f == "index.md" else "Start a new log file per year; keep the old one.")
    # --- records
    for pat in cfg["records"]:
        for f in glob_many(root, [pat]):
            rows_, bad_ = jl_read(f)
            if bad_:
                add("P1", "D25", f"{rel(root, f)} has invalid JSON lines", str(bad_[:5]), "Repair from git history.")
            seen = Counter(str(x.get("id")) for x in rows_ if isinstance(x, dict) and x.get("id"))
            dup = [k for k, n in seen.items() if n > 1]
            if dup:
                add("P1", "D25", f"{rel(root, f)} has duplicate ids", str(dup[:5]), "Merge the records; ids must be unique.")
    start = [(f, (root / f).stat().st_size) for f in cfg["start_files"] if (root / f).is_file()]
    if start:
        tot = sum(n for _, n in start)
        info["session start"] = (f"{kb(tot)} (~{est_tokens(tot) / 1000:.1f}k tokens): "
                                 + ", ".join(f"{f} {kb(n)}" for f, n in start))
        if tot > b["start_kb"] * 1024:
            add("P1", "D26", f"Every session starts by reading {tot / 1024:.0f} KB (budget {b['start_kb']} KB)",
                info["session start"], "Guide sections 7.2-7.3: start with `kac status`; keep a small map and a short state page; fetch the rest on demand.")
    has_evals = (root / "evals").is_dir() and any((root / "evals").iterdir())
    if not has_evals:
        add("P2", "D27", "No golden questions", "Nothing tells you when retrieval gets worse.", "Add evals/golden.yaml with 20+ real questions.")
    order = {"P0": 0, "P1": 1, "P2": 2}
    F.sort(key=lambda x: (order[x[0]], x[1]))
    if a.json:
        print(json.dumps({"root": str(root), "info": info,
                          "findings": [dict(zip(("priority", "code", "title", "detail", "fix"), f)) for f in F]},
                         ensure_ascii=False, indent=1))
    else:
        print(f"kac doctor {VERSION}: {cfg['name']}")
        for k, v in info.items():
            print(f"  {k:<13} {v}")
        for prio, code, title, detail, fix in F:
            print(f"\n[{prio}] {code} {title}")
            if detail:
                print(f"      {detail[:300]}")
            if fix:
                print(f"      fix: {fix}")
        c = Counter(f[0] for f in F)
        print(f"\n{c.get('P0', 0)} P0 (data safety), {c.get('P1', 0)} P1 (integrity and efficiency), {c.get('P2', 0)} P2 (polish)")
    return 1 if any(f[0] == "P0" for f in F) else 0


# ----------------------------------------------------------------------------- main
def main(argv=None):
    for stream in (sys.stdout, sys.stderr):       # Windows pipes default to a legacy code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="kac", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root")
    ap.add_argument("--config")
    ap.add_argument("--version", action="version", version=f"kac {VERSION}")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("doctor"); p.add_argument("--deep", action="store_true"); p.add_argument("--json", action="store_true")
    p.add_argument("--sync"); p.add_argument("--device", action="append")
    p = sub.add_parser("adopt"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("normalize"); p.add_argument("--apply", action="store_true"); p.add_argument("--raw", choices=["git", "folder"])
    p = sub.add_parser("verify"); p.add_argument("--deep", action="store_true"); p.add_argument("--expect")
    sub.add_parser("pin")
    p = sub.add_parser("check"); p.add_argument("--lenient", action="store_true"); p.add_argument("--since")
    p = sub.add_parser("begin"); p.add_argument("op"); p.add_argument("title")
    p.add_argument("--steal", action="store_true"); p.add_argument("--allow-dirty", action="store_true")
    p = sub.add_parser("commit"); p.add_argument("op", nargs="?"); p.add_argument("title", nargs="?")
    p.add_argument("-t", "--trailer", action="append"); p.add_argument("--steal", action="store_true")
    p.add_argument("--repin", action="store_true"); p.add_argument("--run")
    p.add_argument("--lenient", action="store_true")
    sub.add_parser("abort")
    p = sub.add_parser("undo"); p.add_argument("run_id"); p.add_argument("--with-raw", action="store_true")
    p.add_argument("--reason"); p.add_argument("-t", "--trailer", action="append")
    p = sub.add_parser("status"); p.add_argument("-n", type=int, default=5)
    p = sub.add_parser("runs"); p.add_argument("-n", type=int, default=15)
    p = sub.add_parser("history"); p.add_argument("page"); p.add_argument("-n", type=int, default=20)
    p = sub.add_parser("show"); p.add_argument("page"); p.add_argument("--at", required=True)
    p = sub.add_parser("index"); p.add_argument("--check", action="store_true"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("-k", type=int, default=8)
    p.add_argument("--type"); p.add_argument("--state"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("list"); p.add_argument("--type"); p.add_argument("--where", action="append")
    p.add_argument("--count-by"); p.add_argument("--fields"); p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("eval"); p.add_argument("-k", type=int, default=5)
    sub.add_parser("pack")
    p = sub.add_parser("snapshot"); p.add_argument("--name")
    a = ap.parse_args(argv)
    if not a.cmd:
        print(__doc__)
        return 2
    a.explicit_root = bool(a.root or os.environ.get("KAC_ROOT"))
    if a.config:
        os.environ["KAC_CONFIG"] = str(Path(a.config).resolve())       # the checks the kit starts see the same file
    root = find_root(a.root)
    cfg = load_cfg(root)
    fn = {"doctor": cmd_doctor, "adopt": cmd_adopt, "normalize": cmd_normalize, "verify": cmd_verify, "pin": cmd_pin,
          "check": cmd_check,
          "begin": cmd_begin, "commit": cmd_commit, "abort": cmd_abort, "undo": cmd_undo, "runs": cmd_runs,
          "history": cmd_history, "show": cmd_show, "status": cmd_status,
          "index": cmd_index, "search": cmd_search, "list": cmd_list, "eval": cmd_eval, "pack": cmd_pack, "snapshot": cmd_snapshot}[a.cmd]
    return fn(root, cfg, a)


class _Out:
    """stdout that survives a closed pipe (`kac commit ... | head -1`). A run must finish whatever happens to
    its output, and its exit code must say what happened to the wiki, not to the pipe."""

    def __init__(self, stream):
        self._s = stream

    def _dead(self):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), self._s.fileno())
        except (OSError, ValueError):
            pass

    def write(self, text):
        try:
            return self._s.write(text)
        except (BrokenPipeError, ValueError):
            self._dead()
            return len(text)

    def flush(self):
        try:
            self._s.flush()
        except (BrokenPipeError, ValueError):
            self._dead()

    def __getattr__(self, name):
        return getattr(self._s, name)


if __name__ == "__main__":
    sys.stdout = _Out(sys.stdout)
    try:
        code = main() or 0
        sys.stdout.flush()
    except BrokenPipeError:                       # the reader went away before any work was done
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.__stdout__.fileno())
        code = 0
    sys.exit(code)
