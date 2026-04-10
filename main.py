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
# CORE LOGIC
# -------------------------
def get_last_approval(reviews):
    approved = [r for r in reviews if r.get("state") == "APPROVED"]

    if not approved:
        return None

    approved.sort(key=lambda x: x.get("submitted_at", ""))
    return approved[-1]


def is_last_approval_valid(owner, repo, head_sha, reviews):
    last = get_last_approval(reviews)

    if not last:
        return False

    approval_sha = last.get("commit_id")

    if not approval_sha:
        return False

    # если аппрув прямо на текущий коммит
    if approval_sha == head_sha:
        return True

    changed_files = get_changed_files_between(
        owner,
        repo,
        approval_sha,
        head_sha
    )

    # если после аппрува менялся только csproj → НЕ инвалидируем
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
    is_valid = is_last_approval_valid(owner, name, head_sha, reviews)

    if is_valid:
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
