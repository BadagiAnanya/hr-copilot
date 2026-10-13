"""Graph store: a property graph + document store + full-text index in one SQLite file.

nodes  — typed entities (org, team, repo, module, file, symbol, tech, concept)
edges  — typed relations between node ids
docs   — generated or source text attached to a node (readme, overview, hld, lld)
fts    — BM25 index over everything searchable
cache  — LLM results keyed by a hash of (prompt version, model, inputs)

The same schema maps 1:1 onto Postgres (+pgvector) or a graph DB when scaling out.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id    TEXT PRIMARY KEY,
    type  TEXT NOT NULL,
    name  TEXT NOT NULL,
    repo  TEXT,
    path  TEXT,
    props TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS nodes_repo ON nodes(repo);
CREATE TABLE IF NOT EXISTS edges (
    src   TEXT NOT NULL,
    dst   TEXT NOT NULL,
    type  TEXT NOT NULL,
    props TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (src, dst, type)
);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);
CREATE TABLE IF NOT EXISTS docs (
    node_id TEXT NOT NULL,
    kind    TEXT NOT NULL,
    body    TEXT NOT NULL,
    source  TEXT NOT NULL DEFAULT 'extracted',
    PRIMARY KEY (node_id, kind)
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(node_id UNINDEXED, kind UNINDEXED, title, body, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repo_state (repo TEXT PRIMARY KEY, head TEXT, extracted_head TEXT, enriched_key TEXT);
"""


class Store:
    def __init__(self, path: Path | str = DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- writes -------------------------------------------------------------
    def add_node(self, id: str, type: str, name: str, repo: str | None = None, path: str | None = None, **props) -> str:
        self.db.execute(
            "INSERT INTO nodes(id,type,name,repo,path,props) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type, name=excluded.name, repo=excluded.repo, path=excluded.path, props=excluded.props",
            (id, type, name, repo, path, json.dumps(props, sort_keys=True)),
        )
        return id

    def add_edge(self, src: str, type: str, dst: str, **props) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO edges(src,dst,type,props) VALUES(?,?,?,?)",
            (src, dst, type, json.dumps(props, sort_keys=True)),
        )

    def set_doc(self, node_id: str, kind: str, body: str, source: str = "extracted", title: str | None = None) -> None:
        self.db.execute("INSERT OR REPLACE INTO docs(node_id,kind,body,source) VALUES(?,?,?,?)", (node_id, kind, body, source))
        self.db.execute("DELETE FROM fts WHERE node_id=? AND kind=?", (node_id, kind))
        if title is None:
            row = self.node(node_id)
            title = row["name"] if row else node_id
        self.db.execute("INSERT INTO fts(node_id,kind,title,body) VALUES(?,?,?,?)", (node_id, kind, title, body))

    def delete_repo(self, repo: str, keep_generated: bool = True) -> None:
        """Drop everything extracted from one repo so it can be rebuilt (incremental re-index)."""
        ids = [r["id"] for r in self.db.execute("SELECT id FROM nodes WHERE repo=? AND type!='repo'", (repo,))]
        ids.append(f"repo:{repo}")
        for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
            marks = ",".join("?" * len(chunk))
            self.db.execute(f"DELETE FROM edges WHERE src IN ({marks})", chunk)
            src_filter = " AND source='extracted'" if keep_generated else ""
            for row in self.db.execute(f"SELECT node_id, kind FROM docs WHERE node_id IN ({marks}){src_filter}", chunk).fetchall():
                self.db.execute("DELETE FROM fts WHERE node_id=? AND kind=?", tuple(row))
            self.db.execute(f"DELETE FROM docs WHERE node_id IN ({marks}){src_filter}", chunk)
        self.db.execute("DELETE FROM nodes WHERE repo=? AND type!='repo'", (repo,))

    def commit(self) -> None:
        self.db.commit()

    # ---- reads --------------------------------------------------------------
    def node(self, id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM nodes WHERE id=?", (id,)).fetchone()
        return _node(row) if row else None

    def nodes(self, type: str | None = None, repo: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM nodes WHERE 1=1", []
        if type:
            sql, args = sql + " AND type=?", args + [type]
        if repo:
            sql, args = sql + " AND repo=?", args + [repo]
        return [_node(r) for r in self.db.execute(sql + " ORDER BY name", args)]

    def out(self, src: str, type: str | None = None) -> list[dict]:
        sql, args = "SELECT n.*, e.type AS edge, e.props AS eprops FROM edges e JOIN nodes n ON n.id=e.dst WHERE e.src=?", [src]
        if type:
            sql, args = sql + " AND e.type=?", args + [type]
        return [_node(r) for r in self.db.execute(sql + " ORDER BY n.name", args)]

    def into(self, dst: str, type: str | None = None) -> list[dict]:
        sql, args = "SELECT n.*, e.type AS edge, e.props AS eprops FROM edges e JOIN nodes n ON n.id=e.src WHERE e.dst=?", [dst]
        if type:
            sql, args = sql + " AND e.type=?", args + [type]
        return [_node(r) for r in self.db.execute(sql + " ORDER BY n.name", args)]

    def doc(self, node_id: str, kind: str) -> str | None:
        row = self.db.execute("SELECT body FROM docs WHERE node_id=? AND kind=?", (node_id, kind)).fetchone()
        return row["body"] if row else None

    def search(self, query: str, limit: int = 10, repo: str | None = None) -> list[dict]:
        terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        sql = (
            "SELECT f.node_id, f.kind, n.type, n.name, n.repo, n.path, "
            "snippet(fts, 3, '', '', ' … ', 24) AS snippet, bm25(fts, 0, 0, 4.0, 1.0) AS score "
            "FROM fts f JOIN nodes n ON n.id=f.node_id WHERE fts MATCH ?"
        )
        args: list = [match]
        if repo:
            sql, args = sql + " AND n.repo=?", args + [repo]
        rows = self.db.execute(sql + " ORDER BY score LIMIT ?", args + [limit * 3]).fetchall()
        # generated knowledge (overviews, designs) outranks raw source for the same score band
        boost = {"overview": 0.6, "hld": 0.7, "lld": 0.7, "readme": 0.85, "summary": 0.7}
        ranked = sorted((dict(r) for r in rows), key=lambda r: r["score"] / boost.get(r["kind"], 1.0))
        return ranked[:limit]

    # ---- cache --------------------------------------------------------------
    def cache_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def cache_put(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO cache(key,value) VALUES(?,?)", (key, value))
        self.db.commit()

    def stats(self) -> dict:
        nodes = {r[0]: r[1] for r in self.db.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type ORDER BY 2 DESC")}
        edges = {r[0]: r[1] for r in self.db.execute("SELECT type, COUNT(*) FROM edges GROUP BY type ORDER BY 2 DESC")}
        docs = {r[0]: r[1] for r in self.db.execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind ORDER BY 2 DESC")}
        return {"nodes": nodes, "edges": edges, "docs": docs}


def _node(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["props"] = json.loads(d["props"])
    if "eprops" in d:
        d["eprops"] = json.loads(d["eprops"])
    return d
