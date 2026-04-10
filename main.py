from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")


def api(method, url, data=None):
    r = requests.request(
        method,
        f"{GITEA_URL}{url}",
        headers={"Authorization": f"token {TOKEN}"},
        json=data
    )
    try:
        return r.json()
    except:
        return {}


# -------------------------
# HELPERS
# -------------------------
def get_reviews(owner, repo, pr_id):
    return api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews") or []


def get_changed_files_between(owner, repo, base, head):
    res = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/compare/{base}...{head}"
    )
    files = res.get("files", [])
    return [f["filename"] for f in files if isinstance(f, dict)]


def only_csproj(files):
    return files and all(f.endswith(".csproj") for f in files)


def has_real_code_changes(files):
    return any(not f.endswith(".csproj") for f in files)


def set_status(owner, repo, sha, state, desc):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/statuses/{sha}",
        {
            "state": state,
            "context": "pr-approval-gate",
            "description": desc
        }
    )

def get_commits(owner, repo, pr_id):
    res = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/commits")
    return [c["sha"] for c in (res or []) if isinstance(c, dict)]


def get_last_code_change_commit(owner, repo, commits, head_sha):
    last_code_sha = None

    for sha in commits:
        files = get_changed_files_between(owner, repo, sha, head_sha)

        if any(not f.endswith(".csproj") for f in files):
            last_code_sha = sha

    return last_code_sha

def get_last_approval_commit(reviews):
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    if not approvals:
        return None

    # берём самый “поздний” по появлению
    return approvals[-1].get("commit_id")


def has_valid_approval(owner, repo, pr_id, head_sha, reviews):
    commits = get_commits(owner, repo, pr_id)

    approvals = set(
        r.get("commit_id")
        for r in reviews
        if r.get("state") == "APPROVED" and r.get("commit_id")
    )

    if not approvals:
        return False

    # ищем последний code-change commit
    last_code_index = -1

    for i, sha in enumerate(commits):
        files = get_changed_files_between(owner, repo, sha, head_sha)

        if any(not f.endswith(".csproj") for f in files):
            last_code_index = i

    # если code вообще не было → ок
    if last_code_index == -1:
        return True

    # проверяем: есть ли аппрув ПОСЛЕ code change
    for j in range(last_code_index + 1, len(commits)):
        if commits[j] in approvals:
            return True

    return False


# -------------------------
# WEBHOOK
# -------------------------
@app.post("/webhook")
async def webhook(req: Request):
    payload = await req.json()

    pr = payload.get("pull_request")
    repo = payload.get("repository")

    if not pr or not repo:
        return {"ok": True}

    owner = repo["owner"]["username"]
    name = repo["name"]
    pr_id = pr["number"]
    head_sha = pr.get("head", {}).get("sha")

    if not head_sha:
        return {"ok": True}

    reviews = get_reviews(owner, name, pr_id)

    # -------------------------
    # ГЛАВНАЯ ПРОВЕРКА
    # -------------------------
    valid = has_valid_approval(owner, name, head_sha, reviews)

    if valid:
        set_status(owner, name, head_sha, "success", "Approved")
        return {"status": "approved"}

    else:
        set_status(
            owner,
            name,
            head_sha,
            "failure",
            "Re-approval required"
        )

        return {"status": "blocked"}
