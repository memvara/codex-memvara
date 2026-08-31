
"""Gates for the Codex / ChatGPT plugin.

Every file the client will read is asserted here.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import ssl
import subprocess
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
SKILL = PLUGIN / "skills" / "memvara"
HOSTED = "https://app.memvara.dev/mcp"
REPO_NAME = "memvara/codex-memvara"


def _json(path: pathlib.Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class LibraryUnreachable(Exception):
    """Neither a local checkout nor GitHub could answer. Raised, never swallowed.

    A drift check that quietly passes when it cannot look is the same as no drift check.
    This repository has already been caught by exactly that shape: `skill-sync.yml` failed
    on every scheduled run for days while nothing here went red, because the vendored copy
    and `skill.lock` stayed consistent with each other and the only thing that would have
    noticed was a scheduled job nobody read.
    """


def _trust() -> "ssl.SSLContext":
    """A context that trusts the same roots `curl` does.

    python.org's macOS build ignores the system trust store, so an unqualified `urlopen`
    raises CERTIFICATE_VERIFY_FAILED against a certificate `curl` accepts. Without this the
    drift check below does not fail on a Mac -- it *skips*, reporting the library as
    unreachable when the library is fine, which is the quiet half of the failure it was
    written to catch.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "memvara-tests"})
    with urllib.request.urlopen(request, timeout=30, context=_trust()) as resp:
        return bytes(resp.read())


def _library_bytes(sha: str, path: str) -> bytes:
    root = os.environ.get("MEMVARA_LIBRARY")
    if root:
        try:
            return subprocess.check_output(
                ["git", "-C", root, "show", f"{sha}:{path}"],
                stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            # The checkout has the sha `skill.lock` names and nothing else: CI clones the
            # library AT that sha, shallow, so the library's current HEAD is simply not an
            # object here. Falling back to the network rather than failing is what lets the
            # drift check below run on CI at all -- and it only matters when the lock is
            # stale, which is precisely when the check has something to say.
            pass
    return _fetch(f"https://raw.githubusercontent.com/memvara/memvara/{sha}/{path}")


def _library_head() -> str:
    """The library default branch's current sha, or raise `LibraryUnreachable`."""
    root = os.environ.get("MEMVARA_LIBRARY")
    if root:
        for ref in ("origin/main", "main"):
            try:
                return subprocess.check_output(
                    ["git", "-C", root, "rev-parse", ref],
                    stderr=subprocess.DEVNULL).decode().strip()
            except subprocess.CalledProcessError:
                continue
    try:
        body = _fetch("https://api.github.com/repos/memvara/memvara/commits/main")
        return str(json.loads(body)["sha"])
    except Exception as exc:
        raise LibraryUnreachable(str(exc)) from exc


def _library_skill_files(sha: str) -> "set[str]":
    """Every path under the packaged skill at `sha`, relative to it."""
    root = os.environ.get("MEMVARA_LIBRARY")
    prefix = "memvara/skills/memvara/"
    if root:
        try:
            out = subprocess.check_output(
                ["git", "-C", root, "ls-tree", "-r", "--name-only", sha,
                 "memvara/skills/memvara"], stderr=subprocess.DEVNULL).decode()
        except subprocess.CalledProcessError:
            # Not an object in this checkout -- see `_library_bytes`. Ask GitHub instead
            # of reporting the library unreachable, which would SKIP the check on the one
            # run that needed it.
            out = None
        if out is not None:
            return {line[len(prefix):] for line in out.splitlines()
                    if line.startswith(prefix)}
    try:
        tree = json.loads(_fetch(
            f"https://api.github.com/repos/memvara/memvara/git/trees/{sha}?recursive=1"))
    except Exception as exc:
        raise LibraryUnreachable(str(exc)) from exc
    return {entry["path"][len(prefix):] for entry in tree.get("tree", [])
            if entry.get("type") == "blob" and entry["path"].startswith(prefix)}


def _lock() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / "skill.lock").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


