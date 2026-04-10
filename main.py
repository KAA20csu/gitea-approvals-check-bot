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


def has_valid_approval(owner, repo, head_sha, reviews):
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    for a in approvals:
        approval_sha = a.get("commit_id")

        if not approval_sha:
            continue

        # если аппрув прямо на текущий коммит — идеально
        if approval_sha == head_sha:
            return True

        changed_files = get_changed_files_between(
            owner,
            repo,
            approval_sha,
            head_sha
        )

        # если после аппрува менялись только csproj → аппрув валиден
        if only_csproj(changed_files):
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
