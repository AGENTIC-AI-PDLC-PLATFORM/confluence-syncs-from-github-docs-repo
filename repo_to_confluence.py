#!/usr/bin/env python3
"""
repo_to_confluence.py - mirror the docs of a git repository into Confluence through
the Atlassian Rovo MCP Server, as a page tree that follows the repository's folders, replaying
git history so that every Confluence page version corresponds to one version of the source
file (a release tag or a commit).

    repository design-authority-repo           Confluence space
    ---------------------------------------    -----------------------------------------------------------------
    README.md                                  design-authority-repo — knowledge base     root page
    docs/architecture.md                       ├── design-authority-repo · ADR-DECISIONS  folder page + ADR index
    ADR-DECISIONS/STEP 01 - ....md             │   ├── ADR-001 - Use YugabyteDB for ...    id assigned: id + H1 title
    ADR-DECISIONS/STEP 02 - ....md             │   └── STEP 02 - REPO - ...                id still ADR-NNN: file name
    TEMPLATES/TEMPLATE-ADR.MD                  ├── design-authority-repo · TEMPLATES      folder page
                                               │   └── TEMPLATE-ADR                        template: front matter as YAML
                                               ├── design-authority-repo · docs           folder page
                                               │   └── architecture
                                               └── README

    history of docs/architecture.md (tags)     versions of the page "architecture"
    v1.0.0  created                        ->  version 1  "v1.0.0 · 3f2a9c1 · 2025-01-10 · Jane · Initial design"
    v1.1.0  changed                        ->  version 2  "v1.1.0 · 8be01d4 · 2025-03-02 · Omar · Add event bus"
    v1.2.0  (file unchanged)               ->  (no new version)
    v2.0.0  changed                        ->  version 3  "v2.0.0 · ..."

How it works
  * Confluence creates a new page version on every update and stamps it with "now" and
    with the token owner. You cannot backdate or re-author versions. So the script replays
    versions oldest -> newest and carries the git metadata (tag, commit, date, author,
    subject) in the version message and in a provenance banner at the top of the page.
  * Page tree: the repository's root page, one page per folder below it, nested like the
    repository, and each file's page under the page of its folder. A folder page lists the pages
    below it; the page of an ADR folder also holds the ADR index (see ADRs).
  * Titles are unique in a Confluence space:
        root page     <repo> — knowledge base
        folder page   <repo> · <folder path>              e.g. design-authority-repo · ADR-DECISIONS
        file page     the file name without .md (or .MD)  e.g. README, TEMPLATE-ADR
        ADR page      <id> - <H1 title> once the front-matter id is assigned (ADR_TITLE), e.g.
                      "ADR-002 - Zone isolation in IaC: ...", the file name while the id is a placeholder
    When the title is taken in the space by another page, a file page is titled
    "<title> (<repo>/<folder>)" and a folder page "<title> (<org/repo>)".
  * ADRs are the Markdown files under a folder named ADR-DECISIONS (--adr-folder) whose front
    matter has an id or status key. Each ADR page shows its front matter as a metadata table below
    the banner. The ADR-DECISIONS page holds an index of the ADRs below it, generated from their
    front matter (columns in ADR_INDEX_COLUMNS: ID, title, status, date, decision-makers, owner,
    supersedes, superseded by, last commit). An id that is not assigned (ADR-NNN) or a date that is
    not set (YYYY-MM-DD) is reported as a warning on every run, --dry-run included, and the index
    lists that ADR as "ID not assigned" / "not set". Front matter is read with PyYAML.
  * Templates are the Markdown files under a folder named TEMPLATES (--templates-folder), e.g.
    TEMPLATES/TEMPLATE-ADR.MD. Each gets a page under the TEMPLATES folder page, titled by its file
    name, showing its front matter as a YAML block above its text. A template is never an ADR: no
    metadata table, no index row, no placeholder warning.
  * Links between pages (index titles, ADR ids in supersedes / superseded-by / related, folder
    listings) are Confluence page URLs, so they need the site URL: from --cloud-id given as
    https://<site>.atlassian.net, else from getAccessibleAtlassianResources. When a link target
    appears later (an id assigned, a page created), the page that links to it gets a version
    commented "Links updated".
  * Every page starts with a banner (BANNER_TEXT: do not edit here, page-level comments only,
    open a PR in <repo/path>) and a link to the exact source of that version: repository, path
    and commit SHA. The banner ends with a marker, `sync-ref: <commit> banner: <id> page: <id>`:
    the commit the page was generated from, the banner wording, and the file or folder the page
    belongs to, so a page is never mistaken for another file's even with a short title. On every
    run the script reads the marker and only appends versions whose commit is not already an
    ancestor of it, so runs are idempotent and incremental. The banner is generated into every
    body the sync writes, after the optional --transform-cmd, so neither the source file nor the
    transform can drop it. Pages that are already up to date are re-read every --verify-hours: if
    the banner was removed in Confluence or its text is out of date, the sync rebuilds the page
    from the last synced commit as a new version commented "Banner restored" / "Banner updated".
  * Those rewrites, and --adopt takeovers, change a page that git did not change, so they only
    run once the run has proof that page reads return the banner: a page whose banner came back
    intact in this run, or one extra read of a page synced earlier. Without proof nothing is
    rewritten (normal versions are still synced) and the run exits with an error. A page whose
    banner still does not read back after a rewrite is paused for 7 days and listed every run.
  * Renamed files keep their page: it is retitled, moved under the page of its new folder (when
    the update tool takes a parent id; --inspect shows it), its history continues, and the version
    is commented "Renamed from <old path>". Pages of files and folders no longer in git are left
    unchanged and listed as warnings on every run until they are archived or deleted in Confluence.
  * Rovo MCP tool names changed between Atlassian MCP v1 (createConfluencePage /
    updateConfluencePage) and v2 (createConfluenceContent / updateConfluenceContent).
    The script detects which set your endpoint exposes. Atlassian switches v1 endpoints
    to v2 tools on 1 March 2027; nothing to change here when that happens.
  * Endpoints do not all expose every tool. Without a spaces tool (getConfluenceSpaces), the
    space id is read from a page of the space: one CQL search and one page read, cached by
    --state-file; --space-id skips the lookup. Without getAccessibleAtlassianResources, pass
    --cloud-id. --inspect lists what your endpoint is missing.

Requirements: Python 3.10+, git, `pip install mcp pyyaml` (MCP Python SDK 1.x or 2.x).

Auth (headless). An org admin must enable API-token auth for the Rovo MCP Server
(Atlassian Administration > Rovo > Rovo MCP server > Authentication).
    ATLASSIAN_SERVICE_KEY=<service-account API key>             -> Authorization: Bearer
    or ATLASSIAN_EMAIL=<email> + ATLASSIAN_API_TOKEN=<token>    -> Authorization: Basic
A service account is recommended: it is the author shown on every synced version.

Safety
  * One run at a time on one machine: a run holds an exclusive lock (--lock-file, default
    ~/.cache/kb-sync/sync.lock) for its whole duration, released by the OS even after a crash.
    A second run on the same machine exits with status 3 and does nothing. The lock is not
    distributed: it does not reach other machines, and every GitHub Actions run gets a fresh
    runner. In GitHub Actions, the workflow serializes the runs (confluence-kb-sync.yml):
        concurrency:
          group: confluence-kb-sync
          cancel-in-progress: false
    A run in progress always finishes; a new run waits, and replaces a run already waiting. The
    group covers the runs of one repository: a sync from another repository into the same space
    can run at the same time, which is safe (each repository writes only its own pages) but
    shares the hourly MCP call budget. Start syncs from that workflow only, not by hand elsewhere.
  * A write is never retried after a lost connection, since it may have landed: the run counts
    a failure and the next run reads the page's sync-ref marker and pushes only what is missing.
  * The state file never runs ahead of Confluence: a page's entry is written only after the page's
    writes succeeded, and a queued banner rewrite is recorded as seen, not as done. A state file
    saved after a failed run (the workflow saves it with if: always()) is therefore safe to reuse;
    a lost, evicted or older state file only costs MCP calls, since pages are found by title and
    recognized by their marker. A page is never created twice: when a title is taken by a page that
    search does not return yet (a run moments ago), the file is left for the next run instead of
    getting a second page under its fallback title.
  * --transform-cmd runs as an argument list without a shell (--transform-shell to opt in), in
    an empty temporary folder, without this script's credentials or any variable whose name
    contains TOKEN, SECRET, PASSWORD or PRIVATE (--transform-env NAME passes one through). Only
    people allowed to run commands on the runner should be able to set it: in GitHub Actions
    keep it in the workflow file or an admin-only variable, never in workflow_dispatch inputs
    or dispatch payloads. Its input comes from the repository, so with an LLM transform anyone
    who can merge docs can put instructions in front of the model.
  * Front matter is read with a YAML loader that builds plain values only and refuses aliases.

Exit status
    0  everything requested was done and verified (placeholder ids and dates are warnings only)
    1  finished but incomplete: a page failed, or a banner restore/update or --adopt takeover
       was skipped or not verified on read-back (pages paused by an earlier run are only listed)
    2  fatal: invalid arguments, unusable state or lock file, authentication or MCP errors
    3  another run holds the lock; nothing was done
    With --inspect: 0 nothing wrong found, 1 a problem (or an inconclusive page probe),
    2 the endpoint cannot be listed or exposes no Confluence update tool.

Examples
    # 1. Plan only, no network: the page tree, titles and versions that would be written, the
    #    ADRs each index would list, and the placeholder warnings
    python repo_to_confluence.py --repo ./design-authority-repo --space-key KB --dry-run

    # 2. See which Rovo MCP tools and parameters your site exposes and what the sync will use;
    #    with --space-key, look up the space id; with --page-id, read that page exactly as the
    #    sync does: any page with content shows whether reads return page bodies, a page the
    #    sync created also shows whether its banner reads back intact
    python repo_to_confluence.py --inspect --cloud-id https://acme.atlassian.net
    python repo_to_confluence.py --inspect --cloud-id https://acme.atlassian.net --space-key KB --page-id 123456

    # 3. Backfill + incremental sync, one Confluence version per release tag
    python repo_to_confluence.py --repo ./design-authority-repo --space-key KB \\
        --cloud-id https://acme.atlassian.net --mode tags --include-head --state-file kb-sync.json

    # 4. One version per merged change on main, each file rewritten as a KB article by
    #    Claude Code in headless mode (needs ANTHROPIC_API_KEY with --bare). The LLM only
    #    runs for versions that are actually pushed.
    python repo_to_confluence.py --repo . --space-key KB --mode commits \\
        --transform-cmd 'claude --bare -p "Rewrite this repository document as a concise knowledge-base article in Markdown. Keep every fact. Output only the article."'
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import datetime as dt
import functools
import hashlib
import json
import math
import os
import posixpath
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlparse

try:
    import yaml
except ImportError:  # reported when a front matter has to be read: pip install pyyaml
    yaml = None

DEFAULT_ENDPOINT = "https://mcp.atlassian.com/v1/mcp"
DEFAULT_INCLUDE = ["README.md", "docs/**/*.md", "**/ADR-DECISIONS/**/*.md", "**/TEMPLATES/**/*.md"]
DEFAULT_ADR_FOLDERS = ["ADR-DECISIONS"]
DEFAULT_TEMPLATE_FOLDERS = ["TEMPLATES"]
DEFAULT_CALLS_PER_HOUR = 450  # under the Free plan's 500 MCP calls/hour; raise it on paid plans
MAX_VERSION_MESSAGE = 250
MAX_TITLE = 255  # Confluence page title limit
MAX_CANDIDATES = 12  # titles searched at once when a page is looked up without the state file
MAX_FRONT_MATTER = 65536
BLOCK_DAYS = 7  # pause banner rewrites on a page whose banner did not read back after a rewrite

# Banner at the top of every mirrored page, verbatim. {repo_path} becomes the file's path in
# the repository as a code span, linked to GitHub's editor on the synced branch (on a folder
# page: the folder, linked to its tree). Changing this text (or the banner layout in
# banner_lines / folder_banner_lines) makes the next sync update the banner on every page.
BANNER_TEXT = (
    "do not edit here. Git is the source of truth; every sync overwrites this page.\n"
    "\n"
    "💬 Use page-level comments only. Each sync replaces the full page body, which detaches "
    "inline comments from the text they were anchored to.\n"
    "✏️ To change content, open a PR in {repo_path}."
)

# Columns of the ADR index on the page of an ADR folder: (header, source). A source is a
# front-matter key, "title" (the H1 without its "ADR-NNN:" prefix, linked to the ADR page) or
# "commit" (last commit of the file). ADR ids in supersedes / superseded-by / related are linked
# to their pages. Editing this list changes the index on the next sync.
ADR_INDEX_COLUMNS = [
    ("ID", "id"), ("Title", "title"), ("Status", "status"), ("Date", "date"),
    ("Decision-makers", "decision-makers"), ("Owner", "owner"),
    ("Supersedes", "supersedes"), ("Superseded by", "superseded-by"), ("Last commit", "commit"),
]
# Title of an ADR page once its front-matter id is assigned: {id} is the id (ADR-002), {title} the
# H1 without its "ADR-NNN:" prefix, or the file name when there is no H1. With --state-file,
# changing it retitles the existing ADR pages on the next sync.
ADR_TITLE = "{id} - {title}"
ADR_REF_KEYS = ("supersedes", "superseded-by", "related")
ADR_ID_RE = re.compile(r"ADR-(\d+)", re.I)                            # an assigned id, in full
ADR_REF_RE = re.compile(r"(?<![\w-])ADR-(\d+)(?![\w-])", re.I)       # ids inside a value
H1_ID_RE = re.compile(r"ADR-[\w{}]+\s*(?:[:·—–-]\s*)?", re.I)        # "ADR-NNN: " heading prefix
DATE_PLACEHOLDER_RE = re.compile(r"Y{2,4}-M{1,2}-D{1,2}", re.I)

# Last line of the banner: the commit the page was generated from, the id of the banner
# wording/layout, the file or folder the page belongs to, and (ADR and folder pages) a
# fingerprint of the content that does not come from the file: links, folder listing, index.
MARKER_RE = re.compile(r"sync-ref:\s*([0-9a-f]{40})\s+banner:\s*([0-9a-f]{8})\s+page:\s*([0-9a-f]{12})"
                       r"(?:\s+gen:\s*([0-9a-f]{8}))?")
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


# --------------------------------------------------------------------------- logging
def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"::warning::{msg}" if IN_ACTIONS else f"WARNING: {msg}", flush=True)


def error(msg: str) -> None:
    print(f"::error::{msg}" if IN_ACTIONS else f"ERROR: {msg}", flush=True)


def short_hash(text: str, size: int) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:size]


def page_key(repo: str, path: str) -> str:
    """Id written in the marker of the page of `path` (a file, or a folder ending with /)."""
    return short_hash(f"{repo}\n{path}", 12)


# --------------------------------------------------------------------------- git
class GitError(RuntimeError):
    pass


class ConfigError(Exception):
    pass


def git(repo: str, *args: str, binary: bool = False) -> Any:
    res = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=off", *args], capture_output=True)
    if res.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {res.stderr.decode('utf-8', 'replace').strip()}")
    return res.stdout if binary else res.stdout.decode("utf-8", "replace")


def git_ok(repo: str, *args: str) -> bool:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True).returncode == 0


def glob_re(pattern: str) -> re.Pattern[str]:
    """Glob -> regex where `**/` spans directories and `*` stays inside one."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


# --------------------------------------------------------------------------- front matter
def decode(content: bytes) -> str:
    return content.decode("utf-8", "replace").replace("\r\n", "\n")


def split_front_matter(md: str) -> tuple[str | None, str]:
    """(YAML text between the --- lines, or None when there is no front matter; the Markdown after it)."""
    if md.startswith("---\n"):
        end = re.search(r"^(?:---|\.\.\.)[ \t]*$", md[4:], re.M)
        if end:
            return md[4:4 + end.start()], md[4 + end.end():].lstrip("\n")
    return None, md


@functools.lru_cache(maxsize=1)
def front_matter_loader() -> type:
    if yaml is None:
        raise ConfigError("reading the front matter of ADRs needs PyYAML: pip install pyyaml")

    class Loader(yaml.SafeLoader):
        """SafeLoader without aliases (*name): a few nested aliases expand to gigabytes when rendered.
        Values are kept as written: dates stay text (checked by the script, so 2026-02-30 is reported,
        not a crash) and only true/false are booleans (YAML 1.1 also reads yes/no/on/off as booleans,
        which would turn a "no" jurisdiction code into false)."""

        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.AliasEvent):
                raise yaml.composer.ComposerError(None, None, "aliases (*name) are not allowed",
                                                  self.peek_event().start_mark)
            return super().compose_node(parent, index)

    dropped = ("tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool")
    Loader.yaml_implicit_resolvers = {
        first: [(tag, rx) for tag, rx in resolvers if tag not in dropped]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()}
    Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                                 list("tTfF"))
    return Loader


@dataclass
class FrontMatter:
    text: str | None   # YAML between the --- lines, None when the file has no front matter
    data: dict | None  # the parsed mapping, None when absent or unreadable
    error: str         # why it could not be read
    body: str          # the Markdown after it


