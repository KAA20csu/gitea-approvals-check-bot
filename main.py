from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")

PR_STATE = {}

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
# YOUR HELPERS (обязательно должны быть выше webhook)
# -------------------------
def get_reviews(owner, repo, pr_id):
    reviews = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews")
    return reviews or []


def get_files(owner, repo, pr_id):
    files = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files")
    return [f["filename"] for f in (files or []) if isinstance(f, dict)]


def is_csproj_only(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_code_changes(files):
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


def comment(owner, repo, pr_id, text):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": text}
    )


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

    state = PR_STATE.setdefault(pr_id, {
        "last_status": None,
        "last_comment": None,
        "last_comment_sha": None
    })

    reviews = get_reviews(owner, name, pr_id)
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    files = get_files(owner, name, pr_id)

    has_approvals = len(approvals) >= 1
    csproj_only = is_csproj_only(files)
    code_change = has_code_changes(files)

    def set_status_once(state_value, desc):
        key = f"{state_value}:{desc}"
        if state["last_status"] == key:
            return
        set_status(owner, name, sha, state_value, desc)
        state["last_status"] = key

    def comment_once(text, comment_type):
        if state["last_comment_sha"] == sha and state["last_comment"] == comment_type:
            return
        comment(owner, name, pr_id, text)
        state["last_comment_sha"] = sha
        state["last_comment"] = comment_type

    # RULES
    if not has_approvals:
        set_status_once("failure", "No approvals")
        return {"status": "blocked"}

    if csproj_only:
        set_status_once("success", "csproj-only change")
        return {"status": "allowed"}

    if code_change:
        set_status_once("failure", "Code changed after approval")

        comment_once(
            "❌ Code changed after approval. Merge blocked until re-review.",
            "code-change"
        )

        return {"status": "blocked code change"}

    set_status_once("success", "approved state")

    return {"status": "ok"}
