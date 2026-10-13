import json

import pytest

from orgkg import enrich, extract
from orgkg.llm import LLM
from orgkg.query import Graph, NotFound
from orgkg.store import Store

FILES = {
    "svc/README.md": "# svc\nOrder service with an HTTP API.",
    "svc/requirements.txt": "fastapi==0.115\nscikit-learn>=1.0\n",
    "svc/app/__init__.py": "",
    "svc/app/main.py": 'from fastapi import FastAPI\nfrom app.orders import OrderBook\nimport os\napp = FastAPI()\n\n@app.get("/orders")\ndef list_orders():\n    return OrderBook().all()\n\nif __name__ == "__main__":\n    pass\n',
    "svc/app/orders.py": 'import sklearn\n\nclass OrderBook:\n    """Keeps orders in memory."""\n    def all(self):\n        return []\n',
    "gw/src/main/java/com/x/Api.java": 'package com.x;\nimport org.springframework.web.bind.annotation.*;\nimport com.x.Ledger;\n@RestController\npublic class Api {\n  @PostMapping("/pay")\n  public String pay() { return "ok"; }\n}\n',
    "gw/src/main/java/com/x/Ledger.java": "package com.x;\npublic class Ledger {\n  public void record() {}\n}\n",
    "nb/explore.ipynb": json.dumps({"cells": [{"cell_type": "markdown", "source": ["# Exploration"]}, {"cell_type": "code", "source": ["%matplotlib inline\n", "import pandas as pd\n", "import sklearn\n"]}]}),
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    for rel, text in FILES.items():
        path = tmp_path / "repos" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    monkeypatch.setattr(extract, "REPOS_DIR", tmp_path / "repos")
    s = Store(tmp_path / "g.sqlite3")
    for name in ("svc", "gw", "nb"):
        s.add_node(f"repo:{name}", "repo", name, repo=name)
        extract.extract_repo(s, {"name": name, "head": "abc"})
    s.add_node("team:a", "team", "Team A")
    s.add_edge("team:a", "OWNS", "repo:svc")
    s.commit()
    return s


def test_python_structure(store):
    assert [e["name"] for e in store.nodes("endpoint", "svc")] == ["GET /orders"]
    assert [n["path"] for n in store.out("file:svc/app/main.py", "IMPORTS")] == ["app/orders.py"]
    techs = {t["name"] for t in store.out("repo:svc", "USES")}
    assert techs == {"fastapi", "scikit-learn"}  # stdlib `os` excluded, sklearn alias merged with the manifest name
    assert store.node("repo:svc")["props"]["entry_points"] == ["app/main.py"]
    assert store.node("sym:svc/app/orders.py#OrderBook")["props"]["doc"] == "Keeps orders in memory."


def test_java_and_notebook(store):
    assert [e["name"] for e in store.nodes("endpoint", "gw")] == ["POST /pay"]
    assert [n["name"] for n in store.out("file:gw/src/main/java/com/x/Api.java", "IMPORTS")] == ["Ledger.java"]
    assert "spring-boot" in {t["name"] for t in store.out("repo:gw", "USES")}
    assert {t["name"] for t in store.out("repo:nb", "USES")} == {"pandas", "scikit-learn"}


def test_reextract_is_idempotent(store):
    before = store.stats()
    extract.extract_repo(store, {"name": "svc", "head": "def"})
    assert store.stats() == before


class FakeLLM(LLM):
    def __init__(self):
        super().__init__("fake", "fake-1")

    def complete(self, system, user, max_tokens=0):
        self.usage.calls += 1
        return json.dumps({
            "one_liner": "Order service", "maturity": "prototype", "overview": "Serves orders.",
            "hld": {"summary": "API over a book.", "components": [{"name": "API", "responsibility": "HTTP", "paths": ["app/main.py", "app/ghost.py"]}, {"name": "Book", "responsibility": "state", "paths": ["app/orders.py"]}],
                    "connections": [{"from": "API", "to": "Book", "label": "reads"}], "data_flow": ["request", "lookup"]},
            "lld": [{"path": "app/orders.py", "responsibility": "in-memory orders", "flow": "returns list"}, {"path": "nope.py", "responsibility": "x"}],
            "concepts": ["Order Book"], "onboarding": {"start_here": ["app/main.py"]},
        })


def test_enrich_grounds_paths_and_caches(store):
    llm = FakeLLM()
    info = enrich.enrich_repo(store, llm, "svc")
    assert info["ungrounded_paths"] == 2  # app/ghost.py and nope.py do not exist and are dropped
    assert [n["path"] for n in store.out("comp:svc/api", "IMPLEMENTED_BY")] == ["app/main.py"]
    assert "c0 -->|reads| c1" in store.doc("repo:svc", "hld")
    assert store.doc("file:svc/app/orders.py", "lld").startswith("in-memory orders")
    enrich.enrich_repo(store, llm, "svc")
    assert llm.usage.calls == 1  # unchanged content -> served from the cache


def test_queries(store):
    g = Graph(store)
    assert g.repo("SVC")["name"] == "svc"
    assert {u["repo"] for u in g.who_uses("scikit-learn")} == {"svc", "nb"}
    assert g.related_repos("svc")[0]["repo"] == "nb"
    assert g.search("orders memory")[0]["repo"] == "svc"
    assert g.list_repos("Team A")[0]["repo"] == "svc"
    with pytest.raises(NotFound):
        g.explain_path("svc", "missing.py")