def read_front_matter(md: str) -> FrontMatter:
    text, body = split_front_matter(md)
    if text is None:
        return FrontMatter(None, None, "", body)
    if len(text) > MAX_FRONT_MATTER:
        return FrontMatter(text, None, f"front matter is larger than {MAX_FRONT_MATTER} bytes", body)
    loader = front_matter_loader()
    try:
        data = yaml.load(text, Loader=loader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 2}" if mark else ""
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        return FrontMatter(text, None, f"front matter is not valid YAML{where}: {problem}", body)
    except RecursionError:
        return FrontMatter(text, None, "front matter is nested too deeply", body)
    except (ValueError, TypeError, OverflowError) as exc:  # a value the loader cannot build
        return FrontMatter(text, None, f"front matter has a value that cannot be read: {exc}", body)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return FrontMatter(text, None, f"front matter is a YAML {type(data).__name__}, not a mapping of keys", body)
    return FrontMatter(text, {str(k): v for k, v in data.items()}, "", body)


def text_of(value: Any) -> str:
    """A front-matter value as one line of text: lists joined with ", ", dates in ISO format."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return ", ".join(t for t in (text_of(v) for v in value) if t)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {text_of(v)}" for k, v in value.items())
    return re.sub(r"\s+", " ", str(value)).strip()


def items_of(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [t for t in (text_of(v) for v in value) if t]
    text = text_of(value)
    return [text] if text else []


def adr_number(text: str) -> int | None:
    m = ADR_ID_RE.fullmatch(text.strip())
    return int(m.group(1)) if m else None


@dataclass
class AdrInfo:
    fields: dict      # front matter, in file order
    raw_id: str       # id as written ("" when absent)
    id: str | None    # "ADR-002" when assigned
    raw_date: str
    date: str | None  # "2026-10-15" when set
    heading: str      # H1 without its "ADR-NNN:" prefix ("" when there is none)
    error: str        # front matter that could not be read


def first_heading(md: str) -> str:
    fence = None
    for line in md.splitlines():
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            h = re.match(r"^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", line)
            if h:
                return re.sub(r"\s+", " ", h.group(1).replace("`", "")).strip()
    return ""


def adr_info(fm: FrontMatter) -> AdrInfo:
    fields = fm.data or {}
    raw_id = text_of(fields.get("id"))
    m = ADR_ID_RE.fullmatch(raw_id)
    adr_id = f"ADR-{m.group(1)}" if m else None
    value = fields.get("date")
    if isinstance(value, dt.datetime):
        date: str | None = value.date().isoformat()
    elif isinstance(value, dt.date):
        date = value.isoformat()
    else:
        date = None
        raw = text_of(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            with contextlib.suppress(ValueError):
                date = dt.date.fromisoformat(raw).isoformat()
    heading = first_heading(fm.body)
    prefix = H1_ID_RE.match(heading)
    if prefix:
        heading = heading[prefix.end():].strip()
    return AdrInfo(fields, raw_id, adr_id, text_of(value), date, heading, fm.error)


def id_problem(raw: str) -> str:
    if not raw:
        return "no id in the front matter"
    if re.fullmatch(r"ADR-[^0-9]*", raw, re.I):
        return f"id is the placeholder {raw}"
    return f"id {raw!r} is not ADR-<number>"


def date_problem(raw: str) -> str:
    if not raw:
        return "no date in the front matter"
    if DATE_PLACEHOLDER_RE.fullmatch(raw):
        return f"date is the placeholder {raw}"
    return f"date {raw!r} is not a YYYY-MM-DD date"


# --------------------------------------------------------------------------- plan
@dataclass
class Version:
    commit: str   # full sha of the commit this content comes from
    path: str     # path of the file in that commit (renames are followed)
    label: str    # tag name, "main@abc1234" or short sha
    date: str     # ISO 8601 committer date
    author: str
    subject: str
    content: bytes = b""
    _fm: FrontMatter | None = field(default=None, repr=False, compare=False)
    _adr: AdrInfo | None = field(default=None, repr=False, compare=False)

    def fm(self) -> FrontMatter:
        if self._fm is None:
            self._fm = read_front_matter(decode(self.content))
        return self._fm

    def adr(self) -> AdrInfo:
        if self._adr is None:
            self._adr = adr_info(self.fm())
        return self._adr

    def ts(self) -> float:
        try:
            return dt.datetime.fromisoformat(self.date).timestamp()
        except ValueError:
            return 0.0


@dataclass
class PageRead:
    res: ToolResult
    token: str | None           # v2 snapshot token
    marker: re.Match[str] | None
    status: str                 # "current", "trashed", "archived"... ("" when not given)
    title: str | None
    parent: str | None


@dataclass
class Page:
    """A page the sync writes, and what this run knows about it."""
    key: str                     # state-file key: "<repo>:<path>" or "<repo>:<folder>/"
    ident: str                   # page id written in its marker
    idents: frozenset[str]       # marker page ids that make an existing page this one's (renames)
    page_id: str | None = None
    title: str | None = None     # title it has in Confluence, when known
    parent: str | None = None    # its parent page id, when known
    read: PageRead | None = None  # read in this run
    owned: bool = False          # found through the state file
    created: bool = False        # created in this run
    gen: str | None = None       # gen of the body written when it was created in this run
    renamed_from: str = ""       # earlier path of the file the page was found under
    old_key: str = ""            # state-file key of that earlier path
    adopt: bool = False          # --adopt: take over this page without banner
    skip: bool = False           # left alone (archived, or a page without banner in the way)
    in_way: str | None = None    # id of that page without banner


@dataclass
class Doc:
    path: str
    versions: list[Version]
    old_paths: list[str] = field(default_factory=list)  # earlier paths of the file, newest first
    kind: str = "file"            # file | adr | template
    adr_root: str | None = None   # the ADR folder this ADR is under
    page: Page | None = None

    @property
    def folder(self) -> str:
        return posixpath.dirname(self.path)

    @property
    def adr(self) -> AdrInfo:
        return self.versions[-1].adr()


@dataclass
class Folder:
    path: str                     # "" for the repository root
    parent: str | None            # path of the parent folder, None for the root
    adr: bool = False             # an ADR folder: its page holds the ADR index
    templates: bool = False       # a templates folder, or a folder below one: its files are templates
    subfolders: list[str] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)
    page: Page | None = None

    @property
    def label(self) -> str:
        return f"{self.path}/" if self.path else "(root)"


@dataclass
class RepoPlan:
    local: str
    gh: str | None        # "org/name" when the origin remote is on GitHub
    name: str
    ref: str              # resolved ref, e.g. origin/main
    branch: str
    mode: str = "tags"
    tip: str = "0" * 40   # commit of the ref
    docs: list[Doc] = field(default_factory=list)
    folders: dict[str, Folder] = field(default_factory=dict)  # top-down, root first
    notes: list[str] = field(default_factory=list)            # placeholder warnings

    @property
    def repo(self) -> str:
        return self.gh or self.name


def detect_github_slug(repo: str) -> str | None:
    try:  # raw URL: `git remote get-url` would apply url.*.insteadOf rewrites (mirrors)
        url = git(repo, "config", "--get", "remote.origin.url").strip()
    except GitError:
        return None
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def resolve_ref(repo: str, branch: str | None) -> tuple[str, str]:
    candidates: list[str] = []
    if branch:
        candidates = [f"origin/{branch}", branch]
    else:
        with contextlib.suppress(GitError):
            candidates.append(git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip())
        candidates.append("HEAD")
    for ref in candidates:
        if git_ok(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            name = ref.split("/", 1)[1] if ref.startswith("origin/") else ref
            if name == "HEAD":
                with contextlib.suppress(GitError):
                    name = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
            return ref, name
    raise GitError(f"{repo}: cannot resolve branch {branch or '(default)'}")


def list_docs(repo: str, ref: str, include: list[str], exclude: list[str]) -> list[str]:
    """Files at `ref` that an include glob matches and no exclude glob does. A glob matches the path
    as it is or with its extension in lower case, so *.md also matches TEMPLATE-ADR.MD."""
    files = git(repo, "ls-tree", "-r", "--name-only", "-z", ref).split("\0")
    inc = [glob_re(g) for g in include]
    exc = [glob_re(g) for g in exclude]

    def hit(globs: list[re.Pattern[str]], f: str) -> bool:
        stem, ext = posixpath.splitext(f)
        return any(r.fullmatch(f) or r.fullmatch(stem + ext.lower()) for r in globs)

    return sorted(f for f in files if f and hit(inc, f) and not hit(exc, f))


def file_history(repo: str, ref: str, path: str) -> list[Version]:
    """Commits that changed `path`, following renames, along the first-parent chain of
    `ref` (one entry per merged PR when you merge with merge commits). Oldest first."""
    out = git(repo, "log", "--first-parent", "--follow", "--name-status",
              "--format=%x1e%H%x1f%cI%x1f%an%x1f%s", ref, "--", path)
    versions: list[Version] = []
    for record in out.split("\x1e")[1:]:
        lines = [ln for ln in record.splitlines() if ln.strip()]
        sha, date, author, subject = lines[0].split("\x1f", 3)
        path_at = None
        for ln in lines[1:]:
            parts = ln.split("\t")
            status = parts[0]
            if status.startswith(("R", "C")) and len(parts) >= 3:
                path_at = parts[2]
            elif status != "D" and len(parts) >= 2:
                path_at = parts[1]
        if path_at:
            versions.append(Version(sha, path_at, sha[:7], date, author, subject))
    versions.reverse()
    return versions


def tag_points(repo: str, ref: str, pattern: str) -> list[tuple[str, str, int, str, str, str]]:
    """Tags reachable from `ref` (hotfix tags on other branches are left out),
    ordered by the date of the tagged commit: (tag, commit, ts, iso, author, subject)."""
    fmt = "%09".join([
        "%(refname:short)", "%(objectname)", "%(*objectname)",
        "%(committerdate:unix)", "%(*committerdate:unix)",
        "%(committerdate:iso-strict)", "%(*committerdate:iso-strict)",
        "%(authorname)", "%(*authorname)", "%(subject)", "%(objecttype)", "%(*objecttype)",
    ])
    out = git(repo, "for-each-ref", "--merged", ref, f"--format={fmt}", f"refs/tags/{pattern}")
    points = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        name, obj, peeled, ts, pts, iso, piso, author, pauthor, subject, typ, ptyp = cols[:12]
        commit = peeled or obj
        if (ptyp or typ) != "commit":
            continue
        points.append((name, commit, int(pts or ts or 0), piso or iso, pauthor or author, subject))
    points.sort(key=lambda p: (p[2], p[0]))
    return points


def blob_at(repo: str, commit: str, path: str) -> bytes | None:
    try:
        return git(repo, "show", f"{commit}:{path}", binary=True)
    except GitError:
        return None


def is_ancestor(repo: str, a: str, b: str) -> bool:
    return git_ok(repo, "merge-base", "--is-ancestor", a, b)


def commit_exists(repo: str, sha: str) -> bool:
    return git_ok(repo, "cat-file", "-e", f"{sha}^{{commit}}")


def require_complete_clone(repo: str) -> None:
    """Every version is read from the local clone, so it must hold all commits and all file
    versions. A shallow clone (--depth) has no old commits. A partial clone (--filter) fetches old
    file versions from its remote on demand; when that fetch fails (no network, an expired token)
    those versions read as absent and would be skipped silently, and a page created without them
    never gets them back (later runs only add versions newer than its sync-ref marker)."""
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise GitError(f"{repo} is a shallow clone (--depth): the sync needs the whole history. "
                       f"Clone without --depth, or run: git -C {shlex.quote(repo)} fetch --unshallow")
    try:
        keys = git(repo, "config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$")
    except GitError:  # exit status 1: no such key, not a partial clone
        keys = ""
    if any(line.split(None, 1)[-1].strip().lower() not in ("false", "no", "off", "0")
           for line in keys.splitlines() if line.strip()):
        raise GitError(f"{repo} is a partial clone (--filter): its old file versions are not stored "
                       f"locally. Clone it without --filter")


def adr_root_of(path: str, adr_folders: list[str]) -> str | None:
    """The nearest folder above `path` named like an ADR folder."""
    parts = path.split("/")[:-1]
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in adr_folders:
            return "/".join(parts[: i + 1])
    return None


def classify(doc: Doc, args: argparse.Namespace) -> None:
    """A file under a templates folder is a template; under an ADR folder, a file whose front matter
    has an id or status key is an ADR; anything else is a plain file."""
    if any(part in args.templates_folder for part in doc.path.split("/")[:-1]):
        doc.kind = "template"
        return
    doc.adr_root = adr_root_of(doc.path, args.adr_folder)
    if doc.adr_root is None:
        return
    fm = doc.versions[-1].fm()
    if fm.error or (fm.data is not None and ("id" in fm.data or "status" in fm.data)):
        doc.kind = "adr"


def folder_chain(folder: str) -> list[str]:
    """The folder and the folders above it, root first: "a/b" -> ["", "a", "a/b"]."""
    parts = folder.split("/") if folder else []
    return [""] + ["/".join(parts[: i + 1]) for i in range(len(parts))]


def adr_notes(plan: RepoPlan) -> list[str]:
    """Warnings for ADRs whose id or date is a placeholder, and for ids used twice."""
    notes, by_number = [], {}
    for doc in plan.docs:
        if doc.kind != "adr":
            continue
        info = doc.adr
        if info.error:
            notes.append(f"{plan.name}:{doc.path}: {info.error}: the page is titled by its file name "
                         f"and the index lists it as \"ID not assigned\"")
            continue
        problems = []
        if not info.id:
            problems.append(f"{id_problem(info.raw_id)}: the page is titled by its file name and the index "
                            f"lists it as \"ID not assigned\"")
        else:
            by_number.setdefault(adr_number(info.id), []).append(doc)
        if not info.date:
            problems.append(f"{date_problem(info.raw_date)}: the index shows \"not set\"")
        if problems:
            notes.append(f"{plan.name}:{doc.path}: " + "; ".join(problems))
    for docs in by_number.values():
        if len(docs) > 1:
            notes.append(f"{plan.name}: {docs[0].adr.id} is the id of {len(docs)} ADRs "
                         f"({', '.join(d.path for d in docs)}); links to it are left out")
    return notes


def build_plan(local: str, args: argparse.Namespace, gh_override: str | None) -> RepoPlan:
    require_complete_clone(local)
    ref, branch = resolve_ref(local, args.branch)
    gh = gh_override or detect_github_slug(local)
    name = (gh or os.path.basename(os.path.abspath(local))).split("/")[-1]
    tip = git(local, "rev-parse", ref).strip()
    plan = RepoPlan(local, gh, name, ref, branch, args.mode, tip)
    tags = tag_points(local, ref, args.tag_pattern) if args.mode == "tags" else []

    for path in list_docs(local, ref, args.include, args.exclude):
        history = file_history(local, ref, path)
        if args.mode == "commits":
            candidates = history
        else:
            # Path of the doc may differ in older tags (renames): try known historical paths.
            known_paths = list(dict.fromkeys([path] + [v.path for v in reversed(history)]))
            candidates = []
            for tag, commit, _ts, iso, author, subject in tags:
                for p in known_paths:
                    if git_ok(local, "cat-file", "-e", f"{commit}:{p}"):
                        candidates.append(Version(commit, p, tag, iso, author, subject))
                        break
            if args.include_head and history and (not tags or tags[-1][1] != tip):
                h = history[-1]
                candidates.append(Version(tip, path, f"{branch}@{tip[:7]}", h.date, h.author, h.subject))
        versions: list[Version] = []
        for v in candidates:
            content = blob_at(local, v.commit, v.path)
            if content is None or (versions and content == versions[-1].content):
                continue  # deleted at that point, or unchanged: no new Confluence version
            v.content = content
            versions.append(v)
        if args.max_versions and len(versions) > args.max_versions:
            versions = versions[-args.max_versions:]
        if not versions:
            continue
        old_paths = list(dict.fromkeys(p for p in [*(v.path for v in reversed(history)),
                                                   *(v.path for v in reversed(versions))] if p != path))
        doc = Doc(path, versions, old_paths)
        classify(doc, args)
        idents = frozenset(page_key(plan.repo, p) for p in [path, *old_paths])
        doc.page = Page(f"{plan.repo}:{path}", page_key(plan.repo, path), idents)
        plan.docs.append(doc)

    folders = {"": Folder("", None)}
    for doc in plan.docs:
        chain = folder_chain(doc.folder)
        for parent, path in zip(chain, chain[1:], strict=False):
            if path not in folders:
                folders[path] = Folder(path, parent, adr=posixpath.basename(path) in args.adr_folder,
                                       templates=any(part in args.templates_folder for part in path.split("/")))
                folders[parent].subfolders.append(path)
        folders[doc.folder].docs.append(doc)
    for f in folders.values():
        f.subfolders.sort()
        ident = page_key(plan.repo, f"{f.path}/")
        f.page = Page(f"{plan.repo}:{f.path}/", ident, frozenset([ident]))
    plan.folders = {p: folders[p] for p in sorted(folders, key=lambda p: (p.count("/") + bool(p), p))}
    plan.notes = adr_notes(plan)
    return plan


# --------------------------------------------------------------------------- titles
# Confluence titles are unique per space. Root and folder titles carry the repository name; a
# file page whose title is taken in the space gets its folder in brackets (fallback title).
def clip(text: str, suffix: str = "") -> str:
    """A page title: whitespace collapsed, cut to Confluence's 255 characters, suffix kept."""
    text = re.sub(r"\s+", " ", text).strip()
    room = MAX_TITLE - len(suffix)
    if len(text) > room:
        text = text[: max(room - 1, 1)].rstrip() + "…"
    return text + suffix


