"""Deterministic extraction: repos on disk -> nodes and edges. No LLM involved.

Everything here is cheap, reproducible and exact (it is read off the code), which is why
structure comes from static analysis and only *explanations* come from the LLM (enrich.py).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from .config import DATA_DIR, REPOS_DIR, Config
from .store import Store

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "build", "dist", "target", ".idea", ".gradle", ".vscode", "Pods", "logs", ".ipynb_checkpoints"}
LANG = {
    ".py": "Python", ".ipynb": "Jupyter Notebook", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
    ".cpp": "C++", ".cc": "C++", ".h": "C++", ".hpp": "C++", ".c": "C", ".js": "JavaScript", ".ts": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".sql": "SQL", ".sh": "Shell",
}
CONFIG_EXT = {".yml", ".yaml", ".toml", ".gradle", ".properties", ".cfg", ".ini"}
CONFIG_NAMES = {"Dockerfile", "pom.xml", "package.json", "Pipfile", "Makefile", "docker-compose.yml", "requirements.txt", "setup.py"}
DOC_EXT = {".md", ".rst"}
MAX_FILE_BYTES = 400_000
MAX_BODY_CHARS = 20_000

ALIASES = {
    "sklearn": "scikit-learn", "cv2": "opencv", "pil": "pillow", "yaml": "pyyaml", "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4", "torch": "pytorch", "tf": "tensorflow", "dill": "dill", "pymongo": "mongodb",
    "google": "google-cloud", "langchain_core": "langchain", "langchain_community": "langchain",
    "langchain_openai": "langchain", "langchain_groq": "langchain", "opencv-python": "opencv", "python-dotenv": "python-dotenv",
    "pymongo[srv]": "mongodb", "uvicorn[standard]": "uvicorn",
}
JAVA_GROUPS = [
    ("org.springframework", "spring-boot"), ("androidx.", "android-sdk"), ("android.", "android-sdk"),
    ("com.google.firebase", "firebase"), ("com.google.android.gms", "google-play-services"), ("jakarta.", "jakarta-ee"),
    ("lombok", "lombok"), ("org.junit", "junit"), ("org.mockito", "mockito"), ("redis.", "redis"), ("io.lettuce", "redis"),
    ("org.apache.kafka", "kafka"), ("com.fasterxml.jackson", "jackson"), ("org.slf4j", "slf4j"), ("io.micrometer", "micrometer"),
    ("com.github.benmanes.caffeine", "caffeine"), ("org.hibernate", "hibernate"), ("retrofit2", "retrofit"), ("okhttp3", "okhttp"),
]
STDLIB = set(sys.stdlib_module_names) | {"__future__"}
HTTP_VERBS = {"get", "post", "put", "delete", "patch", "route", "websocket"}


def tech_name(raw: str) -> str:
    name = raw.strip().lower().replace("_", "-")
    return ALIASES.get(name, ALIASES.get(raw.strip().lower(), name))


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file() or path.is_symlink():
            continue
        yield path, PurePosixPath(rel.as_posix())


def classify(rel: PurePosixPath) -> tuple[str, str | None]:
    """-> (role, language). role is code | doc | config | other."""
    ext = rel.suffix.lower()
    if ext in LANG:
        return "code", LANG[ext]
    if ext in DOC_EXT:
        return "doc", "Markdown"
    if ext in CONFIG_EXT or rel.name in CONFIG_NAMES or rel.name.startswith("Dockerfile") or rel.name.startswith("requirements"):
        return "config", None
    return "other", None


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES and path.suffix != ".ipynb":
            return None
        data = path.read_bytes()
        if b"\0" in data[:2000]:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def notebook_to_source(text: str) -> tuple[str, str]:
    """-> (python code, markdown prose) with outputs and magics stripped."""
    try:
        cells = json.loads(text).get("cells", [])
    except (json.JSONDecodeError, AttributeError):
        return "", ""
    code, prose = [], []
    for cell in cells:
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else src
        if cell.get("cell_type") == "code":
            code.append("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
        elif cell.get("cell_type") == "markdown":
            prose.append(src)
    return "\n\n".join(code), "\n\n".join(prose)


# ---- per-language analysers: -> dict(imports, symbols, endpoints, entry) ---------------

def analyse_python(code: str) -> dict:
    out = {"imports": [], "symbols": [], "endpoints": [], "entry": '__name__ == "__main__"' in code or "__name__ == '__main__'" in code}
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        out["imports"] = [(m, 0) for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", code, re.M)]
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out["imports"] += [(a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out["imports"].append((node.module or "", node.level))
            if node.level:  # `from . import x` may name sibling modules
                out["imports"] += [(f"{node.module}.{a.name}" if node.module else a.name, node.level) for a in node.names]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            bases = [ast.unparse(b) for b in node.bases]
            out["symbols"].append({"name": node.name, "kind": "class", "line": node.lineno, "doc": _first_line(ast.get_docstring(node)), "methods": methods, "bases": bases})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["symbols"].append({"name": node.name, "kind": "function", "line": node.lineno, "doc": _first_line(ast.get_docstring(node)), "signature": f"({ast.unparse(node.args)})"})
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in HTTP_VERBS and dec.args:
                    first = dec.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("/"):
                        verb = "ANY" if dec.func.attr == "route" else dec.func.attr.upper()
                        out["endpoints"].append({"method": verb, "path": first.value, "handler": node.name})
    return out


def _first_line(doc: str | None) -> str:
    return (doc or "").strip().split("\n")[0][:200]


JAVA_TYPE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public|protected|private)?\s*(?:static\s+|final\s+|abstract\s+|sealed\s+)*(class|interface|enum|record)\s+(\w+)", re.M)
JAVA_METHOD = re.compile(r"^\s+(?:public|protected)\s+(?:static\s+|final\s+|synchronized\s+)*[\w<>\[\],.? ]+?\s+(\w+)\s*\(", re.M)
JAVA_MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*\"([^\"]*)\")?")
JAVA_STEREOTYPE = re.compile(r"@(RestController|Controller|Service|Repository|Component|Configuration|Entity|SpringBootApplication)\b")


def analyse_java(code: str) -> dict:
    package = (re.search(r"^\s*package\s+([\w.]+)\s*;", code, re.M) or [None, ""])[1]
    imports = [(m, 0) for m in re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;", code, re.M)]
    stereotypes = sorted(set(JAVA_STEREOTYPE.findall(code)))
    methods = [m for m in dict.fromkeys(JAVA_METHOD.findall(code)) if m not in {"if", "for", "while", "switch", "return", "new"}]
    symbols = []
    for i, m in enumerate(JAVA_TYPE.finditer(code)):
        symbols.append({
            "name": m.group(2), "kind": m.group(1), "line": code.count("\n", 0, m.start(2)) + 1, "doc": "",
            "methods": methods if i == 0 else [], "stereotypes": stereotypes if i == 0 else [],
        })
    endpoints, prefix = [], ""
    for m in JAVA_MAPPING.finditer(code):
        verb, path = m.group(1), m.group(2) or ""
        if verb == "Request" and not endpoints and code.rfind("class ", 0, m.start()) == -1:
            prefix = path  # class-level @RequestMapping
            continue
        endpoints.append({"method": "ANY" if verb == "Request" else verb.upper(), "path": (prefix + path) or "/", "handler": ""})
    return {"imports": imports, "symbols": symbols, "endpoints": endpoints, "entry": "public static void main" in code, "package": package}


def analyse_regex(code: str, lang: str) -> dict:
    imports, symbols = [], []
    if lang == "Swift":
        imports = [(m, 0) for m in re.findall(r"^\s*import\s+(\w+)", code, re.M)]
        pat = re.compile(r"^\s*(?:@\w+\s+)*(?:public |private |final |open |internal |fileprivate )*(class|struct|enum|protocol|actor|extension)\s+(\w+)", re.M)
        funcs = list(dict.fromkeys(re.findall(r"\bfunc\s+(\w+)", code)))
    elif lang in ("C++", "C"):
        imports = [(m, 0) for m in re.findall(r'^\s*#include\s+"([^"]+)"', code, re.M)]
        pat = re.compile(r"^\s*(class|struct)\s+(\w+)\s*(?::[^{;]*)?\{", re.M)
        funcs = [f for f in dict.fromkeys(re.findall(r"^[\w:<>*& ]+?\s+(\w+)\s*\([^;{]*\)\s*(?:const\s*)?\{", code, re.M)) if f not in {"if", "for", "while", "switch"}]
    else:  # JS / TS / Kotlin / Go …
        imports = [(m, 0) for m in re.findall(r"""(?:from\s+|require\(\s*)['"]([^'"]+)['"]""", code)]
        pat = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(class|interface|object)\s+(\w+)", re.M)
        funcs = list(dict.fromkeys(re.findall(r"\b(?:function|fun|func)\s+(\w+)", code)))
    for m in pat.finditer(code):
        symbols.append({"name": m.group(2), "kind": m.group(1), "line": code.count("\n", 0, m.start(2)) + 1, "doc": "", "methods": []})
    if symbols:
        symbols[0]["methods"] = funcs[:40]
    else:
        symbols = [{"name": f, "kind": "function", "line": 0, "doc": ""} for f in funcs[:40]]
    entry = bool(re.search(r"\bint\s+main\s*\(|@main\b|func main\(", code))
    return {"imports": imports, "symbols": symbols, "endpoints": [], "entry": entry}


# ---- manifests -------------------------------------------------------------------------

def manifest_techs(rel: PurePosixPath, text: str) -> list[str]:
    name = rel.name
    techs: list[str] = []
    if name.startswith("requirements") and name.endswith(".txt"):
        for line in text.splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith(("-", "git+", "http")):
                techs.append(re.split(r"[<>=!~;\s]", line)[0])
    elif name == "Pipfile":
        section = re.search(r"\[packages\](.*?)(?:\n\[|\Z)", text, re.S)
        techs += re.findall(r"^\s*([\w.\-\[\]]+)\s*=", section.group(1) if section else "", re.M)
    elif name == "pom.xml":
        for dep in re.findall(r"<dependency>(.*?)</dependency>", text, re.S):
            art = re.search(r"<artifactId>([^<]+)</artifactId>", dep)
            if art:
                techs.append(art.group(1))
        if "spring-boot-starter-parent" in text:
            techs.append("spring-boot")
    elif name.endswith(".gradle"):
        techs += re.findall(r"""(?:implementation|api|compile|testImplementation)\s*\(?\s*['"][\w.\-]+:([\w.\-]+)(?::[^'"]*)?['"]""", text)
    elif name == "package.json":
        try:
            pkg = json.loads(text)
            techs += list(pkg.get("dependencies", {})) + list(pkg.get("devDependencies", {}))
        except json.JSONDecodeError:
            pass
    elif name.startswith("Dockerfile"):
        techs.append("docker")
    elif name.startswith("docker-compose"):
        techs.append("docker-compose")
        techs += [img.split("/")[-1].split(":")[0] for img in re.findall(r"^\s*image:\s*['\"]?([\w./\-:]+)", text, re.M)]
    elif ".github/workflows" in rel.as_posix():
        techs.append("github-actions")
    cleaned = []
    for t in techs:
        t = re.sub(r"^spring-boot-starter.*$", "spring-boot", t.strip())
        if t:
            cleaned.append(tech_name(t))
    return cleaned


# ---- graph assembly --------------------------------------------------------------------

def build_org(store: Store, cfg: Config, repos: list[dict]) -> None:
    org = store.add_node(f"org:{cfg.org_name}", "org", cfg.org_name)
    assigned = {r for t in cfg.teams for r in t.repos}
    teams = list(cfg.teams)
    loose = [r["name"] for r in repos if r["name"] not in assigned]
    for team in teams:
        tid = store.add_node(f"team:{team.slug}", "team", team.name, description=team.description)
        store.add_edge(org, "HAS_TEAM", tid)
    if loose:
        tid = store.add_node("team:unassigned", "team", "Unassigned", description="Repos without an owning team in config/org.toml.")
        store.add_edge(org, "HAS_TEAM", tid)
    for repo in repos:
        team = cfg.team_of(repo["name"])
        rid = store.add_node(
            f"repo:{repo['name']}", "repo", repo["name"], repo=repo["name"],
            description=repo.get("description") or "", url=repo.get("html_url"), primary_language=repo.get("language"),
            fork=bool(repo.get("fork")), pushed_at=repo.get("pushed_at"), head=repo.get("head"), skipped=repo.get("skipped"),
        )
        store.add_edge(f"team:{team.slug}" if team else "team:unassigned", "OWNS", rid)


def extract_repo(store: Store, repo: dict) -> dict:
    name = repo["name"]
    root = REPOS_DIR / name
    rid = f"repo:{name}"
    store.delete_repo(name)

    files: dict[str, dict] = {}
    for path, rel in iter_files(root):
        role, lang = classify(rel)
        text = read_text(path) if role != "other" else None
        files[rel.as_posix()] = {"path": path, "rel": rel, "role": role, "lang": lang, "text": text, "bytes": path.stat().st_size}

    # resolution tables for internal imports
    py_modules, java_classes, by_name = {}, {}, defaultdict(list)
    for key, f in files.items():
        by_name[f["rel"].name].append(key)
        if f["lang"] == "Python":
            parts = list(f["rel"].with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):  # allow any source-root prefix (src/, app/ …)
                py_modules.setdefault(".".join(parts[i:]), key)

    lang_bytes, repo_tech, endpoints_n, symbols_n, entries = Counter(), Counter(), 0, 0, []
    analyses = {}
    for key, f in files.items():
        if f["role"] != "code" or f["text"] is None:
            continue
        code, prose = f["text"], ""
        if f["lang"] == "Jupyter Notebook":
            code, prose = notebook_to_source(f["text"])
            info = analyse_python(code)
        elif f["lang"] == "Python":
            info = analyse_python(code)
        elif f["lang"] == "Java":
            info = analyse_java(code)
            for sym in info["symbols"]:
                java_classes[f"{info['package']}.{sym['name']}".lstrip(".")] = key
        else:
            info = analyse_regex(code, f["lang"])
        info.update(code=code, prose=prose, loc=code.count("\n") + 1)
        analyses[key] = info
        lang_bytes[f["lang"]] += len(code)

    # modules (directories that contain indexed files)
    def module_id(dir_: PurePosixPath) -> str:
        if str(dir_) in (".", ""):
            return rid
        mid = f"mod:{name}/{dir_.as_posix()}"
        if not store.node(mid):
            store.add_node(mid, "module", dir_.as_posix(), repo=name, path=dir_.as_posix())
            store.add_edge(module_id(dir_.parent), "CONTAINS", mid)
        return mid

    for key, f in files.items():
        rel, info = f["rel"], analyses.get(key)
        if f["role"] == "other":
            continue
        fid = store.add_node(f"file:{name}/{key}", "file", rel.name, repo=name, path=key, role=f["role"], language=f["lang"], bytes=f["bytes"], loc=info["loc"] if info else None, entry_point=bool(info and info["entry"]))
        store.add_edge(module_id(rel.parent), "CONTAINS", fid)
        text = f["text"]
        if text is None:
            continue
        for tech in manifest_techs(rel, text):
            repo_tech[tech] += 3  # declared dependencies weigh more than a single import
            _use(store, fid, tech)
        body = text
        if info:
            body = (info["prose"] + "\n\n" + info["code"]).strip() if info["prose"] else info["code"]
            if info["entry"]:
                entries.append(key)
            for sym in info["symbols"]:
                sid = store.add_node(f"sym:{name}/{key}#{sym['name']}", "symbol", sym["name"], repo=name, path=key, **{k: v for k, v in sym.items() if k != "name"})
                store.add_edge(fid, "DEFINES", sid)
                store.set_doc(sid, "symbol", " ".join(filter(None, [sym["kind"], sym["name"], sym.get("doc"), " ".join(sym.get("methods", []))])))
                symbols_n += 1
            for ep in info["endpoints"]:
                eid = store.add_node(f"api:{name}/{ep['method']} {ep['path']}", "endpoint", f"{ep['method']} {ep['path']}", repo=name, path=key, handler=ep["handler"])
                store.add_edge(fid, "EXPOSES", eid)
                store.set_doc(eid, "endpoint", f"{ep['method']} {ep['path']} {ep['handler']} {key}")
                endpoints_n += 1
            for module, level in info["imports"]:
                target = _resolve(module, level, f, py_modules, java_classes, by_name)
                if target and target != key:
                    store.add_edge(fid, "IMPORTS", f"file:{name}/{target}")
                elif not target and (tech := _external(module, level, f["lang"])):
                    repo_tech[tech] += 1
                    _use(store, fid, tech)
        if rel.name.lower().startswith("readme") and str(rel.parent) == ".":
            store.set_doc(rid, "readme", text[:MAX_BODY_CHARS], title=name)
        store.set_doc(fid, "source", body[:MAX_BODY_CHARS], title=key)

    for lang in lang_bytes:
        store.add_node(f"tech:{lang.lower()}", "tech", lang, kind="language")
        store.add_edge(rid, "WRITTEN_IN", f"tech:{lang.lower()}", bytes=lang_bytes[lang])
    for tech, weight in repo_tech.items():
        store.add_edge(rid, "USES", f"tech:{tech}", weight=weight)

    node = store.node(rid)
    stats = {"files": sum(1 for f in files.values() if f["role"] != "other"), "code_files": len(analyses), "loc": sum(a["loc"] for a in analyses.values()), "symbols": symbols_n, "endpoints": endpoints_n, "languages": dict(lang_bytes.most_common()), "entry_points": entries[:10]}
    store.add_node(rid, "repo", name, repo=name, **{**node["props"], **stats})
    store.db.execute("INSERT INTO repo_state(repo, head, extracted_head) VALUES(?,?,?) ON CONFLICT(repo) DO UPDATE SET head=excluded.head, extracted_head=excluded.extracted_head", (name, repo.get("head"), repo.get("head")))
    return stats


def _use(store: Store, fid: str, tech: str) -> None:
    if not store.node(f"tech:{tech}"):
        store.add_node(f"tech:{tech}", "tech", tech, kind="library")
    store.add_edge(fid, "USES", f"tech:{tech}")


def _resolve(module: str, level: int, f: dict, py_modules: dict, java_classes: dict, by_name: dict) -> str | None:
    lang = f["lang"]
    if lang in ("Python", "Jupyter Notebook"):
        if level:
            base = list(f["rel"].parent.parts)
            base = base[: len(base) - (level - 1)] if level > 1 else base
            module = ".".join([*base, *(module.split(".") if module else [])])
        parts = module.split(".")
        for i in range(len(parts), 0, -1):
            if hit := py_modules.get(".".join(parts[:i])):
                return hit
    elif lang == "Java":
        return java_classes.get(module)
    elif lang in ("C++", "C"):
        hits = by_name.get(PurePosixPath(module).name)
        return hits[0] if hits else None
    elif module.startswith("."):
        stem = PurePosixPath(module).name
        for ext in ("", ".js", ".ts", ".jsx", ".tsx"):
            if hits := by_name.get(stem + ext):
                return hits[0]
    return None


def _external(module: str, level: int, lang: str) -> str | None:
    if not module or level:
        return None
    if lang in ("Python", "Jupyter Notebook"):
        top = module.split(".")[0]
        return None if top in STDLIB or top.startswith("_") else tech_name(top)
    if lang == "Java":
        if module.startswith(("java.", "javax.")):
            return None
        for prefix, tech in JAVA_GROUPS:
            if module.startswith(prefix):
                return tech
        return None  # unresolved same-project or unknown vendor package: don't invent a technology
    if lang == "Swift":
        return None if module == "Foundation" else tech_name(module)
    if lang in ("C++", "C"):
        return None
    if module.startswith((".", "/")):
        return None
    return tech_name(module if not module.startswith("@") else "/".join(module.split("/")[:2]))


def build(store: Store, cfg: Config, only: str | None = None, force: bool = False, log=print) -> None:
    repos = json.loads((DATA_DIR / "repos.json").read_text())
    build_org(store, cfg, repos)
    state = {r["repo"]: r["extracted_head"] for r in store.db.execute("SELECT repo, extracted_head FROM repo_state")}
    for repo in repos:
        name = repo["name"]
        if (only and name != only) or repo.get("skipped") or not (REPOS_DIR / name).exists():
            continue
        if not force and state.get(name) == repo.get("head"):
            log(f"  unchanged {name}")
            continue
        stats = extract_repo(store, repo)
        store.commit()
        log(f"  extracted {name}: {stats['files']} files, {stats['symbols']} symbols, {stats['endpoints']} endpoints, {stats['loc']} loc")
    store.commit()
