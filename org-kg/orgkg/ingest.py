"""Source connector: list an owner's GitHub repos and mirror them locally (shallow).

The repos are only ever *read* by the extractor; nothing in them is executed.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from .config import DATA_DIR, REPOS_DIR, Config

KEEP = ("name", "description", "language", "size", "fork", "html_url", "clone_url", "default_branch", "pushed_at", "topics", "archived")


def list_repos(cfg: Config) -> list[dict]:
    kind = "orgs" if cfg.owner_type == "org" else "users"
    repos, page = [], 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/{kind}/{cfg.github_owner}/repos?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "org-kg"},
        )
        if token := os.environ.get("GITHUB_TOKEN"):
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.load(resp)
        repos += [{k: r.get(k) for k in KEEP} for r in batch]
        if len(batch) < 100:
            break
        page += 1
    return repos


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def sync(cfg: Config, log=print) -> list[dict]:
    """Clone or fast-forward every repo. Returns repo metadata incl. the synced commit."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repos = list_repos(cfg)
    for repo in repos:
        dest = REPOS_DIR / repo["name"]
        repo["head"] = None
        if repo["fork"] and not cfg.include_forks:
            repo["skipped"] = "fork"
        elif not repo["size"]:
            repo["skipped"] = "empty"
        elif repo["size"] > cfg.max_repo_size_kb:
            repo["skipped"] = "too large"
        if repo.get("skipped"):
            log(f"  skip   {repo['name']} ({repo['skipped']})")
            continue
        try:
            if (dest / ".git").exists():
                _git("fetch", "--depth", "1", "origin", repo["default_branch"], cwd=dest)
                _git("reset", "--hard", "FETCH_HEAD", cwd=dest)
                action = "update"
            else:
                _git("clone", "--depth", "1", "--quiet", repo["clone_url"], str(dest))
                action = "clone "
            repo["head"] = _git("rev-parse", "HEAD", cwd=dest)
            log(f"  {action} {repo['name']} @ {repo['head'][:8]}")
        except subprocess.CalledProcessError as exc:
            repo["skipped"] = f"git error: {exc.stderr.strip()[:200]}"
            log(f"  FAILED {repo['name']}: {repo['skipped']}")
    (DATA_DIR / "repos.json").write_text(json.dumps(repos, indent=2))
    return repos
