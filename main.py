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

    if not approvals:
        return False

    # берём ВСЕ commit_id аппрувов
    approval_shas = set(a.get("commit_id") for a in approvals if a.get("commit_id"))

    # получаем всю историю PR
    commits = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{repo}/commits")
    commits = [c["sha"] for c in commits or [] if isinstance(c, dict)]

    last_bad_change_index = -1

    # ищем последний "code change commit"
    for i in range(len(commits)):
        sha = commits[i]

        files = get_changed_files_between(owner, repo, sha, head_sha)

        if any(not f.endswith(".csproj") for f in files):
            last_bad_change_index = i

    # если code changes были после всех аппрувов → блок
    for i in range(last_bad_change_index + 1, len(commits)):
        sha = commits[i]

        if sha in approval_shas:
            return True

    # если code changes есть и нет аппрува после них → блок
    if last_bad_change_index != -1:
        return False

    # если вообще нет code changes → достаточно любого аппрува
    return True


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
