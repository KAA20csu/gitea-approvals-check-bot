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
# HELPERS
# -------------------------
def extract_files(files):
    return [f["filename"] for f in files if isinstance(f, dict)]


def is_csproj_only(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_code_changes(files):
    return any(not f.endswith(".csproj") for f in files)


def get_reviews(owner, repo, pr_id):
    reviews = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews"
    )
    return reviews or []


def get_files(owner, repo, pr_id):
    files = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files"
    )
    return extract_files(files or [])


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
    head_sha = pr.get("head", {}).get("sha")

    state = PR_STATE.setdefault(pr_id, {
        "last_notified_sha": None
    })

    # -------------------------
    # DATA
    # -------------------------
    reviews = get_reviews(owner, name, pr_id)
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    files = get_files(owner, name, pr_id)

    has_approvals = len(approvals) >= 1
    csproj_only = is_csproj_only(files)
    code_changes = has_code_changes(files)

    # -------------------------
    # RULE 1: no approvals
    # -------------------------
    if not has_approvals:
        return {"status": "no approvals"}

    # -------------------------
    # RULE 2: csproj only → ALWAYS OK
    # -------------------------
    if csproj_only:
        return {"status": "ok csproj"}

    # -------------------------
    # RULE 3: code changes after approval
    # -------------------------
    if code_changes:

        # 🔥 анти-спам: один коммент на SHA
        if state["last_notified_sha"] != head_sha:

            comment(
                owner,
                name,
                pr_id,
                "❌ Обнаружены изменения в коде после аппрува. Требуется повторное ревью. Старые аппрувы считаются невалидными."
            )

            state["last_notified_sha"] = head_sha

        return {"status": "blocked code change"}

    return {"status": "ok"}
