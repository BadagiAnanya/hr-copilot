"""LLM enrichment: turn the extracted structure into explanations (overview, HLD, LLD, concepts).

Hierarchical and content-addressed:
  module digest -> module summary      (only for repos too big for one prompt)
  repo digest   -> overview / HLD / LLD
  repo summaries-> team brief
Each call is cached under a hash of (prompt version, model, input digest), so re-running after a
push only pays for the repos whose content actually changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict

from .llm import LLM, LLMRefusal
from .store import Store

PROMPT_VERSION = "v1"
CONTEXT_CHARS = int(os.environ.get("ORGKG_CONTEXT_CHARS", 60_000))  # raw-source budget per call (~15k tokens)
MAX_MODULE_CALLS = int(os.environ.get("ORGKG_MAX_MODULE_CALLS", 12))  # 0 disables the map step (tiny rate limits)
SMALL = CONTEXT_CHARS < 30_000  # constrained providers: shrink the fixed sections of the digest too
COMPACT = "\n\nSpace is limited: at most 5 components, 5 lld entries, 3 key_symbols each, one or two sentences per field, under 900 words in total." if SMALL else ""
README_CHARS = min(8_000, CONTEXT_CHARS // 3)
OUTLINE_CHARS = min(12_000, CONTEXT_CHARS // 3)

SYSTEM = """You are a staff engineer writing onboarding documentation for a colleague from another team.
You are given a machine-extracted digest of one unit of an engineering organisation (a module, a repository or a team):
its file tree, README, dependencies, HTTP endpoints, import graph, symbol outline and selected source files.