def root_title(repo_name: str) -> str:
    return f"{repo_name} — knowledge base"


def folder_title(repo_name: str, folder: str) -> str:
    return f"{repo_name} · {folder}"


def file_stem(path: str) -> str:
    """The file name without its .md extension."""
    name = posixpath.basename(path)
    stem, ext = posixpath.splitext(name)
    return stem if stem and ext.lower() in (".md", ".markdown") else name


def file_context(plan: RepoPlan, path: str) -> str:
    folder = posixpath.dirname(path)
    return f"{plan.name}/{folder}" if folder else plan.name


def doc_base(doc: Doc, v: Version, path: str) -> str:
    """Page titles option A: an ADR whose id is assigned is titled "<id> - <H1 title>" (ADR_TITLE),
    every other file (and an ADR whose id is still a placeholder) by its file name."""
    stem = file_stem(path)
    if doc.kind == "adr" and v.fm().text is not None:
        info = v.adr()
        if info.id:
            return ADR_TITLE.format(id=info.id, title=info.heading or stem)
    return stem


def doc_titles(plan: RepoPlan, doc: Doc) -> tuple[str, str]:
    """(title, fallback title when the title is taken in the space)."""
    base = doc_base(doc, doc.versions[-1], doc.path)
    return clip(base), clip(base, f" ({file_context(plan, doc.path)})")


def folder_titles(plan: RepoPlan, folder: Folder) -> tuple[str, str]:
    base = folder_title(plan.name, folder.path) if folder.path else root_title(plan.name)
    return clip(base), clip(base, f" ({plan.repo})")


def titles_of(plan: RepoPlan, target: Doc | Folder) -> tuple[str, str]:
    return doc_titles(plan, target) if isinstance(target, Doc) else folder_titles(plan, target)


def title_candidates(plan: RepoPlan, target: Doc | Folder) -> list[str]:
    """Titles the page may have, current first: without the state file, a page is found by title
    and recognized by its marker. A file's title changes when its ADR id or H1 changes, or when
    it is renamed."""
    out = list(titles_of(plan, target))
    if isinstance(target, Doc):
        for v in reversed(target.versions):
            base = doc_base(target, v, v.path)
            out += [clip(base), clip(base, f" ({file_context(plan, v.path)})")]
        for old in target.old_paths:
            out += [clip(file_stem(old)), clip(file_stem(old), f" ({file_context(plan, old)})")]
    return list(dict.fromkeys(out))[:MAX_CANDIDATES]


def targets_in_order(plan: RepoPlan) -> list[Doc | Folder]:
    """Folders top-down, then files by depth: when two files have the same name, the shallower one
    is created first and keeps the plain title (README.md before docs/README.md)."""
    return [*plan.folders.values(), *sorted(plan.docs, key=lambda d: (d.path.count("/"), d.path))]


def creation_order(plan: RepoPlan, targets: list[Doc | Folder]) -> list[Doc | Folder]:
    """Missing pages in the order they are created: folders top-down, then files, except that an
    ADR whose first version links to another new ADR comes after it, so its first version has the
    link (no extra "Links updated" version). ADRs that link to each other keep their order."""
    missing = [t for t in targets if not t.page.page_id and not t.page.skip]
    new_adrs: dict[int, list[Doc]] = {}
    for t in missing:
        if isinstance(t, Doc) and t.kind == "adr" and t.adr.id:
            new_adrs.setdefault(adr_number(t.adr.id), []).append(t)
    order: list[Doc | Folder] = []
    done: set[str] = set()
    rest = missing
    while rest:
        later = []
        for t in rest:
            refs = adr_refs(t, t.versions[0]) if isinstance(t, Doc) else []
            deps = [d for ref in refs for d in new_adrs.get(adr_number(ref), []) if d is not t]
            if any(d.page.key not in done for d in deps):
                later.append(t)
            else:
                order.append(t)
                done.add(t.page.key)
        if len(later) == len(rest):  # ADRs that link to each other
            return order + later
        rest = later
    return order


def planned_titles(plan: RepoPlan) -> dict[str, str]:
    """Titles a first sync gives the pages when no page outside the repository holds them."""
    used, out = set(), {}
    for target in targets_in_order(plan):
        title, fallback = titles_of(plan, target)
        out[target.page.key] = fallback if title.lower() in used else title
        used.add(out[target.page.key].lower())
    return out


def label_of(target: Doc | Folder) -> str:
    return target.path if isinstance(target, Doc) else f"folder {target.label}"


# --------------------------------------------------------------------------- rendering
LINK_RE = re.compile(r'(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]+)>?((?:\s+"[^"]*")?)\s*\)')
REFDEF_RE = re.compile(r'^(\s{0,3}\[[^\]]+\]:\s*)<?(\S+?)>?(\s.*)?$')
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def absolutize(target: str, image: bool, gh: str, sha: str, path: str) -> str | None:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith(("#", "//")):
        return None  # absolute URL, mailto:, anchor
    target_path, _, frag = target.partition("#")
    target_path = target_path.split("?", 1)[0]
    if not target_path:
        return None
    if target_path.startswith("/"):
        resolved = target_path.lstrip("/")
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(target_path)))
    if resolved == ".." or resolved.startswith("../"):
        return None
    kind = "raw" if image else "blob"
    url = f"https://github.com/{gh}/{kind}/{sha}/{quote(resolved, safe='/')}"
    return url + (f"#{frag}" if frag else "")


def rewrite_relative_links(md: str, gh: str | None, sha: str, path: str) -> str:
    """Relative links/images would break in Confluence: pin them to the exact commit on GitHub."""
    if not gh:
        return md
    out, fence = [], None
    for line in md.splitlines(keepends=True):
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            out.append(line)
            continue
        if fence is not None:
            out.append(line)
            continue

        def fix(mm: re.Match[str]) -> str:
            url = absolutize(mm.group(3), bool(mm.group(1)), gh, sha, path)
            return mm.group(0) if url is None else f"{mm.group(1)}[{mm.group(2)}]({url}{mm.group(4)})"

        line = LINK_RE.sub(fix, line)
        rd = REFDEF_RE.match(line.rstrip("\r\n"))
        if rd:
            url = absolutize(rd.group(2), False, gh, sha, path)
            if url:
                line = f"{rd.group(1)}{url}{rd.group(3) or ''}" + ("\n" if line.endswith("\n") else "")
        out.append(line)
    return "".join(out)


def quote_block(lines: list[str]) -> str:
    """Markdown blockquote: adjacent lines get hard line breaks, empty lines split paragraphs."""
    out = []
    for i, line in enumerate(lines):
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        out.append(f"> {line}" + ("  " if next_line else "") if line else ">")
    return "\n".join(out) + "\n\n"


def banner_lines(v: Version, plan: RepoPlan, current_path: str) -> list[str]:
    """BANNER_TEXT, then the exact source of this version: repository, path and commit SHA.
    The PR target is the file's current path on the synced branch (it may have been renamed
    since `v`); the source link points at the file as it was in the commit of `v`."""
    repo = plan.repo
    target = f"`{repo}/{current_path}`"
    if plan.gh:
        gh = f"https://github.com/{plan.gh}"
        target = f"[{target}]({gh}/edit/{quote(plan.branch, safe='/')}/{quote(current_path, safe='/')})"
        source = (f"[{plan.gh}]({gh}) › "
                  f"[{v.path}]({gh}/blob/{v.commit}/{quote(v.path, safe='/')}) › "
                  f"commit [`{v.commit[:7]}`]({gh}/commit/{v.commit})")
    else:
        source = f"{repo} › {v.path} › commit `{v.commit[:7]}`"
    tag = "" if v.label == v.commit[:7] else f" · {v.label}"
    return [*BANNER_TEXT.replace("{repo_path}", target).split("\n"), "",
            f"📄 Source: {source}{tag} · {v.date[:10]} · {v.author}"]


def folder_banner_lines(plan: RepoPlan, folder: Folder, v: Version | None) -> list[str]:
    """BANNER_TEXT with the folder as PR target, then the commit of the newest file version in the
    folder when the page was written (the listing and the ADR index reflect the files as of then)."""
    repo = plan.repo
    target = f"`{repo}/{folder.path}/`" if folder.path else f"`{repo}`"
    gh = f"https://github.com/{plan.gh}" if plan.gh else ""
    if plan.gh:
        tree = f"{gh}/tree/{quote(plan.branch, safe='/')}" + (f"/{quote(folder.path, safe='/')}" if folder.path else "")
        target = f"[{target}]({tree})"
    lines = [*BANNER_TEXT.replace("{repo_path}", target).split("\n"), ""]
    if v is None:
        return [*lines, f"📄 Source: {repo} › no files yet"]
    if plan.gh:
        where = f" › [{folder.path}/]({gh}/tree/{v.commit}/{quote(folder.path, safe='/')})" if folder.path else ""
        source = f"[{plan.gh}]({gh}){where} › commit [`{v.commit[:7]}`]({gh}/commit/{v.commit})"
    else:
        source = repo + (f" › {folder.path}/" if folder.path else "") + f" › commit `{v.commit[:7]}`"
    tag = "" if v.label == v.commit[:7] else f" · {v.label}"
    return [*lines, f"📄 Source: {source}{tag} · {v.date[:10]} · {v.author}"]


@functools.lru_cache(maxsize=1)
def banner_id() -> str:
    """Fingerprint of the banner wording and layouts, independent of the page."""
    sample = Version("0" * 40, "p.md", "v0", "2000-01-01", "a", "s")
    text = ""
    for gh in ("o/r", None):
        plan = RepoPlan(".", gh, "r", "main", "main")
        text += quote_block(banner_lines(sample, plan, "p.md"))
        for folder in (Folder("", None), Folder("d", "")):
            text += quote_block(folder_banner_lines(plan, folder, sample))
    return short_hash(text, 8)


def marker_text(sha: str, ident: str, gen: str | None) -> str:
    return f"`sync-ref: {sha} banner: {banner_id()} page: {ident}" + (f" gen: {gen}`" if gen else "`")


def banner_ok(m: re.Match[str] | None) -> bool:
    """True when the page shows the marker line of the current banner."""
    return bool(m) and m.group(2) == banner_id()


def version_message(v: Version, prefix: str = "") -> str:
    parts = [v.label] if v.label == v.commit[:7] else [v.label, v.commit[:7]]
    msg = prefix + " · ".join([*parts, v.date[:10], v.author, v.subject])
    return msg if len(msg) <= MAX_VERSION_MESSAGE else msg[: MAX_VERSION_MESSAGE - 1] + "…"


def folder_message(folder: Folder, newest: Version | None, prefix: str = "") -> str:
    what = "ADR index" if folder.adr else "Folder contents"
    return version_message(newest, f"{prefix}{what} · ") if newest else f"{prefix}{what}"


def esc(text: str) -> str:
    """Text for a Markdown table cell or list item: one line, table separators escaped."""
    return re.sub(r"\s+", " ", text).replace("\\", "\\\\").replace("|", "\\|").replace("<", "\\<")


def cell(text: str) -> str:
    return esc(text).strip()


def link(text: str, url: str | None) -> str:
    if not url:
        return cell(text)
    label = cell(text).replace("[", "\\[").replace("]", "\\]")
    return f"[{label}]({url})"


def yaml_block(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}yaml\n---\n{text.rstrip()}\n---\n{fence}\n\n"


class Links:
    """Confluence page URLs: {site}/wiki/spaces/{space}/pages/{id}. Without the site URL, titles
    and ids are shown without links."""

    def __init__(self, site: str | None, space_key: str, plan: RepoPlan):
        self.base = f"{site}/wiki/spaces/{quote(space_key, safe='~')}/pages/" if site else None
        self.by_number: dict[int, list[Doc]] = {}
        for doc in plan.docs:
            if doc.kind == "adr" and doc.adr.id:
                self.by_number.setdefault(adr_number(doc.adr.id), []).append(doc)

    def page(self, page_id: str | None) -> str | None:
        return f"{self.base}{page_id}" if self.base and page_id else None

    def adr(self, ref: str) -> str | None:
        number = adr_number(ref)
        docs = self.by_number.get(number, []) if number is not None else []
        return self.page(docs[0].page.page_id) if len(docs) == 1 else None


def link_ids(text: str, links: Links) -> str:
    """Text with every ADR id in it linked to the page of that ADR."""
    out, pos = [], 0
    for m in ADR_REF_RE.finditer(text):
        out += [esc(text[pos:m.start()]), link(m.group(0), links.adr(m.group(0)))]
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out).strip()


def adr_refs(doc: Doc, v: Version) -> list[str]:
    """ADR ids that version `v` of an ADR names in supersedes / superseded-by / related."""
    if doc.kind != "adr" or v.fm().text is None:
        return []
    fields = v.adr().fields
    return [m.group(0) for key in ADR_REF_KEYS for item in items_of(fields.get(key)) for m in ADR_REF_RE.finditer(item)]


def doc_gen(doc: Doc, v: Version, links: Links) -> str | None:
    """Fingerprint of what an ADR page shows that does not come from its file: where its ADR ids
    link to. A change (an id assigned, a page created) makes the next sync update the page."""
    if doc.kind != "adr":
        return None
    targets = sorted({f"{adr_number(ref)}={links.adr(ref) or ''}" for ref in adr_refs(doc, v)})
    return short_hash("\n".join(targets), 8)


def value_cell(value: Any) -> str:
    text = text_of(value)
    return link(text, text) if re.fullmatch(r"https?://\S+", text) else cell(text)


def metadata_block(v: Version, links: Links) -> str:
    """The front matter of an ADR version as a table, ADR ids linked to their pages, and a warning
    next to an id or date that is not set."""
    fm, info = v.fm(), v.adr()
    if fm.error:
        return f"⚠️ The front matter of this version cannot be read: {cell(fm.error)}\n\n" + yaml_block(fm.text or "")
    id_cell = " ".join(x for x in (cell(info.raw_id), "" if info.id else "⚠️ ID not assigned") if x)
    date_cell = " ".join(x for x in (cell(info.raw_date), "" if info.date else "⚠️ date not set") if x)
    rows = []
    if "id" not in info.fields:
        rows.append(f"| id | {id_cell} |")
    if "date" not in info.fields:
        rows.append(f"| date | {date_cell} |")
    for key, value in info.fields.items():
        if key == "id":
            text = id_cell
        elif key == "date":
            text = date_cell
        elif key in ADR_REF_KEYS:
            text = ", ".join(link_ids(item, links) for item in items_of(value))
        else:
            text = value_cell(value)
        if text:
            rows.append(f"| {cell(key)} | {text} |")
    return "| Field | Value |\n|---|---|\n" + "\n".join(rows) + "\n\n"


def render_doc(v: Version, plan: RepoPlan, doc: Doc, transform: Transform | None, links: Links) -> tuple[str, str | None]:
    """Page body = banner + (ADR) metadata table or (template) YAML front matter + content, and its
    gen. The banner and the metadata are added after the transform, so every body carries them."""
    fm = v.fm()
    md = rewrite_relative_links(fm.body, plan.gh, v.commit, v.path)
    if transform:
        md = transform.run(md, v, plan)
    head = ""
    if doc.kind == "adr" and fm.text is not None:
        head = metadata_block(v, links)
    elif doc.kind == "template" and fm.text is not None:
        head = yaml_block(fm.text)
    gen = doc_gen(doc, v, links)
    lines = [*banner_lines(v, plan, doc.path), marker_text(v.commit, doc.page.ident, gen)]
    return quote_block(lines) + head + md.strip() + "\n", gen


def page_title_now(plan: RepoPlan, target: Doc | Folder) -> str:
    return target.page.title or titles_of(plan, target)[0]


def adr_sort_key(doc: Doc) -> tuple:
    number = adr_number(doc.adr.id) if doc.adr.id else None
    return (0, number, doc.path) if number is not None else (1, 0, doc.path)


def index_cell(plan: RepoPlan, doc: Doc, source: str, links: Links) -> str:
    info, latest = doc.adr, doc.versions[-1]
    if source == "id":
        return cell(info.id or "ID not assigned")
    if source == "title":
        return link(info.heading or file_stem(doc.path), links.page(doc.page.page_id))
    if source == "date":
        return cell(info.date or "not set")
    if source == "commit":
        label = latest.commit[:7] if latest.label == latest.commit[:7] else latest.label
        url = f"https://github.com/{plan.gh}/commit/{latest.commit}" if plan.gh else None
        return f"{link(label, url)} · {latest.date[:10]}"
    value = info.fields.get(source)
    if source in ADR_REF_KEYS:
        return ", ".join(link_ids(item, links) for item in items_of(value))
    return value_cell(value)


