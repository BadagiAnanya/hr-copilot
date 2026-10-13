"""Read API over the graph. Every consumer (CLI, MCP tools, the Q&A agent, the wiki) goes through here."""
from __future__ import annotations

import difflib
import math
from collections import Counter

from .store import Store


class NotFound(LookupError):
    pass


class Graph:
    def __init__(self, store: Store | None = None):
        self.s = store or Store()

    # ---- name resolution ---------------------------------------------------------------
    def _resolve(self, type: str, name: str) -> dict:
        nodes = self.s.nodes(type)
        lowered = {n["name"].lower(): n for n in nodes} | {n["id"].split(":", 1)[1].lower(): n for n in nodes}
        key = name.strip().lower()
        if key in lowered:
            return lowered[key]
        close = difflib.get_close_matches(key, list(lowered), n=1, cutoff=0.6) or [k for k in lowered if key in k]
        if close:
            return lowered[close[0]]
        raise NotFound(f"No {type} named '{name}'. Known: {', '.join(sorted(n['name'] for n in nodes))}")

    def repo(self, name: str) -> dict:
        return self._resolve("repo", name)

    def team(self, name: str) -> dict:
        return self._resolve("team", name)

    def team_of(self, repo_id: str) -> dict | None:
        owners = self.s.into(repo_id, "OWNS")
        return owners[0] if owners else None

    # ---- org / team level --------------------------------------------------------------
    def list_teams(self) -> list[dict]:
        out = []
        for team in self.s.nodes("team"):
            repos = self.s.out(team["id"], "OWNS")
            out.append({"team": team["name"], "id": team["id"].split(":", 1)[1], "mission": team["props"].get("mission") or team["props"].get("description", ""), "repos": [r["name"] for r in repos]})
        return out

    def list_repos(self, team: str | None = None) -> list[dict]:
        repos = self.s.out(self.team(team)["id"], "OWNS") if team else self.s.nodes("repo")
        out = []
        for r in repos:
            p, owner = r["props"], self.team_of(r["id"])
            out.append({
                "repo": r["name"], "team": owner["name"] if owner else None,
                "what_it_does": p.get("one_liner") or p.get("description") or (f"({p['skipped']})" if p.get("skipped") else ""),
                "maturity": p.get("maturity", ""), "languages": list((p.get("languages") or {}).keys()), "loc": p.get("loc", 0), "url": p.get("url"),
            })
        return out

    def team_brief(self, team: str) -> str:
        node = self.team(team)
        brief = self.s.doc(node["id"], "overview")
        if brief:
            return brief
        rows = self.list_repos(node["name"])
        return f"# Team: {node['name']}\n\n{node['props'].get('description', '')}\n\n" + "\n".join(f"- **{r['repo']}** — {r['what_it_does']}" for r in rows)

    # ---- repo level --------------------------------------------------------------------
    def repo_doc(self, repo: str, level: str = "overview") -> str:
        node = self.repo(repo)
        if level not in ("overview", "hld", "lld", "readme"):
            raise NotFound("level must be one of overview, hld, lld, readme")
        doc = self.s.doc(node["id"], level)
        if doc:
            owner = self.team_of(node["id"])
            return doc + (f"\n\n_Owned by team: {owner['name']} · {node['props'].get('url')}_" if level == "overview" and owner else "")
        return self._fallback_overview(node) if level == "overview" else f"No generated {level} for {node['name']} yet — run `orgkg enrich`."

    def _fallback_overview(self, node: dict) -> str:
        p = node["props"]
        techs = ", ".join(t["name"] for t in self.s.out(node["id"], "USES"))
        return f"# {node['name']}\n\n{p.get('description') or ''}\n\nLanguages: {p.get('languages')}\nTech: {techs}\nEntry points: {p.get('entry_points')}\n\n(Not enriched yet — structure only.)"

    def repo_structure(self, repo: str) -> dict:
        node = self.repo(repo)
        comps = [{"component": c["name"], "responsibility": c["props"].get("responsibility", ""), "paths": [n["path"] for n in self.s.out(c["id"], "IMPLEMENTED_BY")]} for c in self.s.out(node["id"], "HAS_COMPONENT")]
        return {
            "repo": node["name"], "stats": {k: node["props"].get(k) for k in ("files", "code_files", "loc", "symbols", "languages", "entry_points")},
            "components": comps,
            "endpoints": [{"endpoint": e["name"], "file": e["path"]} for e in self.s.nodes("endpoint", node["name"])],
            "tech": [t["name"] for t in self.s.out(node["id"], "USES")],
            "concepts": [c["name"] for c in self.s.out(node["id"], "ABOUT")],
            "files": [f["path"] for f in self.s.nodes("file", node["name"]) if f["props"]["role"] in ("code", "doc")][:200],
        }

    def explain_path(self, repo: str, path: str, max_source: int = 6000) -> dict:
        node = self.repo(repo)
        path = path.strip().strip("/")
        target = self.s.node(f"file:{node['name']}/{path}") or self.s.node(f"mod:{node['name']}/{path}")
        if not target:
            candidates = [f["path"] for f in self.s.nodes("file", node["name"]) if path.lower() in f["path"].lower()]
            if len(candidates) == 1:
                return self.explain_path(repo, candidates[0], max_source)
            raise NotFound(f"No path '{path}' in {node['name']}." + (f" Did you mean: {', '.join(candidates[:8])}" if candidates else ""))
        out = {"repo": node["name"], "path": target["path"], "type": target["type"], "design_notes": self.s.doc(target["id"], "lld") or "(none generated)"}
        if target["type"] == "module":
            out["contains"] = [f"{c['type']}: {c['path']}" for c in self.s.out(target["id"], "CONTAINS")]
            return out
        out["symbols"] = [{"name": s["name"], **{k: v for k, v in s["props"].items() if v and k in ("kind", "line", "doc", "methods", "signature", "stereotypes")}} for s in self.s.out(target["id"], "DEFINES")]
        out["imports"] = [n["path"] for n in self.s.out(target["id"], "IMPORTS")]
        out["imported_by"] = [n["path"] for n in self.s.into(target["id"], "IMPORTS")]
        out["uses"] = [n["name"] for n in self.s.out(target["id"], "USES")]
        out["source"] = (self.s.doc(target["id"], "source") or "")[:max_source]
        return out

    # ---- cross-cutting -----------------------------------------------------------------
    def search(self, query: str, repo: str | None = None, limit: int = 8) -> list[dict]:
        repo_name = self.repo(repo)["name"] if repo else None
        return [{"repo": h["repo"], "path": h["path"], "what": h["type"], "name": h["name"], "kind": h["kind"], "snippet": h["snippet"]} for h in self.s.search(query, limit, repo_name)]

    def who_uses(self, tech: str) -> list[dict]:
        node = self._resolve("tech", tech)
        rows = self.s.db.execute(
            "SELECT n.name FROM edges e JOIN nodes n ON n.id=e.src WHERE e.dst=? AND n.type='repo' AND e.type IN ('USES','WRITTEN_IN') ORDER BY n.name", (node["id"],)
        ).fetchall()
        out = []
        for (name,) in rows:
            owner = self.team_of(f"repo:{name}")
            files = [r[0] for r in self.s.db.execute("SELECT n.path FROM edges e JOIN nodes n ON n.id=e.src WHERE e.dst=? AND n.type='file' AND n.repo=? LIMIT 6", (node["id"], name))]
            out.append({"repo": name, "team": owner["name"] if owner else None, "files": files})
        return out

    def related_repos(self, repo: str, limit: int = 5) -> list[dict]:
        """Repos that share rare technologies or concepts — the likely 'someone already solved this' links."""
        node = self.repo(repo)
        n_repos = max(1, len(self.s.nodes("repo")))
        usage = Counter(r[0] for r in self.s.db.execute("SELECT e.dst FROM edges e JOIN nodes n ON n.id=e.src WHERE n.type='repo' AND e.type IN ('USES','ABOUT')"))
        mine = {t["id"]: t["name"] for t in self.s.out(node["id"], "USES") + self.s.out(node["id"], "ABOUT")}
        scores, shared = Counter(), {}
        for other in self.s.nodes("repo"):
            if other["id"] == node["id"]:
                continue
            theirs = {t["id"] for t in self.s.out(other["id"], "USES") + self.s.out(other["id"], "ABOUT")}
            common = set(mine) & theirs
            if common:
                scores[other["name"]] = sum(math.log(1 + n_repos / usage[t]) for t in common)
                shared[other["name"]] = sorted((mine[t] for t in common), key=lambda name: usage.get(f"tech:{name}", 99))[:8]
        out = []
        for name, score in scores.most_common(limit):
            owner = self.team_of(f"repo:{name}")
            out.append({"repo": name, "team": owner["name"] if owner else None, "shared": shared[name], "score": round(score, 2)})
        return out
