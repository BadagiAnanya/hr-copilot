"""Static exports: a browsable markdown wiki and an interactive graph page."""
from __future__ import annotations

import json
from pathlib import Path

from .config import OUT_DIR
from .query import Graph

COLORS = {"org": "#111827", "team": "#7c3aed", "repo": "#2563eb", "component": "#0891b2", "tech": "#d97706", "concept": "#059669", "endpoint": "#dc2626"}


def export_wiki(graph: Graph, out: Path = OUT_DIR / "wiki") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    index = ["# Engineering knowledge base", "", "Generated from the knowledge graph. Start with your team, then follow the links.", ""]
    for team in graph.list_teams():
        index += [f"## [{team['team']}](teams/{team['id']}.md)", "", team["mission"], ""]
        (out / "teams").mkdir(exist_ok=True)
        (out / "teams" / f"{team['id']}.md").write_text(graph.team_brief(team["team"]) + "\n\n## Repo pages\n\n" + "\n".join(f"- [{r}](../repos/{r}.md)" for r in team["repos"]) + "\n")
        for row in graph.list_repos(team["team"]):
            index.append(f"- [{row['repo']}](repos/{row['repo']}.md) — {row['what_it_does']}")
            (out / "repos").mkdir(exist_ok=True)
            page = [graph.repo_doc(row["repo"], "overview")]
            node = graph.repo(row["repo"])
            if node["props"].get("enriched"):
                page += [graph.repo_doc(row["repo"], "hld"), graph.repo_doc(row["repo"], "lld")]
                related = graph.related_repos(row["repo"])
                if related:
                    page.append("# Related work elsewhere in the org\n\n" + "\n".join(f"- [{r['repo']}]({r['repo']}.md) ({r['team']}) — shares {', '.join(r['shared'])}" for r in related))
            (out / "repos" / f"{row['repo']}.md").write_text("\n\n---\n\n".join(page) + "\n")
        index.append("")
    (out / "index.md").write_text("\n".join(index))
    return out


def export_graph_html(graph: Graph, out: Path = OUT_DIR / "graph.html") -> Path:
    """Org -> team -> repo -> component, plus the technologies and concepts shared by 2+ repos."""
    s = graph.s
    keep = {n["id"]: n for t in ("org", "team", "repo", "component") for n in s.nodes(t)}
    for t in ("tech", "concept"):
        for n in s.nodes(t):
            users = [u for u in s.into(n["id"]) if u["type"] == "repo"]
            if len(users) >= 2:
                keep[n["id"]] = n
    nodes = []
    for n in keep.values():
        tip = n["props"].get("one_liner") or n["props"].get("mission") or n["props"].get("responsibility") or n["props"].get("description") or n["type"]
        nodes.append({"id": n["id"], "label": n["name"], "group": n["type"], "title": f"{n['type']}: {tip}"})
    marks = ",".join("?" * len(keep))
    edges = [
        {"from": r[0], "to": r[1], "label": r[2] if r[2] in ("OWNS", "FLOWS_TO") else "", "dashes": r[2] in ("USES", "ABOUT", "WRITTEN_IN")}
        for r in s.db.execute(f"SELECT src, dst, type FROM edges WHERE src IN ({marks}) AND dst IN ({marks}) AND type!='IMPLEMENTED_BY'", [*keep, *keep])
    ]
    groups = {g: {"color": {"background": c, "border": c}, "font": {"color": "#ffffff"}} for g, c in COLORS.items()}
    legend = " ".join(f'<span style="background:{c}">{g}</span>' for g, c in COLORS.items() if g != "endpoint")
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Org knowledge graph</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js"></script>
<style>html,body{{margin:0;height:100%;font-family:system-ui,sans-serif;background:#f8fafc}}#g{{height:100%}}
#legend{{position:fixed;top:10px;left:10px;z-index:2;font-size:12px}}#legend span{{color:#fff;padding:3px 8px;border-radius:10px;margin-right:4px}}</style></head>
<body><div id="legend">{legend}</div><div id="g"></div><script>
const data = {{nodes: new vis.DataSet({json.dumps(nodes)}), edges: new vis.DataSet({json.dumps(edges)})}};
new vis.Network(document.getElementById('g'), data, {{
  groups: {json.dumps(groups)},
  nodes: {{shape: 'box', margin: 6, font: {{size: 13}}}},
  edges: {{arrows: 'to', color: '#94a3b8', font: {{size: 9, color: '#64748b'}}, smooth: false}},
  physics: {{solver: 'forceAtlas2Based', forceAtlas2Based: {{gravitationalConstant: -60, springLength: 110}}, stabilization: {{iterations: 250}}}},
  interaction: {{hover: true, tooltipDelay: 100}}
}});
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out