def adr_index(plan: RepoPlan, folder: Folder, links: Links) -> str:
    adrs = sorted((d for d in plan.docs if d.kind == "adr" and d.adr_root == folder.path), key=adr_sort_key)
    where = f"`{folder.path}/`"
    if plan.gh:
        where = f"[{where}](https://github.com/{plan.gh}/tree/{quote(plan.branch, safe='/')}/{quote(folder.path, safe='/')})"
    if not adrs:
        return f"No architecture decision records in {where} yet.\n\n"
    out = [f"Architecture decision records in {where}, generated on each sync from their front matter "
           f"(edit the front matter in git to change this table). Sorted by ID; ADRs without an assigned ID "
           f"come last.\n",
           "| " + " | ".join(h for h, _ in ADR_INDEX_COLUMNS) + " |",
           "|" + "---|" * len(ADR_INDEX_COLUMNS)]
    out += ["| " + " | ".join(index_cell(plan, d, src, links) or " " for _, src in ADR_INDEX_COLUMNS) + " |"
            for d in adrs]
    no_id = sum(1 for d in adrs if not d.adr.id)
    no_date = sum(1 for d in adrs if not d.adr.date)
    if no_id or no_date:
        def count(n: int, what: str) -> str:
            return f"{n} ADR{'s have' if n > 1 else ' has'} {what}"
        parts = ([count(no_id, "no assigned ID")] if no_id else []) + ([count(no_date, "no date")] if no_date else [])
        out.append(f"\n⚠️ {' and '.join(parts)}: set `id` (ADR-<number>) and `date` (YYYY-MM-DD) in the "
                   f"front matter.")
    return "\n".join(out) + "\n\n"


def listed_targets(plan: RepoPlan, folder: Folder) -> list[Doc | Folder]:
    """Pages a folder page links to: its subfolders and files, and the ADRs of its index."""
    adrs = [d for d in plan.docs if folder.adr and d.kind == "adr" and d.adr_root == folder.path]
    docs = {d.path: d for d in [*folder.docs, *adrs]}
    return [*(plan.folders[s] for s in folder.subfolders), *docs.values()]


def folder_listing(plan: RepoPlan, folder: Folder, links: Links) -> list[str]:
    """Pages directly below the folder page: subfolders, then files (ADRs of an ADR folder are in its index)."""
    items = []
    for sub in folder.subfolders:
        f = plan.folders[sub]
        items.append(f"- 📁 {link(page_title_now(plan, f), links.page(f.page.page_id))}")
    for doc in folder.docs:
        if folder.adr and doc.kind == "adr" and doc.adr_root == folder.path:
            continue
        items.append(f"- 📄 {link(page_title_now(plan, doc), links.page(doc.page.page_id))}")
    return items


def render_folder(plan: RepoPlan, folder: Folder, links: Links) -> tuple[str, str, Version | None]:
    """Folder page body: banner, (root) introduction, (ADR folder) ADR index, listing. Returns
    (body, gen: fingerprint of the body without the marker, newest file version in the folder)."""
    prefix = f"{folder.path}/" if folder.path else ""
    newest = max((d.versions[-1] for d in plan.docs if d.path.startswith(prefix)),
                 key=lambda v: (v.ts(), v.commit), default=None)
    content = []
    if not folder.path:
        source = f"[{plan.gh}](https://github.com/{plan.gh})" if plan.gh else f"`{plan.name}`"
        unit = "release tag" if plan.mode == "tags" else f"change merged into `{plan.branch}`"
        content.append(f"This section mirrors the documentation of {source} from git: one page per file, "
                       f"under one page per folder, nested like the repository. The page history of a file "
                       f"page replays the file's git history: one Confluence version per {unit}, with the tag, "
                       f"commit, date and author in the version comment.\n\n")
    if folder.adr:
        content.append(adr_index(plan, folder, links))
    items = folder_listing(plan, folder, links)
    if items:
        intro = ("Other pages in this folder:" if folder.adr else
                 "Pages:" if not folder.path else
                 f"Templates in `{folder.path}/`; each page shows the template's front matter as YAML, then "
                 f"its text:" if folder.templates else f"Pages generated from the files in `{folder.path}/`:")
        content.append(intro + "\n\n" + "\n".join(items) + "\n")
    lines = folder_banner_lines(plan, folder, newest)
    body = "".join(content)
    # the last banner line names the newest commit in the folder: left out of the fingerprint, so
    # a folder page gets a new version when its listing or index changes, not on every commit
    gen = short_hash(quote_block(lines[:-1]) + body, 8)
    sha = newest.commit if newest else plan.tip
    return quote_block([*lines, marker_text(sha, folder.page.ident, gen)]) + body, gen, newest