class SkillTree(unittest.TestCase):
    def test_skill_has_front_matter_and_references(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.splitlines()[0] == "---")
        named = set(re.findall(r"references/([a-z0-9-]+\.md)", text))
        self.assertTrue(named)
        for name in named:
            self.assertTrue((SKILL / "references" / name).is_file(), name)

    def test_matches_library_at_lock_sha(self) -> None:
        lock = _lock()
        self.assertEqual(lock["repo"], "memvara/memvara")
        sha = lock["sha"]
        self.assertEqual(len(sha), 40)
        for rel in ("SKILL.md", "references/hosted-mcp.md"):
            expected = _library_bytes(sha, f"memvara/skills/memvara/{rel}")
            self.assertEqual((SKILL / rel).read_bytes(), expected, rel)

    def test_the_vendored_skill_is_not_behind_the_library(self) -> None:
        """The whole tree, against the library's CURRENT default branch.

        `test_matches_library_at_lock_sha` cannot catch a stale sync and is not supposed
        to: it compares the copy against the sha the copy itself names, so a lock and a
        tree frozen together agree with each other forever. That is exactly how this repo
        shipped a skill five commits behind -- `skill-sync.yml` dying every night on a
        permission the organization pins, nothing here going red, and the agreement
        between the two stale files being the thing that hid it.

        Two deliberate choices about noise. It compares BYTES rather than shas, so the
        library moving does not fail this repository -- only the library's *skill* moving
        does, which is rare. And it compares the file SET as well, because a new reference
        file upstream is drift that a per-file comparison of the files we already have
        would never see.

        When the library cannot be reached this SKIPS rather than passes. A skip is
        visible in the run output; a pass is not, and a check that silently succeeds when
        it could not look is the failure it exists to prevent, one level up.
        """
        try:
            head = _library_head()
            upstream = _library_skill_files(head)
        except LibraryUnreachable as exc:
            raise unittest.SkipTest(
                f"library unreachable, drift NOT checked: {exc}") from exc

        self.assertTrue(upstream, "the library reported an empty skill tree")
        ours = {str(path.relative_to(SKILL))
                for path in SKILL.rglob("*") if path.is_file()}
        self.assertEqual(
            ours, upstream,
            f"the vendored skill's file set differs from the library at {head[:7]} — "
            "run scripts/sync_plugin_repos.py from the library and update skill.lock")

        drifted = []
        for rel in sorted(upstream):
            expected = _library_bytes(head, f"memvara/skills/memvara/{rel}")
            if (SKILL / rel).read_bytes() != expected:
                drifted.append(rel)
        self.assertEqual(
            drifted, [],
            f"vendored skill is behind memvara/memvara@{head[:7]}: {drifted} — "
            "sync it")


class License(unittest.TestCase):
    def test_apache(self) -> None:
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)


#: How many tools `app.memvara.dev/mcp` advertises, which is what the README's sentence is
#: about. It is NOT the core's tool count. The two are routinely different and were
#: different for most of 2026-08-25: the core went to thirteen with `memory_standing` while
#: production still ran an older core and served twelve, so this said twelve, the core
#: repository said thirteen, and both were true of their own subject.
#:
#: 0.5.0 shipped and the box was redeployed that evening, so they agree again at thirteen.
#: Verified against the endpoint rather than inferred from the release: `tools/list`
#: answers thirteen with `memory_standing` among them.
#:
#: Keep reading it as the HOSTED number even while it matches. A reader follows this
#: sentence to a server, not to a repository, and the next release separates them again for
#: however long the deploy lags. This constant is the single place to change when it does.
#: Checked against the live endpoint by `ToolCount.test_the_declared_tools_are_the_ones_
#: the_endpoint_serves`. This number is a convenience for the offline guards, NOT the
#: referent: it said 13 while the endpoint served 14, and every guard in this file was
#: green throughout, because they compared the README against this and this against
#: nothing.
HOSTED_TOOLS = (
    "memory_recall", "memory_search", "memory_neighborhood", "memory_paths",
    "memory_ask", "memory_since", "memory_standing", "memory_add", "memory_remember",
    "memory_forget", "memory_end", "memory_history", "memory_why", "memory_stats",
)
HOSTED_TOOL_COUNT = len(HOSTED_TOOLS)

#: Spelled out because that is how the sentence is written. Indexed by the count so the
#: word cannot drift from the number -- two representations of one value disagreeing is
#: the failure this guard exists to prevent.
NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
)


