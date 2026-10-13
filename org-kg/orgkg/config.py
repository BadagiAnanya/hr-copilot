from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("ORGKG_HOME", Path(__file__).resolve().parent.parent))
DATA_DIR = ROOT / "data"
REPOS_DIR = DATA_DIR / "repos"
DB_PATH = DATA_DIR / "graph.sqlite3"
OUT_DIR = ROOT / "out"


@dataclass
class Team:
    slug: str
    name: str
    description: str
    repos: list[str]


@dataclass
class Config:
    org_name: str
    github_owner: str
    owner_type: str = "user"
    include_forks: bool = True
    max_repo_size_kb: int = 100_000
    llm: dict = field(default_factory=dict)
    teams: list[Team] = field(default_factory=list)

    def team_of(self, repo: str) -> Team | None:
        for team in self.teams:
            if repo in team.repos:
                return team
        return None


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config" / "org.toml"
    raw = tomllib.loads(path.read_text())
    org = raw["org"]
    teams = [
        Team(slug=slug, name=t.get("name", slug), description=t.get("description", ""), repos=t.get("repos", []))
        for slug, t in raw.get("teams", {}).items()
    ]
    return Config(
        org_name=org["name"],
        github_owner=org["github_owner"],
        owner_type=org.get("owner_type", "user"),
        include_forks=org.get("include_forks", True),
        max_repo_size_kb=org.get("max_repo_size_kb", 100_000),
        llm=raw.get("llm", {}),
        teams=teams,
    )
