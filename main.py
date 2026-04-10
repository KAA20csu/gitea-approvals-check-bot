from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")

PR_STATE = {}


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


def extract_files(files):
    return [f["filename"] for f in files if isinstance(f, dict)]


def comment(owner, repo, pr_id, text):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": text}
    )


def get_reviews(owner, repo, pr_id):
    return api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews"
    )


def get_files(owner, repo, pr_id):
    files = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files"
    )
    return extract_files(files)


def is_csproj_only(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_code_change(files):
    return any(not f.endswith(".csproj") for f in files)


def invalidate_all_approvals(owner, repo, pr_id):
    reviews = get_reviews(owner, repo, pr_id)

    for r in reviews:
        if r.get("state") == "APPROVED":
            api(
                "POST",
                f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews",
                {
                    "event": "REQUEST_CHANGES",
                    "body": "Auto invalidated due to code changes after approval"
                }
            )


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

    reviews = get_reviews(owner, name, pr_id)
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    files = get_files(owner, name, pr_id)

    has_approvals = len(approvals) >= 1
    csproj_only = is_csproj_only(files)
    code_change = has_code_change(files)

    # -------------------------
    # CASE 1: no approvals → ok but blocked by policy
    # -------------------------
    if not has_approvals:
        return {"status": "no approvals"}

    # -------------------------
    # CASE 2: csproj only → ALWAYS OK
    # -------------------------
    if csproj_only:
        return {"status": "ok csproj only"}

    # -------------------------
    # CASE 3: code change after approvals
    # -------------------------
    if code_change:
        comment(
            owner,
            name,
            pr_id,
            "❌ Обнаружены изменения в коде после аппрува. Аппрувы аннулированы, требуется повторное ревью."
        )

        invalidate_all_approvals(owner, name, pr_id)

        return {"status": "blocked code changed"}

    return {"status": "ok"}
