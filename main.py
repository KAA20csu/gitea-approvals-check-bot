from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")


# -------------------------
# API
# -------------------------
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
# DATA HELPERS
# -------------------------
def get_reviews(owner, repo, pr_id):
    return api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews") or []


def get_approvals(reviews):
    return [r for r in reviews if r.get("state") == "APPROVED"]


def get_pr_commits(owner, repo, pr_id):
    res = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/commits")
    return [c["sha"] for c in (res or []) if isinstance(c, dict)]


def get_changed_files(owner, repo, base, head):
    res = api("GET", f"/api/v1/repos/{owner}/{repo}/compare/{base}...{head}")
    files = res.get("files", [])
    return [f["filename"] for f in files if isinstance(f, dict)]


# -------------------------
# RULES
# -------------------------
def only_csproj(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_code_changes(files):
    return any(not f.endswith(".csproj") for f in files)


def has_any_approval(reviews):
    return len(get_approvals(reviews)) > 0


# -------------------------
# CORE LOGIC
# -------------------------
def is_merge_allowed(owner, repo, pr_id, head_sha, reviews):
    if not has_any_approval(reviews):
        return False, "No approvals"

    commits = get_pr_commits(owner, repo, pr_id)

    # идём от HEAD назад по коммитам PR
    for i in range(len(commits) - 1, -1, -1):
        commit_sha = commits[i]

        files = get_changed_files(owner, repo, commit_sha, head_sha)

        # если нашли реальный code change → ищем аппрув после него
        if has_code_changes(files):
            # проверяем: есть ли аппрув после этого изменения
            approvals = get_approvals(reviews)

            valid = any(
                a.get("commit_id") in commits[i+1:]
                for a in approvals
            )

            if not valid:
                return False, "Code changed → re-approval required"

        # csproj игнорим полностью

    return True, "Approved"


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

    allowed, desc = is_merge_allowed(owner, name, pr_id, head_sha, reviews)

    state = "success" if allowed else "failure"

    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/statuses/{head_sha}",
        {
            "state": state,
            "context": "pr-approval-gate",
            "description": desc
        }
    )

    return {"status": state, "desc": desc}