Rules:
- Ground every statement in the digest. Reference real paths and symbol names exactly as they appear.
- If something is not evident from the digest, write "not evident from the code" instead of guessing.
- Explain intent and flow (why and how things connect), not a line-by-line paraphrase.
- Be concrete and compact. No marketing language, no filler.
- Judge maturity honestly: a tutorial notebook or course exercise is "learning", not a product.
- Output exactly one JSON object matching the schema in the request."""

REPO_SCHEMA = """{
  "one_liner": "what this repo is, in <= 20 words",
  "maturity": "production-like | prototype | learning | fork",
  "overview": "2-3 short paragraphs: what problem it solves, how it is used, what is notable",
  "hld": {
    "summary": "the architecture in one paragraph",
    "components": [{"name": "", "responsibility": "", "paths": ["real file or directory paths"]}],
    "connections": [{"from": "component name", "to": "component name", "label": "what flows"}],
    "data_flow": ["ordered steps of the main end-to-end flow"],
    "external_dependencies": [{"name": "", "purpose": ""}]
  },
  "lld": [{"path": "real file or directory path", "responsibility": "", "key_symbols": [{"name": "", "role": ""}], "flow": "how the code in this unit works, step by step", "gotchas": "edge cases, limits, things a newcomer would trip on (or empty)"}],
  "interfaces": [{"kind": "http | cli | library | ui | notebook | job", "name": "", "description": ""}],
  "concepts": ["3-8 domain or engineering concepts needed to understand this repo, lower case"],
  "how_to_run": "commands or steps, only if evident",
  "onboarding": {"start_here": ["paths to read first, in order"], "questions_for_owners": ["things the code does not answer that are worth asking the owning team"]}
}"""

MODULE_SCHEMA = """{
  "responsibility": "what this module is for",
  "key_symbols": [{"name": "", "role": ""}],
  "flow": "how the code in this module works, step by step",
  "depends_on": ["other modules, files or libraries it relies on"],
  "gotchas": "edge cases or limits (or empty)"
}"""

TEAM_SCHEMA = """{
  "mission": "one sentence",
  "summary": "one paragraph on what the team builds and how its repos relate",
  "capabilities": ["things another team could rely on this team for"],
  "repo_map": [{"repo": "", "role": "why it exists within the team"}],
  "before_you_reach_out": ["what to read or know first so the conversation can start at the edge cases"],
  "shared_stack": ["main technologies"]
}"""


def _key(model: str, kind: str, digest: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{model}|{kind}|{digest}".encode()).hexdigest()


def _cached(store: Store, llm: LLM, kind: str, digest: str, schema: str) -> tuple[dict, bool]:
    key = _key(llm.model, kind, digest)
    if hit := store.cache_get(key):
        return json.loads(hit), True
    result = llm.complete_json(SYSTEM, f"{digest}\n\n---\nWrite the {kind} documentation as JSON with this schema:\n{schema}{COMPACT}")
    store.cache_put(key, json.dumps(result))
    return result, False


# ---- digests ---------------------------------------------------------------------------

def _outline(store: Store, repo: str, prefix: str = "") -> str:
    lines = []
    for f in store.nodes("file", repo):
        if prefix and not f["path"].startswith(prefix):
            continue
        syms = store.out(f["id"], "DEFINES")
        if not syms:
            continue
        lines.append(f"{f['path']}:")
        for s in syms[:25]:
            p = s["props"]
            extra = f" [{', '.join(p['stereotypes'])}]" if p.get("stereotypes") else ""
            members = f" methods: {', '.join(p['methods'][:15])}" if p.get("methods") else p.get("signature", "")
            doc = f" — {p['doc']}" if p.get("doc") else ""
            lines.append(f"  {p.get('kind', 'symbol')} {s['name']}{extra}{members}{doc}")
    return "\n".join(lines)


def _rank_files(store: Store, repo: str, prefix: str = "") -> list[dict]:
    indegree = Counter(r[0] for r in store.db.execute("SELECT e.dst FROM edges e JOIN nodes n ON n.id=e.src WHERE e.type='IMPORTS' AND n.repo=?", (repo,)))
    files = [f for f in store.nodes("file", repo) if f["path"].startswith(prefix) and f["props"]["role"] in ("code", "config")]

    def score(f: dict) -> float:
        p, path = f["props"], f["path"].lower()
        s = 3.0 * indegree[f["id"]] + (5 if p.get("entry_point") else 0) + (2 if p["role"] == "code" else 0)
        s += 3 if re.search(r"(^|/)(app|main|server|index|pipeline|train|model)[^/]*$", path) else 0
        s -= 4 if re.search(r"(^|/)(tests?|__init__|setup)\b|test", path) else 0
        s -= 6 if path.endswith((".xml", ".properties", ".lock")) else 0
        return s

    return sorted(files, key=score, reverse=True)


def _sources(store: Store, files: list[dict], budget: int) -> str:
    parts, used = [], 0
    per_file = max(2_500, budget // max(1, min(6, len(files))))
    for f in files:
        body = store.doc(f["id"], "source")
        if not body or not body.strip():
            continue
        body = body[:per_file] + ("\n… (truncated)" if len(body) > per_file else "")
        if used + len(body) > budget:
            continue
        parts.append(f"### {f['path']}\n```\n{body}\n```")
        used += len(body)
    return "\n\n".join(parts)


def repo_digest(store: Store, repo: str, module_summaries: dict[str, dict] | None = None) -> str:
    node = store.node(f"repo:{repo}")
    p = node["props"]
    files = store.nodes("file", repo)
    cap = 60 if SMALL else 250
    listed = [f for f in files if f["props"]["role"] != "config" or not f["path"].endswith(".xml")] if SMALL else files
    tree = "\n".join(f["path"] for f in listed[:cap]) + (f"\n… {len(files) - cap} more" if len(files) > cap else "")
    techs = ", ".join(t["name"] for t in sorted(store.out(node["id"], "USES"), key=lambda t: -t["eprops"].get("weight", 0))[:30])
    endpoints = "\n".join(f"{e['name']}  ({e['path']})" for e in store.nodes("endpoint", repo))
    imports = "\n".join(
        f"{r[0]} -> {r[1]}"
        for r in store.db.execute(
            "SELECT s.path, d.path FROM edges e JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst WHERE e.type='IMPORTS' AND s.repo=? ORDER BY 1,2 LIMIT ?", (repo, 40 if SMALL else 150)
        )
    )
    sections = [
        f"# Repository: {repo}",
        f"GitHub description: {p.get('description') or '(none)'}\nFork of someone else's project: {p.get('fork')}\nLanguages (bytes): {p.get('languages')}\n"
        f"Size: {p.get('code_files')} code files, {p.get('loc')} lines\nEntry points: {p.get('entry_points')}",
        f"## Dependencies\n{techs or '(none detected)'}",
        f"## HTTP endpoints\n{endpoints or '(none detected)'}",
        f"## File tree\n{tree}",
        f"## Internal import graph (file -> file)\n{imports or '(none)'}",
        f"## README\n{(store.doc(node['id'], 'readme') or '(no README)')[:README_CHARS]}",
        f"## Symbol outline\n{_outline(store, repo)[:OUTLINE_CHARS] or '(none)'}",
    ]
    if module_summaries:
        sections.append("## Module summaries (written from the full source of each module)\n" + json.dumps(module_summaries, indent=1))
        sections.append("## Selected source files\n" + _sources(store, _rank_files(store, repo), CONTEXT_CHARS // 3))
    else:
        sections.append("## Source files\n" + _sources(store, _rank_files(store, repo), CONTEXT_CHARS))
    return "\n\n".join(sections)


def _module_groups(store: Store, repo: str) -> dict[str, int]:
    """Top-level code areas and their source size, used to decide whether to map-reduce."""
    sizes: dict[str, int] = defaultdict(int)
    for f in store.nodes("file", repo):
        if f["props"]["role"] != "code":
            continue
        body = store.doc(f["id"], "source") or ""
        parts = f["path"].split("/")
        sizes["/".join(parts[:-1]) + "/" if len(parts) > 1 else f["path"]] += len(body)
    return dict(sizes)


def module_digest(store: Store, repo: str, prefix: str) -> str:
    files = _rank_files(store, repo, prefix)
    return "\n\n".join([
        f"# Module `{prefix}` of repository {repo}",
        f"## Symbol outline\n{_outline(store, repo, prefix)[:OUTLINE_CHARS]}",
        f"## Source files\n{_sources(store, files, CONTEXT_CHARS)}",
    ])


# ---- applying results to the graph -----------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def render_hld(repo: str, r: dict) -> str:
    hld = r.get("hld", {})
    comps = hld.get("components", [])
    ids = {c["name"]: f"c{i}" for i, c in enumerate(comps)}
    lines = [f"# {repo} — high-level design", "", hld.get("summary", ""), ""]
    if comps:
        lines += ["```mermaid", "flowchart LR"]
        lines += [f'    {ids[c["name"]]}["{c["name"].replace(chr(34), "")}"]' for c in comps]
        for conn in hld.get("connections", []):
            if conn.get("from") in ids and conn.get("to") in ids:
                label = f'|{conn.get("label", "").replace(chr(34), "").replace("|", "/")[:40]}|' if conn.get("label") else ""
                lines.append(f"    {ids[conn['from']]} -->{label} {ids[conn['to']]}")
        lines += ["```", "", "## Components", ""]
        lines += [f"- **{c['name']}** — {c.get('responsibility', '')}" + (f" (`{'`, `'.join(c['paths'])}`)" if c.get("paths") else "") for c in comps]
    if hld.get("data_flow"):
        lines += ["", "## Main flow", ""] + [f"{i}. {step}" for i, step in enumerate(hld["data_flow"], 1)]
    if hld.get("external_dependencies"):
        lines += ["", "## External dependencies", ""] + [f"- **{d.get('name')}** — {d.get('purpose', '')}" for d in hld["external_dependencies"]]
    return "\n".join(lines)


def render_lld(repo: str, r: dict) -> str:
    lines = [f"# {repo} — low-level design", ""]
    for unit in r.get("lld", []):
        lines += [f"## `{unit.get('path')}`", "", unit.get("responsibility", ""), ""]
        if unit.get("key_symbols"):
            lines += [f"- `{s.get('name')}` — {s.get('role', '')}" for s in unit["key_symbols"]] + [""]
        if unit.get("flow"):
            lines += [f"**Flow:** {unit['flow']}", ""]
        if unit.get("gotchas"):
            lines += [f"**Watch out:** {unit['gotchas']}", ""]
    return "\n".join(lines)


def render_overview(repo: str, r: dict) -> str:
    lines = [f"# {repo}", "", f"_{r.get('one_liner', '')}_  ·  maturity: **{r.get('maturity', 'unknown')}**", "", r.get("overview", "")]
    if r.get("interfaces"):
        lines += ["", "## Interfaces", ""] + [f"- **{i.get('kind')}** `{i.get('name')}` — {i.get('description', '')}" for i in r["interfaces"]]
    if r.get("how_to_run"):
        lines += ["", "## How to run", "", r["how_to_run"]]
    ob = r.get("onboarding", {})
    if ob.get("start_here"):
        lines += ["", "## Start here", ""] + [f"{i}. `{p}`" for i, p in enumerate(ob["start_here"], 1)]
    if ob.get("questions_for_owners"):
        lines += ["", "## Open questions for the owning team", ""] + [f"- {q}" for q in ob["questions_for_owners"]]
    if r.get("concepts"):
        lines += ["", "**Concepts:** " + ", ".join(r["concepts"])]
    return "\n".join(lines)


def _valid_paths(store: Store, repo: str) -> set[str]:
    return {n["path"].rstrip("/") for n in store.nodes(repo=repo) if n["path"] and n["type"] in ("file", "module")}


def apply_repo(store: Store, repo: str, r: dict) -> dict:
    """Write an enrichment result into the graph. Paths the model cites are checked against the real tree."""
    rid, valid, bad = f"repo:{repo}", _valid_paths(store, repo), 0
    for old in store.nodes("component", repo):
        store.db.execute("DELETE FROM edges WHERE src=? OR dst=?", (old["id"], old["id"]))
        store.db.execute("DELETE FROM nodes WHERE id=?", (old["id"],))
    store.db.execute("DELETE FROM edges WHERE src=? AND type='ABOUT'", (rid,))

    def node_for(path: str) -> str | None:
        path = path.strip().rstrip("/").removeprefix("./")
        if path in valid:
            return f"file:{repo}/{path}" if store.node(f"file:{repo}/{path}") else f"mod:{repo}/{path}"
        return None

    for comp in r.get("hld", {}).get("components", []):
        kept = [p for p in comp.get("paths", []) if node_for(p)]
        bad += len(comp.get("paths", [])) - len(kept)
        comp["paths"] = kept
        cid = store.add_node(f"comp:{repo}/{_slug(comp['name'])}", "component", comp["name"], repo=repo, responsibility=comp.get("responsibility", ""))
        store.add_edge(rid, "HAS_COMPONENT", cid)
        store.set_doc(cid, "summary", f"{comp['name']}: {comp.get('responsibility', '')}", source="generated")
        for p in kept:
            store.add_edge(cid, "IMPLEMENTED_BY", node_for(p))
    for conn in r.get("hld", {}).get("connections", []):
        a, b = f"comp:{repo}/{_slug(conn.get('from', ''))}", f"comp:{repo}/{_slug(conn.get('to', ''))}"
        if store.node(a) and store.node(b):
            store.add_edge(a, "FLOWS_TO", b, label=conn.get("label", ""))
    for unit in r.get("lld", []):
        target = node_for(unit.get("path", ""))
        if not target:
            bad += 1
            continue
        text = " ".join(filter(None, [unit.get("responsibility"), unit.get("flow"), unit.get("gotchas")]))
        store.set_doc(target, "lld", text, source="generated")
    ob = r.get("onboarding", {})
    if ob.get("start_here"):
        kept = [p for p in ob["start_here"] if node_for(p)]
        bad += len(ob["start_here"]) - len(kept)
        ob["start_here"] = kept
    for concept in r.get("concepts", []):
        cid = store.add_node(f"concept:{_slug(concept)}", "concept", concept.lower())
        store.add_edge(rid, "ABOUT", cid)

    node = store.node(rid)
    store.add_node(rid, "repo", repo, repo=repo, **{**node["props"], "one_liner": r.get("one_liner", ""), "maturity": r.get("maturity", ""), "enriched": True})
    store.set_doc(rid, "overview", render_overview(repo, r), source="generated", title=repo)
    store.set_doc(rid, "hld", render_hld(repo, r), source="generated", title=repo)
    store.set_doc(rid, "lld", render_lld(repo, r), source="generated", title=repo)
    store.commit()
    return {"ungrounded_paths": bad}


def enrich_repo(store: Store, llm: LLM, repo: str) -> dict:
    groups = _module_groups(store, repo)
    module_summaries = None
    calls = cached_calls = 0
    if MAX_MODULE_CALLS and sum(groups.values()) > CONTEXT_CHARS * 1.3 and len(groups) > 1:  # too big for one prompt: map over modules, then reduce
        module_summaries = {}
        for prefix, size in sorted(groups.items(), key=lambda kv: -kv[1])[:MAX_MODULE_CALLS]:
            if size < 1500:
                continue
            module_summaries[prefix], hit = _cached(store, llm, "module", module_digest(store, repo, prefix), MODULE_SCHEMA)
            calls, cached_calls = calls + 1, cached_calls + hit
    result, hit = _cached(store, llm, "repository", repo_digest(store, repo, module_summaries), REPO_SCHEMA)
    info = apply_repo(store, repo, result)
    node = store.node(f"repo:{repo}")
    seen = min(1.0, CONTEXT_CHARS / max(1, sum(groups.values()))) if not module_summaries else 1.0
    store.add_node(node["id"], "repo", repo, repo=repo, **{**node["props"], "source_coverage": round(seen, 2), "enriched_by": llm.model})
    store.commit()
    return {**info, "coverage": round(seen, 2), "calls": calls + 1, "cached": cached_calls + hit, "map_reduce": bool(module_summaries)}


def enrich_team(store: Store, llm: LLM, team_id: str) -> None:
    team = store.node(team_id)
    blocks = []
    for repo in store.out(team_id, "OWNS"):
        p = repo["props"]
        techs = ", ".join(t["name"] for t in store.out(repo["id"], "USES")[:15])
        body = store.doc(repo["id"], "overview") or f"(no content: {p.get('skipped') or 'not enriched'})"
        blocks.append(f"## {repo['name']}\nLanguages: {p.get('languages')}\nTech: {techs}\n{body[:2500]}")
    digest = f"# Team: {team['name']}\nCharter: {team['props'].get('description', '')}\n\n" + "\n\n".join(blocks)
    r, _ = _cached(store, llm, "team", digest, TEAM_SCHEMA)
    lines = [f"# Team: {team['name']}", "", f"**Mission:** {r.get('mission', '')}", "", r.get("summary", ""), "", "## What you can rely on this team for", ""]
    lines += [f"- {c}" for c in r.get("capabilities", [])]
    lines += ["", "## Repositories", ""] + [f"- **{m.get('repo')}** — {m.get('role', '')}" for m in r.get("repo_map", [])]
    lines += ["", "## Before you reach out", ""] + [f"- {c}" for c in r.get("before_you_reach_out", [])]
    lines += ["", "**Shared stack:** " + ", ".join(r.get("shared_stack", []))]
    store.add_node(team_id, "team", team["name"], **{**team["props"], "mission": r.get("mission", "")})
    store.set_doc(team_id, "overview", "\n".join(lines), source="generated", title=team["name"])
    store.commit()


def enrich(store: Store, llm: LLM, only: str | None = None, log=print) -> None:
    for repo in store.nodes("repo"):
        name, p = repo["name"], repo["props"]
        if only and name != only:
            continue
        if p.get("skipped") or not p.get("files"):
            store.set_doc(repo["id"], "overview", f"# {name}\n\n_No analysable content ({p.get('skipped') or 'no source files'})._", source="generated", title=name)
            store.commit()
            continue
        try:
            info = enrich_repo(store, llm, name)
            log(f"  enriched {name}: {info['calls']} call(s), {info['cached']} cached, map-reduce={info['map_reduce']}, source seen={info['coverage']:.0%}, ungrounded paths dropped={info['ungrounded_paths']}")
        except LLMRefusal as exc:
            log(f"  SKIPPED  {name}: {exc}")
        except Exception as exc:  # keep going: one bad repo must not block the rest of the org
            log(f"  FAILED   {name}: {type(exc).__name__}: {str(exc)[:200]}")
    if not only:
        for team in store.nodes("team"):
            try:
                enrich_team(store, llm, team["id"])
                log(f"  team brief: {team['name']}")
            except Exception as exc:
                log(f"  FAILED   team {team['name']}: {type(exc).__name__}: {str(exc)[:200]}")
    log(f"  LLM usage ({llm.provider}/{llm.model}): {llm.usage}")