def _tracked(pattern: str) -> "list[pathlib.Path]":
    """Every file this repository TRACKS matching `pattern`, asked of git.

    The filesystem is the wrong referent for "files this repository owns", and the gap is
    not academic. Worktrees live at `.claude/worktrees/<name>/`, INSIDE the checkout, so
    `ROOT.rglob` from the main checkout walks into every other worktree and reads their
    files -- at whatever commits those happen to sit at -- as though they were this
    repository's. `test_no_other_count_is_stated_anywhere` failed on `main` for precisely
    that: a sibling worktree pinned at an older commit still said "Ten tools", and the
    guard reported this repository as stating a count it does not state anywhere.

    It survived because it is invisible from where the work happens. Run the suite from a
    worktree and there are no worktrees below it, so the scan is correct and green; run it
    from the main checkout and it is wrong. CI never sees it either, having no worktrees.

    **Do not fix this with `.claude` in a `set(path.parts)` denylist.** From inside a
    worktree the checkout itself sits under `.claude/worktrees/`, so every absolute path
    contains `.claude`, the filter excludes the entire repository, and the guard passes
    having read nothing. A guard that scanned zero files is indistinguishable from one
    that found nothing wrong. Filtering `path.relative_to(ROOT).parts` would be correct;
    asking git is better, because a denylist has to keep guessing the name of the next
    scratch directory somebody drops in the tree, and `_library` -- which CI checks out
    inside the repo -- is the one it already had to learn.
    """
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", pattern],
        check=True, capture_output=True, text=True).stdout
    # `ls-files` reports the INDEX, so a file deleted with `rm` rather than `git rm` is
    # still listed and every caller here reads it immediately. `rglob` could only ever
    # yield files that exist, so dropping the check would turn an unstaged deletion into
    # a FileNotFoundError naming a path the developer has already deleted -- which reads
    # as a stale cache rather than as the unstaged deletion it is.
    return [path for path in (ROOT / name for name in listed.split("\0") if name)
            if path.is_file()]


class EndpointUnreachable(Exception):
    """The hosted endpoint could not be asked. Never a pass -- the guard skips and says so."""


def _endpoint_tools() -> "list[str]":
    """`tools/list` from the LIVE hosted endpoint, in the order it returns them.

    The referent for "what does the hosted MCP serve" is the hosted MCP. Everything else
    in this file -- `HOSTED_TOOLS`, the README, the count word -- is a claim ABOUT it, and
    a guard that compares one claim against another proves only that this repository is
    self-consistent. `memvara-web` shipped exactly that: `test/tool-count.test.ts` pinned
    the site's own number and stayed green while the site said ten and the endpoint served
    twelve.

    Standard library plus `certifi` when importable, matching the plugin's own rules:
    python.org's macOS build loads zero roots from the system trust store, and Cloudflare
    answers the stdlib User-Agent with a 1010 at the edge, so both are set explicitly.
    """
    import http.client
    import ssl
    import uuid

    key = (os.environ.get("MEMVARA_API_KEY") or "").strip()
    if not key:
        path = os.path.expanduser("~/.memvara/credentials.json")
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            stored = {}
        for field in ("api_key", "key", "token"):
            if isinstance(stored.get(field), str) and stored[field].strip():
                key = stored[field].strip()
                break
    if not key:
        raise EndpointUnreachable(
            "no credential: set MEMVARA_API_KEY or run the plugin's authenticate command")

    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 -- certifi is optional, the default context is the fallback
        context = ssl.create_default_context()

    host = "app.memvara.dev"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "memvara-plugin-tests/1.0",
    }

    def call(body: dict, extra: "dict | None" = None) -> "tuple[int, dict, bytes]":
        conn = http.client.HTTPSConnection(host, timeout=20, context=context)
        try:
            conn.request("POST", "/mcp", json.dumps(body),
                         {**headers, **(extra or {})})
            reply = conn.getresponse()
            return reply.status, dict(reply.getheaders()), reply.read()
        finally:
            conn.close()

    try:
        status, got, raw = call({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "memvara-plugin-tests", "version": "1"}},
        })
    except OSError as exc:
        raise EndpointUnreachable(f"{type(exc).__name__}: {exc}") from exc
    if status != 200:
        raise EndpointUnreachable(f"initialize answered HTTP {status}: {raw[:120]!r}")
    session = next((value for name, value in got.items()
                    if name.lower() == "mcp-session-id"), None)
    if not session:
        raise EndpointUnreachable("initialize returned no mcp-session-id header")

    call({"jsonrpc": "2.0", "method": "notifications/initialized"},
         {"mcp-session-id": session})
    status, _got, raw = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                             {"mcp-session-id": session})
    if status != 200:
        raise EndpointUnreachable(f"tools/list answered HTTP {status}: {raw[:120]!r}")

    text = raw.decode("utf-8", "replace")
    # The request accepts `text/event-stream` as well as JSON. A greedy `\{.*\}` over an
    # SSE body spans from the first brace to the last ACROSS events and yields invalid
    # JSON -- which would surface here as "unreachable" and skip the guard forever, with a
    # reason line that reads like a transient network problem rather than a parser that
    # can no longer read the response. The server answers `application/json` today; this
    # is what keeps a change there from silently retiring the only check that asks it.
    payloads = [line[len("data:"):].strip()
                for line in text.splitlines() if line.startswith("data:")]
    candidates = payloads[::-1] if payloads else [text.strip()]
    body = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "result" in parsed:
            body = parsed
            break
    if body is None:
        raise EndpointUnreachable(
            f"tools/list returned nothing this can parse as a JSON-RPC result "
            f"(content looked like {'SSE' if payloads else 'a single body'}): {text[:120]!r}")
    try:
        served = [tool["name"] for tool in body["result"]["tools"]]
    except (KeyError, TypeError) as exc:
        raise EndpointUnreachable(f"tools/list was not the expected shape: {exc}") from exc
    if not served:
        raise EndpointUnreachable("tools/list returned an empty tool set")
    return served


