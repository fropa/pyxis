"""
Git repository crawler.
Clones or fetches a repo and yields relevant files for indexing.
"""
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Generator

import git

# File patterns we care about, by source_type
SOURCE_PATTERNS: dict[str, list[str]] = {
    "helm": ["**/Chart.yaml", "**/values*.yaml", "**/templates/**/*.yaml"],
    "terraform": ["**/*.tf", "**/*.tfvars"],
    "ansible": ["**/tasks/*.yml", "**/roles/**/*.yml", "**/playbooks/**/*.yml"],
    "k8s_manifest": ["**/manifests/**/*.yaml", "**/deploy/**/*.yaml", "**/k8s/**/*.yaml"],
    "github_actions": [".github/workflows/**/*.yml", ".github/workflows/**/*.yaml"],
    "gitlab_ci": ["**/.gitlab-ci.yml", "**/gitlab-ci/**/*.yml"],
    "jenkinsfile": ["**/Jenkinsfile", "**/Jenkinsfile.*"],
    "dockerfile": ["**/Dockerfile", "**/Dockerfile.*"],
}

MAX_FILE_SIZE_BYTES = 200 * 1024  # skip files > 200KB


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def clone_or_update(repo_url: str, access_token: str | None, dest_dir: str) -> git.Repo:
    """Clone or pull latest."""
    auth_url = repo_url
    if access_token:
        # Inject token for HTTPS auth
        if repo_url.startswith("https://github.com/"):
            auth_url = repo_url.replace("https://github.com/", f"https://{access_token}@github.com/")
        elif repo_url.startswith("https://gitlab.com/"):
            auth_url = repo_url.replace("https://gitlab.com/", f"https://oauth2:{access_token}@gitlab.com/")

    if os.path.exists(os.path.join(dest_dir, ".git")):
        repo = git.Repo(dest_dir)
        repo.remotes.origin.pull()
    else:
        repo = git.Repo.clone_from(auth_url, dest_dir, depth=1)

    return repo


def iter_files(repo_dir: str) -> Generator[tuple[str, str, str], None, None]:
    """
    Yields (source_type, relative_file_path, content) for all matching files.
    """
    root = Path(repo_dir)
    seen: set[str] = set()

    for source_type, patterns in SOURCE_PATTERNS.items():
        for pattern in patterns:
            for file_path in root.glob(pattern):
                rel = str(file_path.relative_to(root))
                if rel in seen:
                    continue
                if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    seen.add(rel)
                    yield source_type, rel, content
                except Exception:
                    continue
