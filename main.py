from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")

CONTEXT = "pr-approval-gate"


# -------------------------
# API
# -------------------------
def api(method, url, data=None):
    r = requests.request(
        method,
        f"{GITEA_URL}{url}",
        headers={"Authorization": f"token {TOKEN}"},
        json=data,
        timeout=10
    )
    try:
        return r.json()
    except:
        return {}


# -------------------------
# GITEA HELPERS
# -------------------------
def get_reviews(owner, repo, pr_id):
    return api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews"
    ) or []


def get_commits(owner, repo, pr_id):
    res = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/commits"
    )
    return [c["sha"] for c in (res or []) if isinstance(c, dict)]


def get_changed_files(owner, repo, base, head):
    res = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/compare/{base}...{head}"
    )
    files = res.get("files", []) if isinstance(res, dict) else []
    return [f["filename"] for f in files if isinstance(f, dict)]


def set_status(owner, repo, sha, state, desc):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/statuses/{sha}",
        {
            "state": state,  # success | failure
            "context": CONTEXT,
            "description": desc
        }
    )


# -------------------------
# LOGIC HELPERS
# -------------------------
def is_code_change(files):
    return any(not f.endswith(".csproj") for f in files)


def get_last_code_change_commit(owner, repo, commits, head_sha):
    last = None

    for sha in commits:
        files = get_changed_files(owner, repo, sha, head_sha)
        if is_code_change(files):
            last = sha

    return last


def get_last_approval_commit(reviews):
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]
    if not approvals:
        return None

    # берем самый свежий (без trust на порядок API)
    return approvals[-1].get("commit_id")


# -------------------------
# CORE RULE ENGINE
# -------------------------
def is_merge_allowed(owner, repo, pr_id, head_sha, reviews):
    commits = get_commits(owner, repo, pr_id)

    last_code = get_last_code_change_commit(owner, repo, commits, head_sha)
    last_approval = get_last_approval_commit(reviews)

    # нет аппрува вообще
    if not last_approval:
        return False, "No approvals"

    # если code изменений не было
    if not last_code:
        return True, "Approved"

    # если последний аппрув позже code change → OK
    try:
        if commits.index(last_approval) > commits.index(last_code):
            return True, "Approved after code change"
    except ValueError:
        return False, "Invalid commit history"

    return False, "Re-approval required"


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

    # ⚠️ ВАЖНО: берем SHA ИЗ PAYLOAD (без race conditions)
    head_sha = pr["head"]["sha"]

    reviews = get_reviews(owner, name, pr_id)

    allowed, desc = is_merge_allowed(owner, name, pr_id, head_sha, reviews)

    state = "success" if allowed else "failure"

    # ⚠️ ВСЕГДА пишем статус на текущий HEAD
    set_status(owner, name, head_sha, state, desc)

    return {
        "state": state,
        "desc": desc
    }