class ToolCount(unittest.TestCase):
    """The README states how many tools the hosted endpoint has. Nothing checked it.

    It said "Ten tools" while the endpoint served twelve -- wrong before `memory_standing`
    existed, because `memory_neighborhood` and `memory_paths` had never been counted. No
    test touched the number, so it was free to rot from the day it was written.
    """

    def test_the_declared_tools_are_the_ones_the_endpoint_serves(self) -> None:
        """The only guard here that asks the SERVER. Everything else asks this file.

        `HOSTED_TOOLS` held thirteen names while `https://app.memvara.dev/mcp` served
        fourteen, and the whole class was green the entire time -- the README matched the
        tuple, the tuple matched the count word, and nothing matched the endpoint. That is
        the `memvara-web` failure repeated: a claim checked against a copy of itself.

        Names AND order, because the README asserts order and a reader reconciles it
        against the server's listing.

        SKIPS rather than passes when the endpoint cannot be asked -- no credential, no
        network. `tools/list` needs `Authorization`, so CI without a key cannot run this
        and must say so out loud. A check that silently succeeds when it could not look is
        the failure it exists to prevent, one level up.
        """
        try:
            served = _endpoint_tools()
        except EndpointUnreachable as exc:
            raise unittest.SkipTest(
                f"hosted endpoint not asked, tool set NOT checked: {exc}") from exc

        self.assertEqual(
            served, list(HOSTED_TOOLS),
            "HOSTED_TOOLS and the endpoint disagree. The endpoint is right: update the "
            "tuple, the README sentence and its list together")

    def test_the_readme_states_the_hosted_tool_count(self) -> None:
        """Stated positively: the CORRECT phrase must be present.

        The tempting spelling is "the README does not say 'ten tools'". That passes on a
        README that has stopped saying anything at all -- a rewritten sentence, a deleted
        paragraph, a digit instead of a word -- and a guard a deletion satisfies is a guard
        that has quietly stopped guarding. Requiring the right phrase means a page that no
        longer tells the reader the truth fails exactly as loudly as one that tells them
        something false.
        """
        word = NUMBER_WORDS[HOSTED_TOOL_COUNT]
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{word.capitalize()} tools", text,
                      f"the README must state the hosted tool count as "
                      f"'{word.capitalize()} tools'")

    def test_no_other_count_is_stated_anywhere(self) -> None:
        """One number, one place. A second sentence with a different word is how this rots.

        Checked across every markdown file rather than the README alone, because the next
        person to state the count will not necessarily state it where the last one did.

        `plugin/skills/` is excluded because it is not ours to edit -- it is a byte copy of
        the library's tree and `test_matches_library_at_lock_sha` requires it to stay one.
        The exclusion is not a shrug: the vendored skill DOES state a stale count right now
        ("## The ten tools", missing memory_neighborhood and memory_paths), and the fix for
        that belongs upstream in memvara/memvara, arriving here through a sync. A guard
        that failed on it would be asking this repository to correct another one, and the
        only way to make it pass would be the edit the drift test forbids.
        """
        word = NUMBER_WORDS[HOSTED_TOOL_COUNT]
        # `(?:memory\s+)?` because the store listing does not say "ten tools", it says
        # "ten MEMORY tools" -- and a pattern without it does not match, which is the
        # second half of why that sentence rotted unnoticed. Widening the file set alone
        # left this sabotage passing: the file was scanned and the regex still missed it.
        pattern = re.compile(
            r"\b(" + "|".join(w for w in NUMBER_WORDS if w != word)
            + r")\s+(?:memory\s+)?tools\b",
            re.IGNORECASE)
        # `*.json` as well as `*.md`, and asked of git rather than the filesystem.
        #
        # Markdown alone is how this repository's own store listing rotted: the
        # `interface.longDescription` in `plugin/.codex-plugin/plugin.json` -- the sentence
        # a user reads BEFORE installing -- said "Ten memory tools" and no guard covered
        # the file, so it was free to say any number. A hand-maintained list of what is
        # covered is itself unguarded.
        #
        # `git ls-files` rather than `ROOT.rglob` because worktrees live at
        # `.claude/worktrees/<name>/`, INSIDE the checkout: rglob from the main checkout
        # walks into every other worktree and reads their files, at whatever commits those
        # sit at, as though they were this repository's.
        for path in _tracked("*.md") + _tracked("*.json"):
            if "skills" in path.relative_to(ROOT).parts:
                continue
            found = pattern.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"{path} states a different tool count: {found}")