SYNC_CREDENTIALS = {"ATLASSIAN_SERVICE_KEY", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"}
SECRET_NAME_RE = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE", re.I)


@dataclass
class Transform:
    """--transform-cmd: page markdown on stdin, page markdown on stdout. Runs as an argument
    list without a shell (argv None = --transform-shell), in an empty temporary folder, without
    this script's credentials or secret-looking variables unless named with --transform-env."""
    command: str
    argv: list[str] | None
    passthrough: frozenset[str]

    def env(self, v: Version, plan: RepoPlan) -> dict[str, str]:
        env = {k: val for k, val in os.environ.items() if k in self.passthrough
               or (k not in SYNC_CREDENTIALS and not SECRET_NAME_RE.search(k))}
        env.update(KB_REPO=plan.repo, KB_PATH=v.path, KB_REF=v.label, KB_SHA=v.commit)
        return env

    def run(self, text: str, v: Version, plan: RepoPlan) -> str:
        where = f"--transform-cmd for {v.path}@{v.label}"
        try:
            with tempfile.TemporaryDirectory(prefix="kb-transform-", ignore_cleanup_errors=True) as cwd:
                res = subprocess.run(self.argv if self.argv is not None else self.command,
                                     shell=self.argv is None, input=text.encode(), capture_output=True,
                                     env=self.env(v, plan), cwd=cwd, timeout=900)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{where}: no result after 900 s") from None
        except OSError as exc:
            raise RuntimeError(f"{where}: cannot start: {exc}") from exc
        out = res.stdout.decode("utf-8", "replace").strip()
        if res.returncode != 0 or not out:
            raise RuntimeError(f"{where} failed (exit {res.returncode}): "
                               f"{res.stderr.decode('utf-8', 'replace')[:300]}")
        return out


# --------------------------------------------------------------------------- MCP client
class RovoError(RuntimeError):
    pass


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """MCP SDK 2.x uses snake_case model fields, 1.x camelCase."""
    for n in names:
        val = getattr(obj, n, None)
        if val is not None:
            return val
    return default


@contextlib.asynccontextmanager
async def open_transport(url: str, headers: dict[str, str]):
    try:  # MCP SDK >= 1.2x / 2.x
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
    except ImportError:
        streamable_http_client = None
    if streamable_http_client is not None:
        async with create_mcp_http_client(headers=headers) as http, \
                streamable_http_client(url, http_client=http) as streams:
            yield streams[0], streams[1]
    else:  # older MCP SDK 1.x
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(url, headers=headers) as streams:
            yield streams[0], streams[1]


async def list_tools_page(session: Any, cursor: str | None) -> Any:
    if cursor is None:
        return await session.list_tools()
    try:
        from mcp.types import PaginatedRequestParams
        return await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
    except (ImportError, TypeError):
        return await session.list_tools(cursor=cursor)


def first_error(exc: BaseException) -> str:
    """First real error inside the exception groups that anyio wraps transport errors in."""
    def leaves(e: BaseException) -> list[BaseException]:
        subs = getattr(e, "exceptions", None)
        return [x for s in subs for x in leaves(s)] if subs else [e]
    found = leaves(exc)
    first = next((e for e in found if not isinstance(e, asyncio.CancelledError)), found[0])
    return f"{type(first).__name__}: {first}"


def is_rate_limited(text: str) -> bool:
    return bool(re.search(r"\b429\b|rate.?limit|too many requests|throttl", text, re.I))


@dataclass
class ToolResult:
    text: str              # all text content blocks, joined
    data: Any              # structured content, else the text parsed as JSON
    structured: Any = None  # structured content exactly as returned (None when absent)

    def blob(self) -> str:
        """Everything the tool returned: the text blocks and the structured content. A tool may
        put a short message in the text and the page body in the structured content (or the
        reverse), so the sync-ref marker is searched in both."""
        parts = [self.text] if self.text else []
        if self.structured is not None:
            parts.append(json.dumps(self.structured, ensure_ascii=False))
        return "\n".join(parts)

    def dicts(self) -> list[dict]:
        found, stack = [], [self.data]
        while stack:  # breadth-first, shallow matches come first
            node = stack.pop(0)
            if isinstance(node, dict):
                found.append(node)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return found

    def find_key(self, *keys: str) -> Any:
        for d in self.dicts():
            for k in keys:
                if d.get(k) not in (None, ""):
                    return d[k]
        for k in keys:  # last resort for non-JSON text results
            m = re.search(rf'["\']?{k}["\']?\s*[:=]\s*["\']?([\w\-.:/+=]+)', self.text)
            if m:
                return m.group(1)
        return None

    def page_id_by_title(self, title: str) -> str | None:
        for d in self.dicts():
            if d.get("title") == title and d.get("id") and d.get("type", "page") == "page":
                return str(d["id"])
        return None

    def pages_titled(self, titles: list[str]) -> list[tuple[str, str]]:
        """(id, title) of the pages in a search response whose title is one of `titles`, ignoring
        case as Confluence does for title uniqueness."""
        wanted, found = {t.lower() for t in titles}, []
        for d in self.dicts():
            if (isinstance(d.get("title"), str) and d["title"].lower() in wanted and d.get("id")
                    and d.get("type", "page") == "page"):
                found.append((str(d["id"]), d["title"]))
        return list(dict.fromkeys(found))

    def page_ids(self) -> list[str]:
        """Ids of the pages in a search response, in result order."""
        ids = [str(d["id"]) for d in self.dicts()
               if d.get("id") and isinstance(d.get("title"), str) and d.get("type", "page") == "page"]
        return list(dict.fromkeys(ids))


class RovoMCP:
    def __init__(self, endpoint: str, headers: dict[str, str], calls_per_hour: int):
        self.endpoint, self.headers = endpoint, headers
        self.min_interval = 3600.0 / calls_per_hour if calls_per_hour > 0 else 0.0
        self.retry_base = float(os.environ.get("RTC_RETRY_BASE_SECONDS", "30"))
        self.tools: dict[str, dict] = {}
        self.calls = 0
        self._last = 0.0
        self._stack: contextlib.AsyncExitStack | None = None
        self.session: Any = None

    async def __aenter__(self) -> "RovoMCP":
        await self._connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()

    async def _close(self) -> BaseException | None:
        """Close the transport. Returns what closing raised: a broken transport raises its own error
        (connection refused, credential rejected) when its task group is exited."""
        stack, self._stack = self._stack, None
        if stack is None:
            return None
        try:
            await stack.aclose()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            return exc
        return None

    async def _connect(self) -> None:
        """Open a session and list the tools. On failure the half-open transport is closed here, in
        this task (left to the garbage collector, its task group breaks the shutdown with tracebacks),
        and one RovoError is raised: exit status 2."""
        from mcp import ClientSession
        await self._close()  # reconnecting: whatever the old transport raises on close is moot
        self._stack = contextlib.AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(open_transport(self.endpoint, self.headers))
            self.session = await self._stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            self.tools, cursor = {}, None
            while True:
                page = await list_tools_page(self.session, cursor)
                for t in page.tools:
                    self.tools[t.name] = _attr(t, "input_schema", "inputSchema", default={}) or {}
                cursor = _attr(page, "next_cursor", "nextCursor")
                if not cursor:
                    break
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # incl. the cancellation that a failing transport task sends here
            closing = await self._close()
            if closing is None and isinstance(exc, asyncio.CancelledError):
                raise  # a real cancellation, not a connection failure
            raise RovoError(f"cannot connect to {self.endpoint}: {first_error(closing or exc)}. Check the "
                            f"credential, the endpoint, and that an admin enabled API-token authentication "
                            f"for the Rovo MCP server") from None

    def props(self, tool: str) -> dict:
        return (self.tools.get(tool) or {}).get("properties") or {}

    def pick(self, tool: str, *candidates: str) -> str:
        """Parameter name actually used by the server (casing differs between tools)."""
        props = self.props(tool)
        return next((c for c in candidates if c in props), candidates[0])

    def accepts(self, tool: str, param: str) -> bool:
        props = self.props(tool)
        return param in props if props else True

    def reachable(self, tool: str, write: bool = False) -> bool:
        """Can call() run this tool: exposed directly, or through an execute tool (Atlassian MCP v2)?"""
        executors = ("executeWrite" if write else "executeRead", "execute")
        return bool(tool) and (tool in self.tools or any(t in self.tools for t in executors))

    async def _throttle(self) -> None:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def call(self, name: str, args: dict, *, write: bool = False) -> ToolResult:
        tool, payload = name, args
        if name not in self.tools:
            # Atlassian MCP v2 exposes only "primary" tools directly; the rest are run
            # through executeRead / executeWrite (or a single execute tool).
            prefer = ("executeWrite",) if write else ("executeRead",)
            executor = next((t for t in (*prefer, "execute") if t in self.tools), None)
            if not executor:
                raise RovoError(f"tool {name!r} is not exposed by {self.endpoint} "
                                f"(run --inspect; on v2 try the ?tools=all endpoint)")
            tool = executor
            payload = {"name": name, "cloudId": args.get("cloudId"),
                       "inputs": {k: v for k, v in args.items() if k != "cloudId"}}
        delay, reconnected = self.retry_base, False
        for attempt in range(7):
            await self._throttle()
            try:
                res = await self.session.call_tool(tool, payload)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as caught:  # transport/protocol error
                exc: BaseException = caught
                if isinstance(caught, asyncio.CancelledError):
                    # MCP SDK 2.x: a transport task that fails (connection lost) cancels this task.
                    # Closing the transport here surfaces its own error; nothing surfaces when the
                    # cancellation is real (Ctrl-C), and then it must propagate.
                    closing = await self._close()
                    if closing is None:
                        raise
                    exc = ConnectionError(first_error(closing))
                if is_rate_limited(str(exc)) and attempt < 6:  # refused, so safe to repeat
                    warn(f"rate limited on {name}; sleeping {delay:.0f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 900)
                    await self._connect()
                    continue
                if write:
                    # The write may have reached Confluence before the connection failed:
                    # repeating it could add a duplicate version. Reconnect for the next calls
                    # and fail this page; the next run reads its marker and pushes what is missing.
                    with contextlib.suppress(Exception):
                        await self._connect()
                    raise RovoError(f"{name}: connection lost during a write, outcome unknown, not "
                                    f"repeated ({exc}); the next run reconciles from the page") from exc
                if not reconnected:
                    reconnected = True
                    await self._connect()
                    continue
                raise RovoError(f"{name}: {exc}") from exc
            self.calls += 1
            text = "\n".join(getattr(c, "text", "") or "" for c in (res.content or []))
            if _attr(res, "is_error", "isError", default=False):
                if is_rate_limited(text) and attempt < 6:
                    warn(f"rate limited on {name}; sleeping {delay:.0f}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 900)
                    continue
                raise RovoError(f"{name} failed: {text[:600]}")
            structured = _attr(res, "structured_content", "structuredContent")
            data = structured
            if data is None:
                with contextlib.suppress(ValueError):
                    data = json.loads(text)
            return ToolResult(text, data, structured)
        raise RovoError(f"{name}: still rate limited after retries")


# --------------------------------------------------------------------------- dialects
def cql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cql_for_titles(space_key: str, titles: list[str]) -> str:
    clause = " OR ".join(f"title = {cql_quote(t)}" for t in titles)
    return f"space = {cql_quote(space_key)} AND type = page AND ({clause})"


PARENT_PARAMS = ("parentId", "parent_id", "parentContentId")


class Dialect:
    name = "?"
    needs_snapshot = False
    CREATE = UPDATE = GET = SEARCH = SPACES = ""

    def __init__(self, rovo: RovoMCP, cloud_id: str):
        self.r, self.cid = rovo, cloud_id
        self.space_source = ""  # how the last space_id() call found the id, for the logs

    def can_retitle(self) -> bool:
        """Does the update tool take a title (needed to rename a page when its file is renamed)?"""
        return "title" in self.r.props(self.UPDATE)

    def parent_param(self) -> str | None:
        """Parameter of the update tool that moves a page under another parent, if any."""
        props = self.r.props(self.UPDATE)
        return next((p for p in PARENT_PARAMS if p in props), None)

    async def read(self, page_id: str) -> tuple[ToolResult, str | None]:
        raise NotImplementedError

    async def space_by_tool(self, key: str) -> str | None:
        raise NotImplementedError

    async def find_titled(self, space_key: str, titles: list[str]) -> list[tuple[str, str]]:
        """(id, title) of the pages of the space titled with one of `titles`: one CQL search."""
        param = self.r.pick(self.SEARCH, "cql", "query")
        args: dict[str, Any] = {"cloudId": self.cid, param: cql_for_titles(space_key, titles)}
        if "limit" in self.r.props(self.SEARCH):
            args["limit"] = 50
        return (await self.r.call(self.SEARCH, args)).pages_titled(titles)

    async def find_page(self, space_key: str, title: str) -> str | None:
        """Id of the page titled `title`, ignoring case: an exact CQL title match, else a CQL text search
        on the title (an exact match may miss a title that differs in case), filtered on the whole title."""
        found = await self.find_titled(space_key, [title])
        if not found:
            param = self.r.pick(self.SEARCH, "cql", "query")
            args: dict[str, Any] = {"cloudId": self.cid,
                                    param: f"space = {cql_quote(space_key)} AND type = page AND title ~ {cql_quote(title)}"}
            if "limit" in self.r.props(self.SEARCH):
                args["limit"] = 50
            with contextlib.suppress(RovoError):  # a text query the site rejects: no case variant found
                found = (await self.r.call(self.SEARCH, args)).pages_titled([title])
        exact = [pid for pid, t in found if t == title]
        return (exact or [pid for pid, _ in found] or [None])[0]

    async def space_id(self, key: str) -> str:
        """Id of space `key` for the create tool: from the dialect's spaces tool when the endpoint
        exposes it, else (some sites expose no spaces tool) the spaceId of a page of the space.
        --space-id skips this lookup and --state-file caches its result."""
        note = ""
        if self.r.reachable(self.SPACES):
            try:
                sid = await self.space_by_tool(key)
            except RovoError as exc:
                sid, note = None, f" ({exc})"
            if sid:
                self.space_source = self.SPACES
                return sid
        found = await self.space_from_pages(key)
        if found:
            sid, self.space_source = found
            return sid
        tools = " or ".join(([self.SPACES] if self.r.reachable(self.SPACES) else []) + [self.SEARCH])
        raise RovoError(f"space {key!r} not found with {tools}{note}: check --space-key and that the token can "
                        f"read the space, or pass --space-id (needed for a space with no page to read)")

    async def space_from_pages(self, key: str) -> tuple[str, str] | None:
        """(space id, where it came from) without a spaces tool: search pages of the space with CQL,
        read one and take its spaceId. None when no page of the space can be read."""
        param = self.r.pick(self.SEARCH, "cql", "query")
        args: dict[str, Any] = {"cloudId": self.cid, param: f"space = {cql_quote(key)} AND type = page"}
        if "limit" in self.r.props(self.SEARCH):
            args["limit"] = 5
        res = await self.r.call(self.SEARCH, args)
        for d in res.dicts():  # a response that carries the space object itself
            if str(d.get("key", "")).upper() == key.upper() and str(d.get("id", "")).isdigit():
                return str(d["id"]), f"{self.SEARCH} result"
        for page_id in res.page_ids()[:3]:
            try:
                page, _ = await self.read(page_id)
            except RovoError:
                continue
            sid = page.data.get("spaceId") if isinstance(page.data, dict) else None
            sid = sid if sid is not None else page.find_key("spaceId", "space_id")
            if sid is not None and str(sid).isdigit():
                return str(sid), f"spaceId of page {page_id}"
        return None

    def _created_id(self, res: ToolResult, title: str) -> str:
        pid = res.page_id_by_title(title) or res.find_key("contentId", "id", "pageId")
        if not pid:
            raise RovoError(f"{self.CREATE}: no page id in response: {res.text[:400]}")
        return str(pid)


class V1(Dialect):
    """Atlassian MCP v1 tool set (default on /v1/mcp until 1 March 2027)."""
    name = "v1"
    CREATE, UPDATE, GET, SEARCH = ("createConfluencePage", "updateConfluencePage",
                                   "getConfluencePage", "searchConfluenceUsingCql")
    SPACES = "getConfluenceSpaces"

    def can_retitle(self) -> bool:
        return self.r.accepts(self.UPDATE, "title")  # update() sends the title on the same terms

    async def space_by_tool(self, key: str) -> str | None:
        keys_schema = json.dumps(self.r.props(self.SPACES).get("keys") or {})
        as_list = '"array"' in keys_schema or not keys_schema.strip("{}")
        res = await self.r.call(self.SPACES, {"cloudId": self.cid, "keys": [key] if as_list else key})
        for d in res.dicts():
            if str(d.get("key", "")).upper() == key.upper() and d.get("id"):
                return str(d["id"])
        return None

    async def read(self, page_id: str) -> tuple[ToolResult, str | None]:
        args = {"cloudId": self.cid, "pageId": page_id}
        if "contentFormat" in self.r.props(self.GET):
            args["contentFormat"] = "markdown"
        return await self.r.call(self.GET, args), None

    async def create(self, space_id: str, parent_id: str | None, title: str, body: str, message: str) -> str:
        args: dict[str, Any] = {"cloudId": self.cid, "spaceId": space_id, "title": title, "body": body}
        if parent_id:
            args["parentId"] = parent_id
        if self.r.accepts(self.CREATE, "contentFormat"):
            args["contentFormat"] = "markdown"
        if "versionMessage" in self.r.props(self.CREATE):
            args["versionMessage"] = message
        return self._created_id(await self.r.call(self.CREATE, args, write=True), title)

    async def update(self, page_id: str, title: str, body: str, message: str, token: str | None,
                     parent_id: str | None = None) -> None:
        args: dict[str, Any] = {"cloudId": self.cid, "pageId": page_id, "body": body}
        if self.r.accepts(self.UPDATE, "title"):
            args["title"] = title
        if self.r.accepts(self.UPDATE, "contentFormat"):
            args["contentFormat"] = "markdown"
        if self.r.accepts(self.UPDATE, "versionMessage"):
            args["versionMessage"] = message
        if parent_id and self.parent_param():
            args[self.parent_param()] = parent_id
        await self.r.call(self.UPDATE, args, write=True)


class V2(Dialect):
    """Atlassian MCP v2 tool set (/v2/mcp, and /v1/mcp from 1 March 2027)."""
    name = "v2"
    needs_snapshot = True
    CREATE, UPDATE, GET, SEARCH = ("createConfluenceContent", "updateConfluenceContent",
                                   "getConfluenceContent", "searchConfluence")
    SPACES = "getConfluenceSpace"

    async def space_by_tool(self, key: str) -> str | None:
        res = await self.r.call(self.SPACES, {"cloudId": self.cid, "spaceIdOrKey": key})
        for d in res.dicts():
            if str(d.get("key", "")).upper() == key.upper() and d.get("id"):
                return str(d["id"])
        sid = res.find_key("id", "spaceId")
        return str(sid) if sid else None

    async def read(self, page_id: str) -> tuple[ToolResult, str | None]:
        p = self.r.pick
        args: dict[str, Any] = {"cloudId": self.cid, p(self.GET, "content_id", "contentId", "id"): page_id}
        for names, value in ((("content_format", "contentFormat"), "markdown"),
                             (("detail",), "full"),
                             (("include_metadata", "includeMetadata"), True)):
            key = p(self.GET, *names)
            if self.r.accepts(self.GET, key):
                args[key] = value
        res = await self.r.call(self.GET, args)
        token = res.find_key("snapshotToken", "snapshot_token")
        return res, (str(token) if token else None)

    async def create(self, space_id: str, parent_id: str | None, title: str, body: str, message: str) -> str:
        parent: dict[str, str] = {"spaceId": space_id}
        if parent_id:
            parent["parentContentId"] = parent_id
        args: dict[str, Any] = {"cloudId": self.cid, "parent": parent, "contentType": "page",
                                "title": title, "body": {"format": "markdown", "value": body}}
        if "versionMessage" in self.r.props(self.CREATE):
            args["versionMessage"] = message
        return self._created_id(await self.r.call(self.CREATE, args, write=True), title)

    async def update(self, page_id: str, title: str, body: str, message: str, token: str | None,
                     parent_id: str | None = None) -> None:
        args: dict[str, Any] = {"cloudId": self.cid,
                                self.r.pick(self.UPDATE, "contentId", "content_id", "id"): page_id,
                                "body": {"format": "markdown", "value": body}}
        if token:
            args["snapshotToken"] = token
        if self.r.accepts(self.UPDATE, "versionMessage"):
            args["versionMessage"] = message
        if "title" in self.r.props(self.UPDATE):
            args["title"] = title
        if parent_id and self.parent_param():
            args[self.parent_param()] = parent_id
        await self.r.call(self.UPDATE, args, write=True)


def pick_dialect(rovo: RovoMCP, cloud_id: str) -> Dialect:
    if "updateConfluenceContent" in rovo.tools:
        return V2(rovo, cloud_id)
    if "updateConfluencePage" in rovo.tools:
        return V1(rovo, cloud_id)
    raise RovoError("no Confluence update tool exposed: check the token scopes "
                    "(write:confluence...) and the admin's Rovo MCP permission groups; run --inspect")


# --------------------------------------------------------------------------- state
SHA_RE = re.compile(r"[0-9a-f]{40}")


def valid_state_entry(key: str, value: Any) -> bool:
    """Shape of one cache entry. A damaged or hand-edited entry is dropped (and rebuilt from
    Confluence) instead of crashing the run later."""
    if key.startswith("space:"):
        return isinstance(value, str) and bool(value)
    if not isinstance(value, dict) or not isinstance(value.get("page_id"), str) or not value["page_id"]:
        return False
    if not (isinstance(value.get("sha"), str) and SHA_RE.fullmatch(value["sha"])):
        return False
    if any(value.get(k) is not None and not isinstance(value[k], str) for k in ("title", "parent", "gen", "banner")):
        return False
    block = value.get("banner_blocked")
    return (isinstance(value.get("verified", 0), (int, float))
            and (block is None or (isinstance(block, dict) and isinstance(block.get("sha"), str)
                                   and isinstance(block.get("until", 0), (int, float)))))


def load_state(path: str | None) -> dict:
    """The state file is only a cache (page ids, synced commits, banner checks); the sync-ref
    marker on each page is the source of truth. So a missing, empty or corrupt file means an
    empty cache, which this run rebuilds at the cost of extra MCP reads. A corrupt file is moved
    to <path>.corrupt-<time> for inspection. A path that cannot be written stops the run before
    anything is synced, instead of failing after the first page."""
    if not path:
        return {}
    if os.path.isdir(path):
        raise ConfigError(f"--state-file {path} is a directory")
    try:
        fd, probe = _state_tmp(path)
        os.close(fd)
        os.remove(probe)
    except OSError as exc:
        raise ConfigError(f"--state-file {path} cannot be written: {exc}") from exc
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        data = json.loads(text) if text.strip() else {}
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, found {type(data).__name__}")
    except OSError as exc:
        raise ConfigError(f"--state-file {path} cannot be read: {exc}") from exc
    except ValueError as exc:  # invalid JSON, not UTF-8, or not an object
        aside = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(path, aside)
            kept = f"moved to {aside}"
        except OSError:
            kept = "left in place (it will be overwritten)"
        warn(f"state file {path} is corrupt ({exc}); {kept}. This run starts with an empty cache: "
             f"more MCP reads, nothing lost (pages carry their sync-ref marker)")
        return {}
    state = {k: v for k, v in data.items() if valid_state_entry(k, v)}
    dropped = len(data) - len(state)
    if dropped:
        warn(f"state file {path}: ignored {dropped} malformed entr{'y' if dropped == 1 else 'ies'}; "
             f"rebuilt from Confluence during this run")
    return state


def _state_tmp(path: str) -> tuple[int, str]:
    """A temporary file with a unique name next to the state file (same folder, so the final
    rename is atomic)."""
    folder = os.path.dirname(os.path.abspath(path))
    return tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.", suffix=".tmp")


def save_state(path: str | None, state: dict) -> None:
    """Write to a uniquely named temporary file, then rename it over the state file: a crash
    leaves either the old or the new file, never a half-written one."""
    if not path:
        return
    try:
        fd, tmp = _state_tmp(path)
    except OSError as exc:
        raise ConfigError(f"--state-file {path} cannot be written: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise ConfigError(f"--state-file {path} cannot be written: {exc}") from exc


class LockBusy(Exception):
    pass


def default_lock_file() -> str:
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(cache, "kb-sync", "sync.lock")


@contextlib.contextmanager
def run_lock(path: str):
    """Exclusive lock held for a whole sync run (git planning excepted). The OS releases it when
    the process ends, even after a crash or a kill, so a stale lock never blocks the next run."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")  # noqa: SIM115 - held open for the whole run
    except OSError as exc:
        raise ConfigError(f"--lock-file {path} cannot be opened: {exc}") from exc
    try:
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = ""
            with contextlib.suppress(OSError):
                fh.seek(0)
                holder = fh.read().strip()
            raise LockBusy(f"another sync run holds {path}" + (f" ({holder})" if holder else "")
                           + "; nothing was done") from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid {os.getpid()} on {socket.gethostname()} since {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.flush()
        yield
    finally:
        fh.close()


def is_blocked(entry: dict) -> bool:
    """Banner rewrites are paused on this page: its banner did not read back after the last one."""
    block = entry.get("banner_blocked") or {}
    return block.get("sha") == entry.get("sha") and time.time() < float(block.get("until", 0))


def verify_due(entry: dict, hours: float) -> bool:
    """Should a page that is already up to date be read again to check its banner?"""
    if hours < 0 or is_blocked(entry):
        return False
    return time.time() - float(entry.get("verified", 0)) >= hours * 3600


# --------------------------------------------------------------------------- sync
@dataclass
class Rewrite:
    """A write to a page that git did not change: banner restored / updated, or --adopt."""
    plan: RepoPlan
    target: Doc | Folder
    kind: str  # "restored" | "updated" | "adopted"
    links: Links


@dataclass
class Ctx:
    args: argparse.Namespace
    rovo: RovoMCP
    d: Dialect
    space_id: str
    state: dict
    site: str | None = None      # https://<site>.atlassian.net, for links between pages
    created_titles: dict[str, str] = field(default_factory=dict)  # titles created in this run, lower case
    taken: set[str] = field(default_factory=set)       # titles held by pages that are not ours, lower case
    unseen: set[str] = field(default_factory=set)      # titles held by a page search does not return yet
    claimed: dict[str, str] = field(default_factory=dict)  # page id -> key of the file or folder it is for
    verified: set[str] = field(default_factory=set)    # pages read or created in this run
    warned: set[str] = field(default_factory=set)
    pushed: int = 0
    read_proof: str = ""  # a page whose banner came back intact when read in this run
    written: list[str] = field(default_factory=list)  # page ids written in this run
    rewrites: list[Rewrite] = field(default_factory=list)  # deferred until reads are proven


TITLE_TAKEN_RE = re.compile(r"already exists|same title|title.{0,40}(?:exists|taken|in use)", re.I)
GONE_STATUSES = {"trashed", "deleted", "purged"}
REWRITE_LABEL = {"restored": "banner restore", "updated": "banner update", "adopted": "page takeover (--adopt)"}
REWRITE_PREFIX = {"restored": "Banner restored · ", "updated": "Banner updated · ", "adopted": ""}


def page_status(res: ToolResult) -> str:
    """Page status in a read response ("current", "trashed", "archived"...), "" if not given.
    A page in the Confluence trash can still be read by id: it must count as deleted."""
    status = res.data.get("status") if isinstance(res.data, dict) else None
    return status.lower() if isinstance(status, str) else ""


async def read_page(ctx: Ctx, page_id: str) -> PageRead:
    res, token = await ctx.d.read(page_id)
    data = res.data if isinstance(res.data, dict) else {}
    title = data.get("title") if isinstance(data.get("title"), str) else None
    if title is None:
        title = next((t for x in res.dicts() if isinstance(t := x.get("title"), str)), None)
    parent = next((data[k] for k in PARENT_PARAMS if data.get(k) not in (None, "")), None)
    if parent is None:
        parent = next((x[k] for x in res.dicts() for k in PARENT_PARAMS if x.get(k) not in (None, "")), None)
    return PageRead(res, token, MARKER_RE.search(res.blob()), page_status(res), title,
                    str(parent) if parent is not None else None)


def parent_page(plan: RepoPlan, target: Doc | Folder) -> Page | None:
    if isinstance(target, Doc):
        return plan.folders[target.folder].page
    return plan.folders[target.parent].page if target.parent is not None else None


def want_parent(ctx: Ctx, plan: RepoPlan, target: Doc | Folder) -> str | None:
    """Page the target's page belongs under (None: the space root, or not created yet)."""
    if isinstance(target, Doc):
        return plan.folders[target.folder].page.page_id
    if target.parent is None:
        return ctx.args.parent_id
    return plan.folders[target.parent].page.page_id


def claim(ctx: Ctx, page: Page) -> None:
    ctx.claimed[page.page_id] = page.key


def forget_page(ctx: Ctx, page: Page) -> None:
    """The known page is gone: drop it from the state file and look the target up again."""
    if page.page_id and ctx.claimed.get(page.page_id) == page.key:
        del ctx.claimed[page.page_id]
    for key in (page.key, page.old_key):
        if key:
            ctx.state.pop(key, None)
    page.page_id = page.title = page.parent = page.read = None
    page.owned = False
    page.renamed_from = page.old_key = ""


def record(ctx: Ctx, page: Page, entry: dict) -> None:
    """Store the state entry of a page. Callers store it only after the page's Confluence writes
    succeeded, so the state file never runs ahead of Confluence (it may lag behind: pages carry their
    sync-ref marker, which the next run reads). Drops the entry of the file's earlier path."""
    if page.old_key:
        ctx.state.pop(page.old_key, None)
    ctx.state[page.key] = entry
    save_state(ctx.args.state_file, ctx.state)


def seen_entry(page: Page, sha: str | None) -> dict | None:
    """State entry for a page whose rewrite is queued: what its read showed, not what the rewrite will
    write, so the next run reads the page again until the rewrite is done."""
    m = page.read.marker if page.read else None
    sha = m.group(1) if m else sha
    if not sha:
        return None
    return {"page_id": page.page_id, "sha": sha, "title": page.title, "parent": page.parent,
            "gen": m.group(4) if m else None, "banner": m.group(2) if m else None, "verified": int(time.time())}


def warn_once(ctx: Ctx, key: str, msg: str) -> None:
    if key not in ctx.warned:
        ctx.warned.add(key)
        warn(msg)


async def check_page(ctx: Ctx, plan: RepoPlan, target: Doc | Folder) -> bool:
    """Read the page known for the target. False (and the page forgotten) when it is gone or now
    carries another file's marker; the caller then looks it up or creates it again."""
    p = target.page
    try:
        pr = await read_page(ctx, p.page_id)
    except RovoError:
        pr = None
    if pr is not None and pr.status in GONE_STATUSES:
        log(f"  {label_of(target)}: page {p.page_id} is in the Confluence trash")
        pr = None
    if pr is not None and pr.marker and pr.marker.group(3) not in p.idents:
        warn(f"{plan.name}:{label_of(target)}: page {p.page_id} now carries the marker of another file or folder; "
             f"publishing this one's page again")
        pr = None
    if pr is None:
        forget_page(ctx, p)
        return False
    p.read, p.title, p.parent = pr, pr.title or p.title, pr.parent or p.parent
    ctx.verified.add(p.page_id)
    if pr.marker:
        ctx.read_proof = ctx.read_proof or f"{plan.name}:{label_of(target)}"
    if pr.status == "archived":
        warn(f"{plan.name}:{label_of(target)}: page {p.page_id} is archived in Confluence; skipped. Unarchive it "
             f"to resume the sync, or remove the {'file' if isinstance(target, Doc) else 'folder'} from git")
        p.skip = True
        entry = ctx.state.get(p.key)
        if entry:  # checked again at the --verify-hours cadence, or when the file changes
            entry.update(verified=int(time.time()), banner=pr.marker.group(2) if pr.marker else None)
            save_state(ctx.args.state_file, ctx.state)
    return True


def state_clean(ctx: Ctx, entry: dict, target: Doc | Folder) -> bool:
    """Known up to date from the state file alone (more checks once the page ids of this run are known)."""
    if entry.get("banner") != banner_id() or verify_due(entry, ctx.args.verify_hours):
        return False
    return not isinstance(target, Doc) or entry.get("sha") == target.versions[-1].commit


async def locate(ctx: Ctx, plan: RepoPlan, target: Doc | Folder) -> None:
    """Find the target's page without writing: through the state file (current path, then the
    paths of a renamed file), else by its titles in the space, recognized by the page id in its
    marker. A page found nowhere is created later."""
    p = target.page
    keys = [p.key] + ([f"{plan.repo}:{old}" for old in target.old_paths] if isinstance(target, Doc) else [])
    for key in keys:
        entry = ctx.state.get(key)
        if not entry or entry["page_id"] in ctx.claimed:
            continue
        p.page_id, p.title, p.parent, p.owned = entry["page_id"], entry.get("title"), entry.get("parent"), True
        if key != p.key:
            p.renamed_from, p.old_key = key[len(plan.repo) + 1:], key
        if not p.renamed_from and state_clean(ctx, entry, target):
            claim(ctx, p)
            return
        if await check_page(ctx, plan, target):
            claim(ctx, p)
            return
        break
    candidates = title_candidates(plan, target)
    titles = titles_of(plan, target)
    order = {t: i for i, t in enumerate(candidates)}
    order.update({t.lower(): i for t, i in list(order.items())})
    found = sorted(await ctx.d.find_titled(ctx.args.space_key, candidates),
                   key=lambda f: order.get(f[1], order.get(f[1].lower(), 99)))
    olds = {page_key(plan.repo, old): old for old in getattr(target, "old_paths", [])}
    for page_id, title in found:
        if page_id in ctx.claimed:
            ctx.taken.add(title.lower())
            continue
        try:
            pr = await read_page(ctx, page_id)
        except RovoError:
            continue
        if pr.status in GONE_STATUSES:
            continue
        if pr.marker and pr.marker.group(3) in p.idents:
            p.page_id, p.read, p.title, p.parent = page_id, pr, pr.title or title, pr.parent
            claim(ctx, p)
            ctx.verified.add(page_id)
            ctx.read_proof = ctx.read_proof or f"{plan.name}:{label_of(target)}"
            if pr.marker.group(3) != p.ident:
                p.renamed_from = olds.get(pr.marker.group(3), "")
            if pr.status == "archived":
                warn(f"{plan.name}:{label_of(target)}: page {page_id} is archived in Confluence; skipped. "
                     f"Unarchive it to resume the sync, or remove it from git")
                p.skip = True
            return
        if pr.marker or title.lower() not in {t.lower() for t in titles}:
            ctx.taken.add(title.lower())  # another file's page, or an unrelated page with an older title of ours
            continue
        above = parent_page(plan, target)
        expected = want_parent(ctx, plan, target) or (above.in_way if above else None)
        here = pr.parent is not None and pr.parent == expected
        if isinstance(target, Folder) and target.parent is None and not ctx.args.parent_id:
            here = True  # the repository's root page title: any page without banner is in the way
        if not here:
            ctx.taken.add(title.lower())  # an unrelated page: this one gets the fallback title
            continue
        if ctx.args.adopt:
            p.page_id, p.read, p.title, p.parent, p.adopt = page_id, pr, pr.title or title, pr.parent, True
            claim(ctx, p)
            ctx.verified.add(page_id)
            log(f"  {label_of(target)}: page {page_id} {title!r} has no sync banner; takeover queued (--adopt)")
            return
        warn(f"{plan.name}:{label_of(target)}: page {page_id} {title!r} has no sync banner and sits where this "
             f"page belongs; skipped. If it is this page with its banner removed, or a page to take over, rerun "
             f"with --adopt; otherwise rename or move it")
        p.skip, p.in_way = True, page_id
        return


async def wait_for_title(ctx: Ctx, title: str) -> str | None:
    """The page holding a taken title: searched at once (it may differ in case), then again while the
    search index catches up with a page created moments ago."""
    found = await ctx.d.find_page(ctx.args.space_key, title)
    for delay in (2, 4, 8, 16, 30):
        if found:
            return found
        log(f"  {title!r} exists but search does not return it yet; retrying in {delay}s")
        await asyncio.sleep(delay)
        found = await ctx.d.find_page(ctx.args.space_key, title)
    return found


async def create_page(ctx: Ctx, plan: RepoPlan, target: Doc | Folder, parent_id: str | None,
                      body: str, message: str) -> bool:
    """Create the target's page under parent_id with its title, or its fallback title when the
    title is taken. False when the page turned out to exist already: found as ours while waiting
    for the search index (a run moments ago)."""
    p, titles = target.page, titles_of(plan, target)
    for title in dict.fromkeys(titles):
        if title.lower() in ctx.taken or title.lower() in ctx.created_titles:
            continue
        if title.lower() in ctx.unseen:
            raise RovoError(f"title {title!r} is taken by a page that search does not return yet; left for the "
                            f"next run rather than risk a second page")
        try:
            page_id = await ctx.d.create(ctx.space_id, parent_id, title, body, message)
        except RovoError as exc:
            if not TITLE_TAKEN_RE.search(str(exc)):
                raise
        else:
            ctx.created_titles[title.lower()] = page_id
            p.page_id, p.title, p.parent, p.created, p.owned = page_id, title, parent_id, True, False
            ctx.claimed[page_id] = p.key
            ctx.verified.add(page_id)
            ctx.pushed += 1
            ctx.written.append(page_id)
            return True
        # The title is taken by a page search did not return. It may be this target's own page, created
        # by a run moments ago (search index lag): only a page identified as another one's lets the
        # fallback title be used, so a page is never created twice.
        found = await wait_for_title(ctx, title)
        if found is None:
            ctx.unseen.add(title.lower())
            raise RovoError(f"title {title!r} is taken by a page that search does not return yet (search index "
                            f"lag after a recent run?); left for the next run rather than risk a second page")
        if found not in ctx.claimed:
            pr = await read_page(ctx, found)  # a failed read raises: no fallback without knowing whose page it is
            if pr.marker and pr.marker.group(3) in p.idents:
                p.page_id, p.read, p.title, p.parent = found, pr, pr.title or title, pr.parent
                claim(ctx, p)
                ctx.verified.add(found)
                return False
        ctx.taken.add(title.lower())
        log(f"  {label_of(target)}: title {title!r} is taken in the space")
    raise RovoError(f"no free title: {titles[0]!r} and {titles[1]!r} are both taken in the space")


async def write(ctx: Ctx, plan: RepoPlan, target: Doc | Folder, title: str, body: str, message: str,
                token: str | None, parent: str | None) -> None:
    """One update of the target's page. When Confluence refuses the title as taken, the update is
    sent again with the fallback title (a refused update did not land)."""
    d, p = ctx.d, target.page
    fallback = titles_of(plan, target)[1]
    try:
        await d.update(p.page_id, title, body, message, token, parent)
    except RovoError as exc:
        if not TITLE_TAKEN_RE.search(str(exc)) or title == fallback:
            raise
        log(f"  {label_of(target)}: title {title!r} is taken in the space; titled {fallback!r}")
        ctx.taken.add(title.lower())
        title = fallback
        token = (await d.read(p.page_id))[1] if d.needs_snapshot else None
        await d.update(p.page_id, title, body, message, token, parent)
    if d.can_retitle():
        p.title = title
    if parent:
        p.parent = parent
    ctx.pushed += 1
    ctx.written.append(p.page_id)


def title_to_send(plan: RepoPlan, target: Doc | Folder) -> str:
    """Keep the title the page has when it is the target's title or fallback title."""
    titles = titles_of(plan, target)
    return target.page.title if target.page.title in titles else titles[0]


async def alive(ctx: Ctx, plan: RepoPlan, folder: Folder, links: Links) -> str:
    """Page id of the folder's page, checked in this run: read if it was only known from the state
    file, created (with the folders above it) when missing or gone."""
    p = folder.page
    if p.page_id and (p.page_id in ctx.verified or await check_page(ctx, plan, folder)):
        return p.page_id
    if p.skip:
        raise RovoError(f"the page of folder {folder.label} is not available (see the warning above)")
    await create_folder(ctx, plan, folder, links)
    return p.page_id


async def create_folder(ctx: Ctx, plan: RepoPlan, folder: Folder, links: Links) -> None:
    if folder.parent is None:
        parent_id = ctx.args.parent_id
    else:
        parent_id = await alive(ctx, plan, plan.folders[folder.parent], links)
    body, gen, _ = render_folder(plan, folder, links)
    if await create_page(ctx, plan, folder, parent_id, body, "Created by repo sync"):
        folder.page.gen = gen
        log(f"+ folder {folder.label}: created page {folder.page.page_id} {folder.page.title!r}")
        record(ctx, folder.page, folder_entry(plan, folder, gen))


def folder_entry(plan: RepoPlan, folder: Folder, gen: str) -> dict:
    prefix = f"{folder.path}/" if folder.path else ""
    newest = max((d.versions[-1] for d in plan.docs if d.path.startswith(prefix)),
                 key=lambda v: (v.ts(), v.commit), default=None)
    p = folder.page
    return {"page_id": p.page_id, "sha": newest.commit if newest else plan.tip, "title": p.title,
            "parent": p.parent, "gen": gen, "banner": banner_id(), "verified": int(time.time())}


async def create_doc(ctx: Ctx, plan: RepoPlan, doc: Doc, links: Links) -> None:
    parent_id = await alive(ctx, plan, plan.folders[doc.folder], links)
    first = doc.versions[0]
    body, gen = render_doc(first, plan, doc, ctx.args.transform, links)
    if await create_page(ctx, plan, doc, parent_id, body, version_message(first)):
        doc.page.gen = gen
        log(f"+ {doc.path}: created page {doc.page.page_id} {doc.page.title!r} @ {first.label}")


def pending_versions(plan: RepoPlan, doc: Doc, synced: str | None) -> list[Version]:
    """Versions newer than the commit the page was generated from."""
    latest = doc.versions[-1]
    if not synced:
        return [latest]
    if not commit_exists(plan.local, synced):
        warn(f"{doc.path}: synced commit {synced[:7]} not in local history (rewritten?); "
             f"appending the latest version only")
        return [latest]
    pending = [v for v in doc.versions if not is_ancestor(plan.local, v.commit, synced)]
    # e.g. a "main@sha" head version later tagged: same content, no new version needed
    published = next((c for p in dict.fromkeys([doc.path, *doc.old_paths, *(v.path for v in doc.versions)])
                      if (c := blob_at(plan.local, synced, p)) is not None), None)
    while pending and pending[0].content == published:
        pending.pop(0)
    return pending


async def sync_doc(ctx: Ctx, plan: RepoPlan, doc: Doc, links: Links) -> None:
    """Bring the file's page up to date: missing git versions, links, parent, title, banner."""
    args, d, p = ctx.args, ctx.d, doc.page
    if p.skip:
        return
    latest = doc.versions[-1]
    folder = plan.folders[doc.folder]
    titles = doc_titles(plan, doc)
    gen_now = doc_gen(doc, latest, links)
    if not p.page_id:
        await create_doc(ctx, plan, doc, links)
    elif not p.created and p.read is None:
        entry = ctx.state.get(p.key) or {}
        if (entry.get("sha") == latest.commit and entry.get("gen") == gen_now and entry.get("title") in titles
                and entry.get("parent") in (None, folder.page.page_id)):
            log(f"= {doc.path}: up to date ({latest.label})")
            return
        if not await check_page(ctx, plan, doc):
            await create_doc(ctx, plan, doc, links)
        elif p.skip:
            return

    m, pending, rewrite, written_gen = None, [], "", p.gen
    if p.created:
        pending = doc.versions[1:]
    else:
        m = p.read.marker
        synced = None
        if m:
            synced, written_gen = m.group(1), m.group(4)
        elif p.adopt:
            rewrite = "adopted"
        else:  # our page (state file), but its banner with the marker is gone
            synced = (ctx.state.get(p.old_key or p.key) or {}).get("sha")
            log(f"  {doc.path}: banner missing on the page")
        if not rewrite:
            pending = pending_versions(plan, doc, synced)
            if not pending and m and m.group(3) != p.ident:
                pending = [latest]  # renamed: the title, banner and marker name the new path

    parent = folder.page.page_id
    move = None
    if parent and p.parent != parent and (p.parent is not None or posixpath.dirname(p.renamed_from or doc.path) != doc.folder):
        if d.parent_param():
            move = parent = await alive(ctx, plan, folder, links)
        else:
            warn_once(ctx, f"move:{p.key}", f"{doc.path}: page {p.page_id} is not under {folder_titles(plan, folder)[0]!r} "
                                            f"and {d.UPDATE} takes no parent id: move it in Confluence")

    # new git versions are always written: they are right whatever page reads return
    token = p.read.token if p.read else None
    title = title_to_send(plan, doc)
    for i, v in enumerate(pending):
        if d.needs_snapshot and token is None:
            _, token = await d.read(p.page_id)
        body, written_gen = render_doc(v, plan, doc, args.transform, links)
        prefix = f"Renamed from {p.renamed_from} · " if p.renamed_from and i == 0 else ""
        await write(ctx, plan, doc, title, body, version_message(v, prefix), token, move)
        title, move, token = p.title or title, None, None  # a snapshot token is single-use
        log(f"  {doc.path}: new version @ {v.label}" + ("" if v.label == v.commit[:7] else f" ({v.commit[:7]})"))
    if p.renamed_from and pending and not d.can_retitle():
        warn(f"{doc.path}: {d.UPDATE} takes no title, so page {p.page_id} keeps the title {p.title!r}; "
             f"rename it in Confluence")

    if rewrite:
        # rewrites wait until the run has proof that page reads return the banner (apply_rewrites)
        ctx.rewrites.append(Rewrite(plan, doc, rewrite, links))
        log(f"  {doc.path}: {REWRITE_LABEL[rewrite]} queued")
        return  # the page only becomes ours once the takeover is written
    if not pending and not p.created and not banner_ok(m):
        rewrite = "updated" if m else "restored"
        ctx.rewrites.append(Rewrite(plan, doc, rewrite, links))
        log(f"  {doc.path}: {REWRITE_LABEL[rewrite]} queued")
        entry = seen_entry(p, synced)
        if entry:
            record(ctx, p, entry)
        return
    else:
        why = []
        if written_gen != gen_now:
            why.append("Links updated")
        if move:
            why.append(f"Moved under {page_title_now(plan, folder)}")
        if not pending and p.read and p.read.title and p.read.title not in titles and d.can_retitle():
            why.append("Retitled")
        if why:
            token = (await d.read(p.page_id))[1] if d.needs_snapshot else None
            body, _ = render_doc(latest, plan, doc, args.transform, links)
            await write(ctx, plan, doc, title_to_send(plan, doc), body,
                        version_message(latest, " · ".join(why) + " · "), token, move)
            log(f"  {doc.path}: {' · '.join(why)} @ {latest.label}")
        elif not pending and not p.created:
            log(f"= {doc.path}: up to date ({latest.label}), banner checked")
    record(ctx, p, {"page_id": p.page_id, "sha": latest.commit, "title": p.title or title, "parent": p.parent,
                    "gen": gen_now, "banner": banner_id(), "verified": int(time.time())})


async def sync_folder(ctx: Ctx, plan: RepoPlan, folder: Folder, links: Links) -> None:
    """Bring the folder's page up to date once the pages below it are: listing, ADR index, banner."""
    d, p = ctx.d, folder.page
    if p.skip:
        return
    missing = [label_of(t) for t in listed_targets(plan, folder) if not t.page.page_id]
    if missing:
        log(f"  folder {folder.label}: not refreshed, the page of {', '.join(missing)} is not available")
        return
    if not p.page_id:
        await create_folder(ctx, plan, folder, links)
    body, gen, newest = render_folder(plan, folder, links)
    titles = folder_titles(plan, folder)
    parent = want_parent(ctx, plan, folder)
    if p.created and p.gen == gen:
        return
    if not p.created and p.read is None:
        entry = ctx.state.get(p.key) or {}
        if (entry.get("gen") == gen and entry.get("title") in titles and entry.get("parent") in (None, parent)
                and state_clean(ctx, entry, folder)):
            log(f"= folder {folder.label}: up to date")
            return
        if not await check_page(ctx, plan, folder):
            await create_folder(ctx, plan, folder, links)
            return
        if p.skip:
            return
    m = None if p.created else p.read.marker
    if not p.created and (not m or not banner_ok(m)):
        rewrite = "adopted" if p.adopt else "updated" if m else "restored"
        ctx.rewrites.append(Rewrite(plan, folder, rewrite, links))
        log(f"  folder {folder.label}: {REWRITE_LABEL[rewrite]} queued")
        entry = None if p.adopt else seen_entry(p, (ctx.state.get(p.key) or {}).get("sha"))
        if entry:
            record(ctx, p, entry)
        return
    else:
        written = p.gen if p.created else m.group(4)
        move = parent if parent and p.parent and p.parent != parent and d.parent_param() else None
        retitle = bool(p.read and p.read.title and p.read.title not in titles and d.can_retitle())
        if written != gen or move or retitle:
            token = (await d.read(p.page_id))[1] if d.needs_snapshot else None
            what = [] if written != gen else (["Moved"] if move else []) + (["Retitled"] if retitle else [])
            await write(ctx, plan, folder, title_to_send(plan, folder), body,
                        folder_message(folder, newest, "".join(f"{w} · " for w in what)), token, move)
            log(f"  folder {folder.label}: {'ADR index' if folder.adr else 'listing'} updated"
                if written != gen else f"  folder {folder.label}: {', '.join(what).lower()}")
        else:
            log(f"= folder {folder.label}: up to date, banner checked")
    record(ctx, p, folder_entry(plan, folder, gen))


async def sync_repo(ctx: Ctx, plan: RepoPlan) -> int:
    """Locate every page first (no writes), then create the missing ones top-down, then add the
    missing versions, then refresh the folder pages, whose listing and ADR index link to the
    pages below them. Returns the number of failures."""
    failures = 0
    links = Links(ctx.site, ctx.args.space_key, plan)
    targets = targets_in_order(plan)
    for target in targets:
        try:
            await locate(ctx, plan, target)
        except RovoError as exc:
            warn(f"{plan.name}:{label_of(target)}: {exc}")
            target.page.skip = True
            failures += 1
    # all pages exist before versions are added, so links between pages resolve
    for target in creation_order(plan, targets):
        if not target.page.page_id and not target.page.skip:
            try:
                if isinstance(target, Doc):
                    await create_doc(ctx, plan, target, links)
                else:
                    await create_folder(ctx, plan, target, links)
            except (RovoError, RuntimeError, GitError) as exc:
                warn(f"{plan.name}:{label_of(target)}: {exc}")
                target.page.skip = True
                failures += 1
    for doc in plan.docs:
        try:
            await sync_doc(ctx, plan, doc, links)
        except (RovoError, RuntimeError, GitError) as exc:
            warn(f"{plan.name}:{doc.path}: {exc}")
            failures += 1
    for folder in reversed(plan.folders.values()):
        try:
            await sync_folder(ctx, plan, folder, links)
        except (RovoError, RuntimeError) as exc:
            warn(f"{plan.name}:{label_of(folder)}: {exc}")
            failures += 1
    await check_orphans(ctx, plan)
    return failures


async def prove_reads(ctx: Ctx) -> str:
    """Up to 3 extra reads of pages synced earlier (in this run first) whose banner should show.
    Returns what proved that page reads return the banner, or "" when nothing did."""
    queued = {r.target.page.page_id for r in ctx.rewrites}
    earlier = [e["page_id"] for e in ctx.state.values()
               if isinstance(e, dict) and e.get("sha") and e.get("page_id")]
    for page_id in [p for p in dict.fromkeys([*ctx.written, *earlier]) if p not in queued][:3]:
        try:
            res, _ = await ctx.d.read(page_id)
        except RovoError:
            continue
        if MARKER_RE.search(res.blob()):
            return f"page {page_id} read back with its banner"
    return ""


async def rewrite_page(ctx: Ctx, r: Rewrite) -> bool:
    """Write the current content over a page that git did not change, then read it back.
    Returns False when the banner does not show up (rewrites then paused on that page)."""
    d, t, p = ctx.d, r.target, r.target.page
    if isinstance(t, Doc):
        latest = t.versions[-1]
        body, gen = render_doc(latest, r.plan, t, ctx.args.transform, r.links)
        message, sha = version_message(latest, REWRITE_PREFIX[r.kind]), latest.commit
    else:
        body, gen, newest = render_folder(r.plan, t, r.links)
        message = folder_message(t, newest, REWRITE_PREFIX[r.kind])
        sha = newest.commit if newest else r.plan.tip
    parent = want_parent(ctx, r.plan, t)
    move = parent if parent and p.parent and p.parent != parent and d.parent_param() else None
    token = (await d.read(p.page_id))[1] if d.needs_snapshot else None
    await write(ctx, r.plan, t, title_to_send(r.plan, t), body, message, token, move)
    what = "page adopted" if r.kind == "adopted" else f"banner {r.kind}"
    log(f"  {r.plan.name}:{label_of(t)}: {what}")
    now = int(time.time())
    entry: dict[str, Any] = {"page_id": p.page_id, "sha": sha, "title": p.title, "parent": p.parent,
                             "gen": gen, "banner": banner_id(), "verified": now}
    check = await read_page(ctx, p.page_id)
    ok = banner_ok(check.marker) and check.marker.group(3) == p.ident
    if not ok:
        until = now + BLOCK_DAYS * 86400
        entry["banner_blocked"] = {"sha": sha, "until": until}
        warn(f"{r.plan.name}:{label_of(t)}: banner written but not found when the page was read back; "
             f"banner rewrites paused on this page until {time.strftime('%Y-%m-%d', time.gmtime(until))} "
             f"or until its content changes in git")
    record(ctx, p, entry)
    return ok


async def apply_rewrites(ctx: Ctx) -> int:
    """Run the queued rewrites, but only once page reads are proven to return the banner, so a
    site whose reads hide it costs no version at all. Returns the number of failures."""
    if not ctx.rewrites:
        return 0
    names = [f"{r.plan.name}:{label_of(r.target)} ({REWRITE_LABEL[r.kind]})" for r in ctx.rewrites]
    proof = f"{ctx.read_proof} read with its banner" if ctx.read_proof else await prove_reads(ctx)
    if not proof:
        error(f"Nothing rewritten: no page read in this run showed its banner, so it is not proven that "
              f"page reads work on this site. Skipped {len(names)} page rewrite(s): {', '.join(names)}. "
              f"New git versions were still synced. Run --inspect --page-id <id> to see what page reads return.")
        return len(names)  # every requested rewrite is incomplete
    log(f"\nPage reads verified ({proof}); {len(names)} page rewrite(s):")
    failures = not_read_back = 0
    for i, r in enumerate(ctx.rewrites):
        if not_read_back >= 2:
            error(f"stopped after {not_read_back} rewrites whose banner did not read back; "
                  f"skipped: {', '.join(names[i:])}")
            failures += len(names) - i
            break
        try:
            if not await rewrite_page(ctx, r):
                not_read_back += 1
                failures += 1  # written but not verified
        except (RovoError, RuntimeError) as exc:
            warn(f"{r.plan.name}:{label_of(r.target)}: {exc}")
            failures += 1
    return failures


async def check_orphans(ctx: Ctx, plan: RepoPlan) -> None:
    """Pages of files and folders that are no longer in git at the synced branch (deleted, or no
    longer matched by --include/--exclude). They are left unchanged and listed on every run. At
    the --verify-hours cadence each one is read, and forgotten once it is gone from Confluence."""
    current = {t.page.key for t in [*plan.docs, *plan.folders.values()]}
    orphans = []
    for key, entry in sorted(ctx.state.items()):
        if not key.startswith(f"{plan.repo}:") or key in current or not isinstance(entry, dict):
            continue
        path = key[len(plan.repo) + 1:]
        if verify_due(entry, ctx.args.verify_hours):
            try:
                gone = page_status((await ctx.d.read(entry["page_id"]))[0]) in GONE_STATUSES
            except RovoError:
                gone = True
            if gone:
                del ctx.state[key]
                log(f"  {path}: gone from git and page {entry['page_id']} gone from Confluence; forgotten")
                continue
            entry["verified"] = int(time.time())
        orphans.append(f"{entry.get('title') or path} (page {entry['page_id']})")
    save_state(ctx.args.state_file, ctx.state)
    if orphans:
        warn(f"{plan.name}: {len(orphans)} page(s) mirror files or folders no longer in git at {plan.branch} "
             f"(deleted or no longer included). They are left unchanged; archive or delete them in "
             f"Confluence: {', '.join(orphans)}")


def report_blocked(state: dict) -> None:
    """Every run lists the pages on which banner rewrites are paused."""
    paused = [f"{key} (until {time.strftime('%Y-%m-%d', time.gmtime(e['banner_blocked']['until']))})"
              for key, e in sorted(state.items()) if isinstance(e, dict) and is_blocked(e)]
    if paused:
        warn(f"banner rewrites paused on {len(paused)} page(s) whose banner did not read back after a "
             f"rewrite: {', '.join(paused)}")


def auth_headers() -> dict[str, str]:
    key = os.environ.get("ATLASSIAN_SERVICE_KEY")
    if key:
        return {"Authorization": f"Bearer {key}"}
    email, token = os.environ.get("ATLASSIAN_EMAIL"), os.environ.get("ATLASSIAN_API_TOKEN")
    if email and token:
        raw = base64.b64encode(f"{email}:{token}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}
    raise ConfigError("set ATLASSIAN_SERVICE_KEY, or ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN")


def site_of(value: str | None) -> str | None:
    """https://<site>.atlassian.net from a URL, None for a cloud id."""
    url = urlparse(value or "")
    return f"{url.scheme}://{url.netloc}" if url.scheme in ("https", "http") and url.netloc else None


async def resolve_site(rovo: RovoMCP, given: str | None) -> tuple[str, str | None]:
    """(cloudId for the tools, site URL for links between pages or None)."""
    site = site_of(given)
    if given and site:
        return given, site
    if not rovo.reachable("getAccessibleAtlassianResources"):
        if given:
            return given, None
        raise ConfigError(f"{rovo.endpoint} does not expose getAccessibleAtlassianResources, so the site cannot "
                          f"be looked up: pass --cloud-id https://<your-site>.atlassian.net")
    try:
        res = await rovo.call("getAccessibleAtlassianResources", {})
    except RovoError:
        if given:
            return given, None
        raise
    sites = [d for d in res.dicts() if d.get("id") and (d.get("url") or d.get("name"))]
    if given:
        match = next((s for s in sites if str(s["id"]) == given), None)
        return given, site_of(str(match.get("url") or "")) if match else None
    if not sites:
        raise RovoError(f"no accessible Atlassian site: {res.text[:300]}")
    if len(sites) > 1:
        warn("several sites accessible, using the first; pass --cloud-id to choose: "
             + ", ".join(str(s.get("url") or s.get("name")) for s in sites))
    return str(sites[0]["id"]), site_of(str(sites[0].get("url") or ""))


def print_tree(plan: RepoPlan, folder: Folder, depth: int, titles: dict[str, str]) -> None:
    """The planned page tree, with the titles a first sync gives (unless another page holds them)."""
    pad = "  " * depth
    note = ""
    if folder.adr:
        n = sum(1 for d in plan.docs if d.kind == "adr" and d.adr_root == folder.path)
        note = f"   [ADR index: {n} ADR(s)]"
    log(f"  {pad}📁 {titles[folder.page.key]}{note}")
    for sub in folder.subfolders:
        print_tree(plan, plan.folders[sub], depth + 1, titles)
    for doc in folder.docs:
        labels = [v.label for v in doc.versions]
        shown = ", ".join(labels if len(labels) <= 8 else ["…", *labels[-8:]])
        if doc.kind == "adr":
            info = doc.adr
            status = text_of(info.fields.get("status")) or "no status"
            kind = f"   [{info.id or 'ID not assigned'} · {status} · {info.date or 'date not set'}]"
        else:
            kind = "   [template]" if doc.kind == "template" else ""
        log(f"  {pad}  📄 {titles[doc.page.key]}{kind}\n  {pad}      {doc.path}: "
            f"{len(doc.versions)} version(s): {shown}")


def print_plan(plans: list[RepoPlan], args: argparse.Namespace) -> None:
    for p in plans:
        log(f"\n{p.repo}  (ref {p.ref}, mode {args.mode})")
        print_tree(p, p.folders[""], 0, planned_titles(p))
    folders = sum(len(p.folders) for p in plans)
    docs = sum(len(p.docs) for p in plans)
    versions = sum(len(d.versions) for p in plans for d in p.docs)
    per_update = 2 if args.assume_v2 else 1
    calls = 2 + folders * 4 + docs * 2 + (versions - docs) * per_update
    pace = (f"~{calls / args.calls_per_hour * 60:.0f} min at {args.calls_per_hour} calls/h"
            if args.calls_per_hour else "no client-side throttle")
    log(f"\n{folders} folder page(s), {docs} file page(s), {versions} version(s). First full sync = about "
        f"{calls} MCP calls ({pace}). Later runs only push new versions.")


def _preview(text: str, limit: int = 500) -> str:
    flat = text.replace("\n", " ⏎ ")
    return flat if len(flat) <= limit else f"{flat[:limit]} … ({len(text)} chars in total)"


def find_body(data: Any) -> tuple[str, str] | None:
    """Where the page body is in a read response: (field path, text). The body is a "body" field
    holding the text, or holding it in "value" one or two levels down (body.value,
    body.storage.value). None when the response has no such field."""
    queue: list[tuple[str, Any]] = [("", data)]
    while queue:
        path, node = queue.pop(0)
        if isinstance(node, list):
            queue.extend((f"{path}[{i}]", v) for i, v in enumerate(node))
            continue
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if k == "body":
                if isinstance(v, str):
                    return p, v
                if isinstance(v, dict):
                    holders = [(p, v)] + [(f"{p}.{n}", x) for n, x in v.items() if isinstance(x, dict)]
                    for hp, h in holders:
                        if isinstance(h.get("value"), str):
                            return f"{hp}.value", h["value"]
            queue.append((p, v))
    return None


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\\*_`>]", "", text)).strip().lower()


def banner_traces(text: str) -> bool:
    """Does the text show pieces of the sync banner: the marker label or the banner's first line?"""
    first = _plain(BANNER_TEXT.split("\n", 1)[0])
    return "sync-ref" in text.lower() or bool(first and first in _plain(text))


async def probe_page(d: Dialect, page_id: str, space_key: str | None) -> bool:
    """Read one page exactly as the sync does and report what the sync needs from the response:
    the page body, the sync-ref marker, the title, the parent and (v2) the snapshot token. Writes
    nothing. Any page with content shows whether reads return page bodies; only a page the sync
    wrote shows whether its banner reads back intact. True when nothing is wrong."""
    log(f"\n=== Probe: page {page_id}, read with {d.GET} ({d.name} tools) as the sync reads it")
    res, token = await d.read(page_id)
    log(f"text content: {_preview(res.text) if res.text else '(empty)'}")
    if res.structured is None:
        log("structured content: none")
    else:
        shape = sorted(res.structured) if isinstance(res.structured, dict) else type(res.structured).__name__
        log(f"structured content: {shape}")
    if isinstance(res.data, dict):
        log(f"response fields: {', '.join(map(str, res.data))}")
    body = find_body(res.data) if res.data is not None else (("text content", res.text) if res.text else None)
    if body is None:
        log('page body: NOT FOUND (no "body" field in the response)')
    elif not body[1].strip():
        log(f"page body: {body[0]}, EMPTY")
    else:
        log(f"page body: {body[0]}, {len(body[1])} chars: {_preview(body[1], 200)}")

    problems: list[str] = []
    struct_dump = json.dumps(res.structured, ensure_ascii=False) if res.structured is not None else ""
    in_text, in_struct = MARKER_RE.search(res.text or ""), MARKER_RE.search(struct_dump)
    m = in_text or in_struct
    if m:
        where = "the text content" if in_text else "the structured content only"
        banner_state = "current banner" if banner_ok(m) else "older banner wording (the next check updates it)"
        log(f"sync-ref marker: found in {where}: commit {m.group(1)[:12]}, page {m.group(3)}, {banner_state}")
    elif body is None:
        log("sync-ref marker: NOT FOUND, and the response has no page body")
        problems.append("reads do not return the page body, so the sync cannot see banners: banner "
                        "restores and --adopt never run")
    elif not body[1].strip():
        log("sync-ref marker: none (the page body came back empty)")
    elif banner_traces(res.blob()):
        log("sync-ref marker: NOT FOUND, but the body shows sync banner text")
        problems.append("the page has a sync banner whose sync-ref marker does not read back intact")
    else:
        log("sync-ref marker: none, as expected on a page the sync did not write")
    if d.needs_snapshot:
        log(f"snapshot token (needed for each v2 update): {'found' if token else 'NOT FOUND'}")
        if not token:
            problems.append("no snapshot token in the response: v2 updates need one")
    title = next((t for x in res.dicts() if isinstance(t := x.get("title"), str)), None)
    log(f"title: {title!r}" if title else "title: NOT FOUND in the response")
    parent = next((x[k] for x in res.dicts() for k in PARENT_PARAMS if x.get(k) not in (None, "")), None)
    log(f"parent page: {parent}" if parent is not None else
        "parent page: NOT FOUND in the response (the sync then only moves a page when its file moves)")
    searched = bool(title and space_key)
    if title and space_key:
        found, waited = await d.find_page(space_key, title), 0
        for delay in (2, 4, 8, 16, 30):  # a page created moments ago may not be indexed yet
            if found == page_id:
                break
            await asyncio.sleep(delay)
            waited += delay
            found = await d.find_page(space_key, title)
        lag = f" (after {waited} s: search index lag)" if waited and found == page_id else ""
        log(f"search by title in space {space_key} ({d.SEARCH}): "
            + (f"returns this page{lag}" if found == page_id
               else f"returns {found!r}, expected {page_id}, even after {waited} s"))
        if found != page_id:
            problems.append(f"{d.SEARCH} does not find the page by its title in space {space_key}")
    out = f"rovo-probe-{page_id}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"tool": d.GET, "text": res.text, "structured": res.structured}, fh, indent=1, ensure_ascii=False)
    log(f"full response saved to {out}")
    if problems:
        log("RESULT: PROBLEM: " + "; ".join(problems))
        return False
    if m:
        log("RESULT: OK, the sync can read this page")
        return True
    if body is None or not body[1].strip():
        log("RESULT: INCONCLUSIVE: this page is empty, so the read cannot show whether page bodies come "
            "back. Probe a page that has content")
        return False
    log("RESULT: OK for this page: reads return the page body" + (" and search finds the page" if searched else "")
        + ". The sync did not write it, so the banner round-trip is not tested yet: after the first sync, "
          "rerun with --page-id of a page the sync created")
    return True


def readiness(rovo: RovoMCP, d: Dialect, args: argparse.Namespace) -> bool:
    """What the sync calls on this endpoint, and what is missing. No tool calls. True when the
    sync can run with the given options."""
    log(f"\n=== What the sync uses on this endpoint ({d.name} tools)")
    ok = True

    def line(what: str, text: str) -> None:
        log(f"  {what:<15}{text}")

    for what, tool, write_ in (("read pages", d.GET, False), ("find by title", d.SEARCH, False),
                               ("create pages", d.CREATE, True), ("add versions", d.UPDATE, True)):
        if not rovo.reachable(tool, write_):
            line(what, f"{tool}: NOT EXPOSED, the sync cannot run (check the token scopes and the "
                       f"admin's Rovo MCP permission groups)")
            ok = False
            continue
        props, notes = rovo.props(tool), []
        if tool not in rovo.tools:
            notes.append("through an execute tool")
        if d.name == "v1" and tool != d.SEARCH:  # what V1.read / create / update send
            sends = "contentFormat" in props if tool == d.GET else rovo.accepts(tool, "contentFormat")
            notes.append("markdown" if sends else "no contentFormat: server default format")
        if tool == d.CREATE and props and "versionMessage" not in props:
            notes.append("no version message: a page's first version shows its tag/commit in the banner only")
        if tool == d.UPDATE:
            notes.append("version message" if rovo.accepts(tool, "versionMessage")
                         else "no versionMessage: git tag/commit/author only in the page banner")
            notes.append("title: renamed files and assigned ADR ids retitle their page" if d.can_retitle()
                         else "no title: a page keeps its first title (renames, ADR ids)")
        line(what, tool + (f" ({', '.join(notes)})" if notes else ""))
    if rovo.reachable(d.UPDATE, True):
        param = d.parent_param()
        line("move pages", f"{d.UPDATE} {param}: the page of a file moved to another folder follows it" if param
             else f"{d.UPDATE} takes no parent id: the page of a file moved to another folder stays under "
                  f"the old folder page")
    if args.cloud_id:
        line("site", f"--cloud-id {args.cloud_id}")
    elif rovo.reachable("getAccessibleAtlassianResources"):
        line("site", "getAccessibleAtlassianResources (first accessible site; --cloud-id chooses one)")
    else:
        line("site", "NOT RESOLVABLE: getAccessibleAtlassianResources is not exposed; pass "
                     "--cloud-id https://<your-site>.atlassian.net")
        ok = False
    if site_of(args.cloud_id):
        line("page links", f"{site_of(args.cloud_id)}/wiki/spaces/<key>/pages/<id>")
    elif rovo.reachable("getAccessibleAtlassianResources"):
        line("page links", "site URL from getAccessibleAtlassianResources")
    else:
        line("page links", "no site URL: ADR index, metadata and folder pages list titles and ids without "
                           "links; pass --cloud-id https://<your-site>.atlassian.net")
    if rovo.reachable(d.SPACES):
        line("space id", f"{d.SPACES} (--space-id skips the lookup)")
    else:
        line("space id", f"{d.SPACES} is not exposed: read from a page of the space ({d.SEARCH} + "
                         f"{d.GET}); --space-id skips the lookup")
    return ok


async def inspect(args: argparse.Namespace) -> int:
    async with RovoMCP(args.endpoint, auth_headers(), 0) as rovo:
        log(f"{len(rovo.tools)} tools exposed by {args.endpoint}:")
        log("  " + ", ".join(sorted(rovo.tools)))
        try:
            d = pick_dialect(rovo, args.cloud_id or "")
            log(f"\nDialect: {d.name}")
            names = [d.CREATE, d.UPDATE, d.GET, d.SEARCH]
        except RovoError as exc:
            log(f"\n{exc}")
            return 2
        for name in [*names, "getAccessibleAtlassianResources", "executeRead", "executeWrite", "execute"]:
            if name in rovo.tools:
                log(f"\n--- {name}\n{json.dumps(rovo.tools[name], indent=1)}")
        ok = readiness(rovo, d, args)
        if not (args.space_key or args.page_id):
            return 0 if ok else 1
        try:
            d.cid, _ = await resolve_site(rovo, args.cloud_id)
        except (RovoError, ConfigError) as exc:
            log(f"  cannot test the space or the page: {exc}")
            return 1
        if args.space_key:
            try:
                sid = await d.space_id(args.space_key)
                log(f"  space {args.space_key}: id {sid} ({d.space_source})")
            except RovoError as exc:
                log(f"  space {args.space_key}: NOT RESOLVED: {exc}")
                ok = False
        if args.page_id:
            try:
                ok = await probe_page(d, args.page_id, args.space_key) and ok
            except RovoError as exc:
                log(f"RESULT: PROBLEM: {exc}")
                ok = False
    return 0 if ok else 1


async def run(args: argparse.Namespace) -> int:
    if args.inspect:
        return await inspect(args)
    plans = [build_plan(r, args, args.github_repo) for r in args.repo]
    if args.dry_run:
        for plan in plans:
            for note in plan.notes:
                warn(note)
        print_plan(plans, args)
        return 0

    with run_lock(args.lock_file):
        state = load_state(args.state_file)
        failures = 0
        async with RovoMCP(args.endpoint, auth_headers(), args.calls_per_hour) as rovo:
            cloud_id, site = await resolve_site(rovo, args.cloud_id)
            dialect = pick_dialect(rovo, cloud_id)
            if args.space_id:
                space_id, how = args.space_id, "--space-id"
            elif state.get(f"space:{args.space_key}"):
                space_id, how = state[f"space:{args.space_key}"], "state file"
            else:
                space_id = await dialect.space_id(args.space_key)
                how = dialect.space_source
            state[f"space:{args.space_key}"] = space_id
            log(f"Rovo MCP {dialect.name} tools · site {cloud_id} · space {args.space_key} ({space_id}, {how})")
            if not site:
                warn("the site URL is unknown, so the ADR index, ADR metadata and folder pages show titles and "
                     "ids without links: pass --cloud-id https://<your-site>.atlassian.net")
            ctx = Ctx(args, rovo, dialect, space_id, state, site)
            for plan in plans:
                log(f"\n== {plan.repo} ({plan.ref}, {len(plan.docs)} docs in {len(plan.folders)} folder(s), "
                    f"mode {args.mode})")
                for note in plan.notes:
                    warn(note)
                failures += await sync_repo(ctx, plan)
            failures += await apply_rewrites(ctx)
            report_blocked(state)
            save_state(args.state_file, state)
            log(f"\nDone: {ctx.pushed} version(s) pushed, {rovo.calls} MCP calls, {failures} failure(s).")
    return 1 if failures else 0


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def _finite_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"must be a finite number, got {text!r}")
    return value


SPACE_KEY_RE = re.compile(r"~?[A-Za-z0-9][A-Za-z0-9:_-]*")  # personal spaces: ~accountId
GH_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def validate_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject bad values before anything runs (argparse exits with status 2)."""
    url = urlparse(args.endpoint)
    if not url.hostname or url.scheme not in ("https", "http") or (
            url.scheme == "http" and url.hostname not in LOCAL_HOSTS):
        ap.error(f"--endpoint must be an https:// URL (http:// only for localhost), got {args.endpoint!r}; "
                 f"the credentials travel in its headers")
    if args.space_key is not None and not SPACE_KEY_RE.fullmatch(args.space_key):
        ap.error(f"--space-key {args.space_key!r} is not a Confluence space key")
    for opt in ("space_id", "parent_id", "page_id"):
        value = getattr(args, opt)
        if value is not None and not value.isdigit():
            ap.error(f"--{opt.replace('_', '-')} must be a numeric id, got {value!r}")
    if args.github_repo is not None and not GH_SLUG_RE.fullmatch(args.github_repo):
        ap.error(f"--github-repo must look like org/name, got {args.github_repo!r}")
    if args.github_repo and len(args.repo) > 1:
        ap.error("--github-repo only applies to a single --repo")
    for repo in args.repo:
        if not os.path.isdir(repo):
            ap.error(f"--repo {repo!r} is not a folder")
    for opt in ("branch", "tag_pattern"):
        value = getattr(args, opt)
        if value is not None and (not value or value.startswith("-") or any(c.isspace() for c in value)):
            ap.error(f"--{opt.replace('_', '-')} {value!r} is not a valid git name")
    for name in args.adr_folder:
        if not name or "/" in name or name in (".", ".."):
            ap.error(f"--adr-folder {name!r} is not a folder name")
    for name in args.templates_folder:
        if not name or "/" in name or name in (".", ".."):
            ap.error(f"--templates-folder {name!r} is not a folder name")
    for name in args.transform_env:
        if not ENV_NAME_RE.fullmatch(name):
            ap.error(f"--transform-env {name!r} is not an environment variable name")
    if args.transform_shell and not args.transform_cmd:
        ap.error("--transform-shell needs --transform-cmd")
    if args.page_id and not args.inspect:
        ap.error("--page-id goes with --inspect")
    if not (args.inspect or args.dry_run or args.space_key):
        ap.error("--space-key is required")
    args.transform = None
    if args.transform_cmd:
        argv = None
        if not args.transform_shell:
            try:
                argv = shlex.split(args.transform_cmd)
            except ValueError as exc:
                ap.error(f"--transform-cmd: {exc}")
            if not argv:
                ap.error("--transform-cmd is empty")
            if not shutil.which(argv[0]):
                ap.error(f"--transform-cmd: {argv[0]!r} is not an executable on PATH")
        args.transform = Transform(args.transform_cmd, argv, frozenset(args.transform_env))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_argument_group("source (git)")
    src.add_argument("--repo", action="append", help="local clone with full history and tags (repeatable; default .)")
    src.add_argument("--github-repo", help="org/name for links (default: from the origin remote)")
    src.add_argument("--branch", help="branch to follow (default: origin/HEAD)")
    src.add_argument("--mode", choices=["tags", "commits"], default="tags",
                     help="tags: one version per release tag (default); commits: one per first-parent commit")
    src.add_argument("--tag-pattern", default="*", help="only tags matching this glob, e.g. 'v*'")
    src.add_argument("--include-head", action="store_true", help="tags mode: also publish the branch tip after the last tag")
    src.add_argument("--include", action="append", help=f"glob of files to publish (repeatable; default {DEFAULT_INCLUDE}); "
                                                        "*.md also matches .MD")
    src.add_argument("--exclude", action="append", default=[], help="glob of files to skip (repeatable)")
    src.add_argument("--max-versions", type=_non_negative_int, default=0,
                     help="keep only the newest N versions per file (0 = all)")
    src.add_argument("--adr-folder", action="append", metavar="NAME",
                     help="name of a folder holding ADRs: its Markdown files whose front matter has an id or "
                          "status key are ADRs (metadata table, \"<id> - <H1 title>\" page title, placeholder "
                          f"warnings), and its page holds the ADR index (repeatable; default {DEFAULT_ADR_FOLDERS})")
    src.add_argument("--templates-folder", action="append", metavar="NAME",
                     help="name of a folder holding templates: each of its Markdown files gets a page showing "
                          "its front matter as YAML, and is never an ADR (repeatable; default "
                          f"{DEFAULT_TEMPLATE_FOLDERS}). Publish another folder name with --include too")
    dst = ap.add_argument_group("destination (Confluence via Rovo MCP)")
    dst.add_argument("--space-key", help="Confluence space key, e.g. KB")
    dst.add_argument("--space-id", help="numeric space id (skips the lookup)")
    dst.add_argument("--parent-id", help="page under which each repository's root page is created")
    dst.add_argument("--cloud-id", help="cloudId or site URL, e.g. https://acme.atlassian.net (the URL form also "
                                        "gives the links between pages their site)")
    dst.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help=f"Rovo MCP endpoint (default {DEFAULT_ENDPOINT})")
    dst.add_argument("--calls-per-hour", type=_non_negative_int, default=DEFAULT_CALLS_PER_HOUR,
                     help=f"client-side throttle (default {DEFAULT_CALLS_PER_HOUR}, under the Free plan's 500 "
                          "MCP calls/hour; Standard allows 1000, Premium/Enterprise 1000 + 20 per user up to "
                          "10,000). 0 disables the throttle: only for tests against a local server")
    dst.add_argument("--state-file", help="JSON cache of page ids and synced commits (saves MCP calls)")
    dst.add_argument("--verify-hours", type=_finite_float, default=24,
                     help="re-read pages that are already up to date at most every N hours and restore a "
                          "missing or outdated banner (default 24; 0 = every run; negative = never). "
                          "Without --state-file every run reads every page anyway")
    dst.add_argument("--adopt", action="store_true",
                     help="take over a page without sync banner that has the title of a file or folder page and "
                          "sits where that page belongs, once page reads are proven (restores the banner of a "
                          "mirrored page when the state file was lost)")
    dst.add_argument("--transform-cmd",
                     help="command: page markdown on stdin -> page markdown on stdout. Run as an argument "
                          "list without a shell, in an empty temporary folder, without this script's "
                          "credentials or variables named *TOKEN*, *SECRET*, *PASSWORD*, *PRIVATE*. "
                          "Must come from a trusted source (see Safety above)")
    dst.add_argument("--transform-shell", action="store_true",
                     help="run --transform-cmd through the shell (pipes, redirections); only for trusted commands")
    dst.add_argument("--transform-env", action="append", default=[], metavar="NAME",
                     help="pass this environment variable to the transform even if it looks secret, "
                          "e.g. CLAUDE_CODE_OAUTH_TOKEN (repeatable)")
    run_ = ap.add_argument_group("run")
    run_.add_argument("--dry-run", action="store_true", help="print the plan, no network")
    run_.add_argument("--assume-v2", action="store_true", help="dry-run estimate for v2 tools (2 calls per update)")
    run_.add_argument("--inspect", action="store_true", help="list the tools and schemas the endpoint exposes")
    run_.add_argument("--page-id", help="with --inspect: read this page as the sync does and report what "
                                        "the response contains (writes nothing; add --space-key to test search). "
                                        "Use a page the sync created to test that its banner reads back")
    run_.add_argument("--lock-file", default=default_lock_file(),
                      help="lock held for the whole run, so two runs on this machine never overlap. It does "
                           "not reach other machines: in GitHub Actions the workflow's concurrency group "
                           "serializes runs (default %(default)s)")
    args = ap.parse_args(argv)
    args.repo = args.repo or ["."]
    args.include = args.include or DEFAULT_INCLUDE
    args.adr_folder = args.adr_folder or DEFAULT_ADR_FOLDERS
    args.templates_folder = args.templates_folder or DEFAULT_TEMPLATE_FOLDERS
    validate_args(ap, args)
    return args


def main() -> None:
    try:
        sys.exit(asyncio.run(run(parse_args())))
    except LockBusy as exc:
        print(f"::error::{exc}" if IN_ACTIONS else f"ERROR: {exc}", file=sys.stderr)
        sys.exit(3)
    except (RovoError, GitError, ConfigError) as exc:
        print(f"::error::{exc}" if IN_ACTIONS else f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
