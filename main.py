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


def get_last_approval(reviews):
    approved = [r for r in reviews if r.get("state") == "APPROVED"]
    if not approved:
        return None

    # берём самый свежий аппрув
    approved.sort(key=lambda x: x.get("submitted_at", ""))
    return approved[-1]


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


def request_rereview(owner, repo, pr_id):
    # лайтовый способ — просто комментарий (без спама можно улучшить)
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": "🔄 Требуется повторное ревью после изменений в коде"}
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
    head_sha = pr.get("head", {}).get("sha")

    if not head_sha:
        return {"ok": True}

    reviews = get_reviews(owner, name, pr_id)
    last_approval = get_last_approval(reviews)

    # -------------------------
    # НЕТ АППРУВА
    # -------------------------
    if not last_approval:
        set_status(owner, name, head_sha, "failure", "No approvals")
        return {"status": "no approvals"}

    approval_sha = last_approval.get("commit_id")

    # -------------------------
    # ЕСЛИ SHA СОВПАДАЕТ → ВСЁ ОК
    # -------------------------
    if approval_sha == head_sha:
        set_status(owner, name, head_sha, "success", "Approved")
        return {"status": "approved"}

    # -------------------------
    # СМОТРИМ ЧТО ИЗМЕНИЛОСЬ ПОСЛЕ АППРУВА
    # -------------------------
    changed_files = get_changed_files_between(
        owner,
        name,
        approval_sha,
        head_sha
    )

    # -------------------------
    # ТОЛЬКО CSPROJ → ОК
    # -------------------------
    if only_csproj(changed_files):
        set_status(owner, name, head_sha, "success", "Only csproj changed")
        return {"status": "csproj ok"}

    # -------------------------
    # ЕСТЬ CODE CHANGE → БЛОК
    # -------------------------
    if has_real_code_changes(changed_files):
        set_status(
            owner,
            name,
            head_sha,
            "failure",
            "Code changed after approval"
        )

        request_rereview(owner, name, pr_id)

        return {"status": "re-review required"}

    # fallback
    set_status(owner, name, head_sha, "success", "Approved")
    return {"status": "ok"}