class SharedInstructions(unittest.TestCase):
    """CLAUDE.md is shared across every plugin repo, and nothing used to carry it.

    It was hand-copied and it drifted: eleven of fourteen sections were byte-identical
    across all seven repositories while a section written in one of them reached none of
    the others. The canonical is `plugin-claude.md` in the library; `skill-sync.yml`
    composes this file from it and preserves the `local:` block, because two sections
    legitimately differ per repo — a repository's own runtime facts, and hook rules that
    only one plugin needs.

    Without this guard the sync would be a tidier way to drift rather than an end to it,
    which is the objection the section it carries makes about hand-maintained copies.
    """

    BEGIN = "<!-- local: begin"
    END = "<!-- local: end -->"

    def _text(self) -> str:
        return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    def test_the_local_block_is_delimited_exactly_once(self) -> None:
        """Two of either marker and the splice takes the wrong span; none and the composer
        refuses rather than replacing this repository's sections with a placeholder.
        """
        text = self._text()
        self.assertEqual(text.count(self.BEGIN), 1)
        self.assertEqual(text.count(self.END), 1)
        self.assertLess(text.index(self.BEGIN), text.index(self.END))

    def test_the_shared_half_matches_the_library(self) -> None:
        """Compared against the LIBRARY, never against this file's own halves.

        A check that read both halves of one file would prove it internally consistent and
        nothing else — exactly how a vendored skill sat five commits behind while its own
        drift test passed.
        """
        lock = _lock()
        try:
            canonical = _library_bytes(lock["sha"], "plugin-claude.md").decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(
                f"library has no plugin-claude.md at {lock['sha'][:7]}: {exc}") from exc
        text = self._text()
        head, rest = text.split(self.BEGIN, 1)
        _, tail = rest.split(self.END, 1)
        want_head, want_tail = canonical.split("@@LOCAL@@\n", 1)
        self.assertEqual(head, want_head,
                         "text above the local block drifted — edit plugin-claude.md in "
                         "memvara/memvara, not the copy here")
        self.assertEqual(tail.lstrip("\n"), want_tail.lstrip("\n"),
                         "text below the local block drifted from plugin-claude.md")

    def test_the_local_block_holds_what_only_this_repo_knows(self) -> None:
        """Not decorative: it carries the two sections that differ per repo. A sync that
        flattened it would lose them silently — the file would still read as a complete
        CLAUDE.md, just one belonging to a different repository.
        """
        local = self._text().split(self.BEGIN, 1)[1].split(self.END, 1)[0]
        self.assertIn("Runtime facts that cost hours to find", local)
        self.assertIn("If this repo ships hooks", local)


class Hygiene(unittest.TestCase):
    def test_no_npx_in_json(self) -> None:
        """No JSON *this repo ships* may reach for npx.

        `_library` is skipped because it is not ours: CI checks the library out there, at
        `skill.lock`'s sha, so the drift test can run offline. The moment that lock moves
        to a sha where the library has an npm package, an unfiltered scan reads
        `_library/npm/memvara/package.json` -- whose description legitimately begins "npx
        memvara" -- and fails a sync PR for a string in another repository. That is not
        hypothetical: it happened in claude-memvara on 2026-08-25, and this lock bump is
        the one that would have done it here.

        The scan stays repo-wide rather than narrowing to `plugin/`: the rule is about
        anything shipped from here, and an allowlist of directories stops covering the
        next one added.
        """
        for path in ROOT.rglob("*.json"):
            if {"node_modules", "_library"} & set(path.parts):
                continue
            self.assertNotIn("npx", path.read_text(encoding="utf-8"), path)

    def test_no_hooks(self) -> None:
        self.assertFalse((PLUGIN / "hooks").exists())
        self.assertFalse((PLUGIN / ".app.json").exists())
        self.assertFalse((PLUGIN / "commands").exists())

    def test_github_org(self) -> None:
        env = os.environ.get("GITHUB_REPOSITORY")
        if env:
            self.assertEqual(env, REPO_NAME)

