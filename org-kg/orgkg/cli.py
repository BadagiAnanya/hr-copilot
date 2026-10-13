from __future__ import annotations

import argparse
import json
import sys

from .config import load_config, load_dotenv
from .query import Graph, NotFound
from .store import Store


def _table(rows: list[dict]) -> None:
    if not rows:
        print("(nothing)")
        return
    for row in rows:
        print("- " + " | ".join(f"{', '.join(map(str, v)) if isinstance(v, list) else v}" for v in row.values() if v not in (None, "", [])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orgkg", description="Organisation knowledge graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="list the owner's GitHub repos and mirror them locally")
    p = sub.add_parser("build", help="extract nodes/edges from the mirrored repos (no LLM)")
    p.add_argument("--repo"), p.add_argument("--force", action="store_true")
    p = sub.add_parser("enrich", help="generate overview/HLD/LLD with the LLM (cached by content hash)")
    p.add_argument("--repo")
    sub.add_parser("stats")
    sub.add_parser("teams")
    sub.add_parser("repos").add_argument("--team")
    sub.add_parser("team").add_argument("name")
    p = sub.add_parser("repo", help="show overview / hld / lld / readme for a repo")
    p.add_argument("name"), p.add_argument("--level", default="overview", choices=["overview", "hld", "lld", "readme", "structure"])
    p = sub.add_parser("explain", help="explain a file or directory in a repo")
    p.add_argument("repo"), p.add_argument("path")
    p = sub.add_parser("search")
    p.add_argument("query"), p.add_argument("--repo")
    sub.add_parser("who-uses").add_argument("tech")
    sub.add_parser("related").add_argument("repo")
    sub.add_parser("ask", help="ask a question in natural language").add_argument("question")
    sub.add_parser("export", help="write out/wiki (markdown) and out/graph.html")
    args = parser.parse_args(argv)

    load_dotenv()
    cfg, store = load_config(), Store()
    graph = Graph(store)
    try:
        if args.cmd == "sync":
            from .ingest import sync
            sync(cfg)
        elif args.cmd == "build":
            from .extract import build
            build(store, cfg, only=args.repo, force=args.force)
        elif args.cmd == "enrich":
            from .enrich import enrich
            from .llm import get_llm
            enrich(store, get_llm(cfg.llm), only=args.repo)
        elif args.cmd == "stats":
            print(json.dumps(store.stats(), indent=1))
        elif args.cmd == "teams":
            _table(graph.list_teams())
        elif args.cmd == "repos":
            _table(graph.list_repos(args.team))
        elif args.cmd == "team":
            print(graph.team_brief(args.name))
        elif args.cmd == "repo":
            print(json.dumps(graph.repo_structure(args.name), indent=1) if args.level == "structure" else graph.repo_doc(args.name, args.level))
        elif args.cmd == "explain":
            print(json.dumps(graph.explain_path(args.repo, args.path), indent=1))
        elif args.cmd == "search":
            _table(graph.search(args.query, args.repo))
        elif args.cmd == "who-uses":
            _table(graph.who_uses(args.tech))
        elif args.cmd == "related":
            _table(graph.related_repos(args.repo))
        elif args.cmd == "ask":
            from .ask import ask
            from .llm import get_llm
            llm = get_llm(cfg.llm)
            result = ask(graph, llm, args.question)
            print(result["answer"])
            print(f"\nSources: {', '.join(result['sources'])}\n[{llm.provider}/{llm.model}: {llm.usage}]", file=sys.stderr)
        elif args.cmd == "export":
            from .wiki import export_graph_html, export_wiki
            print("wiki :", export_wiki(graph))
            print("graph:", export_graph_html(graph))
    except NotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
