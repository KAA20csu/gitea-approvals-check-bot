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


def comment(owner, repo, pr_id, text):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": text}
    )


def set_status(owner, repo, sha, state, description):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/statuses/{sha}",
        {
            "state": state,  # success | failure | pending
            "context": "pr-approval-gate",
            "description": description
        }
    )


# -------------------------
# LOGIC HELPERS
# -------------------------
def is_csproj_only(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_code_changes(files):
    return any(not f.endswith(".csproj") for f in files)


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
    # RULE 1: NO APPROVALS
    # -------------------------
    if not has_approvals:
        set_status(
            owner,
            name,
            sha,
            "failure",
            "No active approvals"
        )
        return {"status": "blocked no approvals"}

    # -------------------------
    # RULE 2: ONLY CSPROJ → ALWAYS ALLOW
    # -------------------------
    if csproj_only:
        set_status(
            owner,
            name,
            sha,
            "success",
            "csproj-only change, approvals valid"
        )
        return {"status": "allowed csproj"}

    # -------------------------
    # RULE 3: CODE CHANGE AFTER APPROVAL → HARD BLOCK
    # -------------------------
    if code_change:
        set_status(
            owner,
            name,
            sha,
            "failure",
            "Code change after approval - re-review required"
        )

        comment(
            owner,
            name,
            pr_id,
            "❌ Изменения в коде после аппрува. Мерж заблокирован до повторного ревью."
        )

        return {"status": "blocked code change"}

    # -------------------------
    # DEFAULT SAFE STATE
    # -------------------------
    set_status(
        owner,
        name,
        sha,
        "success",
        "approved and unchanged"
    )

    return {"status": "ok"}