class CodexManifest(unittest.TestCase):
    def test_codex_plugin_json(self) -> None:
        body = _json(PLUGIN / ".codex-plugin" / "plugin.json")
        assert isinstance(body, dict)
        self.assertEqual(body["name"], "memvara")
        self.assertEqual(body["version"], Version.VERSION)
        self.assertEqual(body["skills"], "./skills/")
        self.assertEqual(body["mcpServers"], "./.mcp.json")
        self.assertEqual(body["interface"]["privacyPolicyURL"], "https://memvara.dev/privacy")
        self.assertEqual(body["repository"], f"https://github.com/{REPO_NAME}")

    def test_marketplace_policy(self) -> None:
        body = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
        assert isinstance(body, dict)
        p = body["plugins"][0]
        self.assertEqual(p["source"]["path"], "./plugin")
        self.assertEqual(p["policy"]["installation"], "AVAILABLE")
        self.assertEqual(p["policy"]["authentication"], "ON_INSTALL")
        self.assertEqual(p["category"], "Productivity")

    def test_legacy_claude_marketplace(self) -> None:
        body = _json(ROOT / ".claude-plugin" / "marketplace.json")
        assert isinstance(body, dict)
        self.assertEqual(body["plugins"][0]["source"], "./plugin")

    def test_mcp_oauth_resource(self) -> None:
        server = _json(PLUGIN / ".mcp.json")["mcpServers"]["memvara"]
        self.assertEqual(server["url"], HOSTED)
        self.assertEqual(server["type"], "http")
        self.assertEqual(server["oauth_resource"], HOSTED)
        self.assertNotIn("command", server)

    def test_readme(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("codex plugin marketplace add memvara/codex-memvara", text)
        self.assertIn(HOSTED, text)
        self.assertIn("chatgpt", text.lower())
        self.assertNotIn("npx ", text)

    def test_plugin_tree(self) -> None:
        allowed = {
            pathlib.Path(".codex-plugin") / "plugin.json",
            pathlib.Path(".mcp.json"),
        }
        for path in SKILL.rglob("*"):
            if path.is_file():
                allowed.add(path.relative_to(PLUGIN))
        found = {p.relative_to(PLUGIN) for p in PLUGIN.rglob("*") if p.is_file()}
        self.assertFalse(found - allowed, found - allowed)


class Version(unittest.TestCase):
    """Every version this repository states must be the same one, and none may hide.

    Five skill syncs shipped under 0.1.0. The vendored skill is the whole of what a client
    receives here, it changed five times, and the string a client compares never moved.
    `claude-memvara` was caught by the identical shape at larger scale -- twenty-one
    commits on main behind an unchanged version, `/plugin update` answering "already at
    the latest version" for every one of them.

    Three deliberate choices, each of them paid for by a sabotage run.

    Files are found by walking the tree, not by reading a list, so a manifest nobody
    remembered cannot go unchecked. `DECLARED` is then the completeness half -- it names
    the manifests that MUST carry a version, and it is compared against the walk in both
    directions, which is what keeps a hand-written list from quietly narrowing coverage.

    The file set comes from `git ls-files`, not from the filesystem. Two sweeps of the
    tree were tried first and both were wrong in a way a passing run could not show: one
    ignored directories by absolute path, which excluded the entire repository whenever the
    checkout was a worktree (those live under `.claude/worktrees/`, so `.claude` was in the
    parts of every path); the next was caught by CI dragging in six manifests from the
    library checkout under `_library/`. Git already knows which files this repository owns.

    And the assertions demand presence rather than absence of the wrong value. The
    coverage check was first written as a bare set comparison and passed on that broken
    walk because both sides were empty; the value check alone still passes when one
    manifest of several drops its version entirely. A guard an absence satisfies has
    stopped guarding.
    """

    VERSION = "0.2.4"
    DECLARED = {
        'plugin/.codex-plugin/plugin.json',
    }

    @classmethod
    def _walk(cls, node: object, where: str = ""):
        """Every `version` string at any depth, with the pointer that reached it."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "version" and isinstance(value, str):
                    yield f"{where}.{key}", value
                else:
                    yield from cls._walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from cls._walk(value, f"{where}[{index}]")

    @classmethod
    def _candidates(cls) -> list:
        """Every JSON file this repository TRACKS -- asked of git, not of the filesystem.

        The filesystem is the wrong referent. CI checks the library out into `_library/`,
        which carries the sibling plugins' own manifests, and an `rglob` swept all six into
        the walk; a denylist would then have to grow a name for every scratch directory
        anyone ever creates, and the first one nobody thought of is a false failure. What
        the question actually means is "files this repository owns", and git is the thing
        that knows. Untracked checkouts and nested worktrees fall out for free.

        No fallback when git cannot answer. A fallback here would silently cover less than
        the caller believes, which is the failure this whole class exists to prevent.
        """
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "*.json"],
            check=True, capture_output=True, text=True).stdout
        return [
            ROOT / name for name in listed.split("\0")
            if name and pathlib.PurePath(name).name != "package-lock.json"
        ]

    def _stated(self) -> list:
        found = []
        for path in self._candidates():
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            found.extend((path, where, value) for where, value in self._walk(body))
        return found

    def test_every_version_this_repo_states_is_the_released_one(self) -> None:
        stated = self._stated()
        self.assertTrue(
            stated, "no file states a version at all -- this guard has stopped guarding")
        for path, where, value in stated:
            self.assertEqual(
                value, self.VERSION,
                f"{path.relative_to(ROOT)}{where} says {value!r}; a partial bump is how a "
                "client gets told it is current while the contents moved underneath it")

    def test_exactly_the_manifests_that_must_declare_a_version_do(self) -> None:
        """Both directions, because each catches a mistake the other cannot see.

        A file the walk misses is a version nobody checks. A file that has stopped
        declaring one is a manifest shipping unversioned -- invisible to the value check
        above, which goes green as soon as any other file still says the right thing.
        Confirmed by sabotage: deleting the key from one of three manifests left it green.
        """
        reached = {str(path.relative_to(ROOT)) for path, _where, _value in self._stated()}
        by_text = {
            str(path.relative_to(ROOT)) for path in self._candidates()
            if '"version"' in path.read_text(encoding="utf-8")
        }
        self.assertEqual(by_text, self.DECLARED, "a manifest gained or lost its version")
        self.assertEqual(reached, self.DECLARED, "the JSON walk missed a stated version")

    def test_the_release_number_is_written_down_exactly_once_in_this_suite(self) -> None:
        """`VERSION` above is the only place the tests name it.

        Ported from claude-memvara, which learned it the same way this repository just
        did: another test asserted the release literally, so a bump had to be applied in
        two places and one of them was missed. Every extra place is the mechanism a
        partial bump needs, and a partial bump is what tells a client it is current while
        the contents moved underneath it.

        The duplicates that prompted this now read `Version.VERSION` instead, which is
        why they no longer count.
        """
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        self.assertEqual(
            source.count(f'"{self.VERSION}"'), 1,
            f"{self.VERSION} appears more than once in this file; VERSION is meant to be "
            "the single place the suite states the release")


def _readme_prose(root: pathlib.Path) -> str:
    """The README with every run of whitespace collapsed to one space.

    Prose wraps, and where it wraps is not a fact about what it says. Matching the raw
    text pinned a line break: reflowing a paragraph turned a guard red while the sentence
    it guards was present and correct, and the cheapest way out of that is to delete the
    guard. It matters for the negative assertion too -- a claim reintroduced with a
    different wrap would slip past `assertNotIn` on the raw text.
    """
    return " ".join(root.joinpath("README.md").read_text(encoding="utf-8").split())


class ModuleShape(unittest.TestCase):
    """Nothing may be defined below `unittest.main()`.

    Measured, not imagined: `AuthScript` was appended to the end of this file, after the
    `__main__` block. Under `unittest discover` the module is imported, the block does not
    run, and every test is collected. Run directly -- `python3 test/test_plugin.py`, the
    obvious way to check one file -- `unittest.main()` executes before the class exists
    and five guards silently do not run. Both invocations printed `OK`: 26 tests one way
    and 21 the other, with nothing in the output saying so.

    That is this repository's signature failure in miniature, so it gets a guard rather
    than a fixed comment: a passing run must not be able to mean "the check never ran".
    """

    def test_nothing_is_defined_after_the_main_block(self) -> None:
        import ast

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        body = ast.parse(source).body
        guards = [i for i, node in enumerate(body)
                  if isinstance(node, ast.If) and "__main__" in ast.dump(node.test)]
        self.assertEqual(len(guards), 1, "expected exactly one __main__ block")
        after = [type(node).__name__ for node in body[guards[0] + 1:]]
        self.assertEqual(
            after, [],
            f"{after} is defined after `unittest.main()`, so `python3 test/test_plugin.py` runs "
            "without it and still prints OK")


class AuthScript(unittest.TestCase):
    """The skill ships the device-code flow, because this host has nowhere else to put it.

    Codex plugins cannot ship slash commands: `commands` is not a field the manifest
    accepts, and the `validate_plugin.py` shipped with the binary rejects it with the same
    message it gives a field invented on the spot. What Codex does load is the skill, and
    the skill was measured to resolve a path relative to its own directory before anything
    was built on that -- a probe skill whose SKILL.md held no nonce and pointed at a
    sibling file came back with the nonce, and came back `NO PROBE` with the registration
    removed and every file still on disk.

    So the script arrives here the way every other skill byte does: vendored whole from
    memvara/memvara by skill-sync.yml, diffed against `skill.lock`. These tests do not
    re-check the bytes -- `SkillTree` already does, against the library rather than
    against a copy of this repository's own claim. They check the two things that vendoring
    cannot: that the file is actually here, and that a person is told it exists.
    """

    SCRIPT = SKILL / "scripts" / "memvara_auth.py"
    COMMANDS = ("authenticate", "login", "logout", "stats")

    def test_the_skill_ships_the_auth_script(self) -> None:
        """Stated positively, because the failure to catch is a deletion.

        Spelled "no unexpected file in the skill tree" this would pass on a plugin that
        had stopped shipping the one file a locked-out user needs.
        """
        self.assertTrue(
            self.SCRIPT.is_file(),
            f"{self.SCRIPT.relative_to(ROOT)} is missing; the README tells the user it "
            "is there and the skill tells the model to run it")

    def test_the_script_runs_here_and_names_every_command(self) -> None:
        """Executed rather than read. A vendored file with a syntax error is invisible to
        a byte diff against the library -- both copies are equally broken and agree.

        No network: an unknown command is refused on shape before anything is dialled.
        """
        done = subprocess.run(
            [sys.executable, str(self.SCRIPT), "not-a-command"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for command in self.COMMANDS:
            self.assertIn(command, done.stdout,
                          f"the usage this prints omits {command}")

    def test_the_readme_says_the_script_is_here_and_where(self) -> None:
        """A capability nobody is told about is one nobody uses.

        The path is asserted and then RESOLVED, so a README that names a plausible-looking
        path into the wrong directory fails here rather than sending someone to a file
        that is not there.
        """
        text = _readme_prose(ROOT)
        quoted = "skills/memvara/scripts/memvara_auth.py"
        self.assertIn(quoted, text,
                      "the README never mentions the auth script, so the only way to "
                      "find it is to read the skill")
        self.assertTrue((PLUGIN / quoted).is_file(),
                        f"the README says {quoted}, and nothing is there")
        self.assertIn("no `pip install`", text,
                      "the README does not say the script needs nothing installed, "
                      "which is the reason it can rescue a locked-out machine")

    def test_the_readme_says_this_host_has_no_slash_commands(self) -> None:
        """The reduced port, stated in the shipped artifact rather than in a plan.

        `claude-memvara` and `grok-memvara` ship `/memvara authenticate`. This host cannot,
        and a user who has read about those commands and cannot find them here needs to
        learn why from the README rather than conclude the plugin is broken. Asserted
        positively -- the sentence must be PRESENT -- so deleting the explanation fails
        exactly as loudly as never writing it.
        """
        text = _readme_prose(ROOT)
        self.assertIn("cannot ship slash commands", text)
        self.assertIn("/memvara", text,
                      "the section does not name the thing the user went looking for")

    def test_the_readme_no_longer_promises_no_python(self) -> None:
        """It said "there is no local Python process", and now one ships.

        Stated as a requirement on the CURRENT sentence rather than as an absence of the
        old one: the README has to say what is true now, so a rewrite that deletes the
        claim entirely and explains nothing fails here too.
        """
        text = _readme_prose(ROOT)
        self.assertNotIn("no local Python process", text,
                         "the README still claims no Python ships, and a Python script "
                         "is sitting in plugin/skills/memvara/scripts/")
        self.assertIn("Nothing runs in the background", text,
                      "the README should still tell the reader nothing is left running, "
                      "which is the true half of what that sentence used to claim")


if __name__ == "__main__":
    unittest.main()
