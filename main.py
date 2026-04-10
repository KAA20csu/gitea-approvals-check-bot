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
# GITEA HELPERS
# -------------------------
def get_reviews(owner, repo, pr_id):
    res = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews"
    )
    return res or []


def get_files(owner, repo, pr_id):
    res = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files"
    )
    return [f["filename"] for f in (res or []) if isinstance(f, dict)]


def set_status(owner, repo, sha, state, desc):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/statuses/{sha}",
        {
            "state": state,  # success | failure | pending
            "context": "pr-approval-gate",
            "description": desc
        }
    )


# -------------------------
# LOGIC HELPERS
# -------------------------
def is_csproj_only(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_code_changes(files):
    return any(not f.endswith(".csproj") for f in files)


def compute_gate(has_approvals, csproj_only, code_change):
    """
    ЕДИНСТВЕННОЕ место принятия решения
    (никаких state-грязных багов больше)
    """
    if not has_approvals:
        return "failure", "No approvals"

    if csproj_only:
        return "success", "csproj-only change"

    if code_change:
        return "failure", "Code changed after approval"

    return "success", "approved state"


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
    sha = pr.get("head", {}).get("sha")

    if not sha:
        return {"ok": True}

    # -------------------------
    # DATA
    # -------------------------
    reviews = get_reviews(owner, name, pr_id)
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    files = get_files(owner, name, pr_id)

    has_approvals = len(approvals) >= 1
    csproj_only = is_csproj_only(files)
    code_change = has_code_changes(files)

    # -------------------------
    # COMPUTE STATE (NO CACHE, NO SPAM, NO STUCK STATE)
    # -------------------------
    state_value, desc = compute_gate(
        has_approvals,
        csproj_only,
        code_change
    )

    # -------------------------
    # APPLY STATUS (idempotent by Gitea context+sha)
    # -------------------------
    set_status(owner, name, sha, state_value, desc)

    return {
        "status": state_value,
        "desc": desc
    }
