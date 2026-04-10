from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")


# -------------------------
# API WRAPPER
# -------------------------
def api(method, url, data=None):
    r = requests.request(
        method,
        f"{GITEA_URL}{url}",
        headers={"Authorization": f"token {TOKEN}"},
        json=data
    )

    print(f"➡️ {method} {url} -> {r.status_code}")

    try:
        return r.json()
    except:
        return {}


# -------------------------
# HELPERS
# -------------------------
def extract_files(owner, repo, pr_id):
    files = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files")
    return [f.get("filename") for f in files if isinstance(f, dict)]


def get_approvals(owner, repo, pr_id):
    reviews = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews")
    return [r for r in reviews if r.get("state") == "APPROVED"]


def is_csproj_only(files):
    return files and all(f.endswith(".csproj") for f in files)


def comment(owner, repo, pr_id, text):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": text}
    )


def add_label(owner, repo, pr_id, label):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/labels",
        {"labels": [label]}
    )


# -------------------------
# WEBHOOK
# -------------------------
@app.post("/webhook")
async def webhook(req: Request):
    payload = await req.json()

    print("\n====================")
    print("🔥 WEBHOOK RECEIVED")
    print("RAW ACTION:", payload.get("action"))

    pr = payload.get("pull_request")
    repo = payload.get("repository")

    # ❗ CRITICAL FIX: не фильтруем action вообще
    if not pr or not repo:
        print("⛔ Not a PR event")
        return {"ok": True}

    owner = repo["owner"]["username"]
    name = repo["name"]
    pr_id = pr["number"]

    print(f"📌 PR: {owner}/{name} #{pr_id}")

    # -------------------------
    # APPROVALS
    # -------------------------
    approvals = get_approvals(owner, name, pr_id)
    print("🟢 APPROVALS:", len(approvals))

    if len(approvals) < 1:
        print("⛔ No approvals → skip")
        return {"status": "no approvals"}

    # -------------------------
    # FILES
    # -------------------------
    files = extract_files(owner, name, pr_id)
    print("📦 FILES:", files)

    # -------------------------
    # CASE 1: csproj only
    # -------------------------
    if is_csproj_only(files):
        comment(owner, name, pr_id, "📦 Изменение версий пакетов")
        return {"status": "csproj ok"}

    # -------------------------
    # CASE 2: CODE CHANGE
    # -------------------------
    comment(owner, name, pr_id, "❌ Изменение в коде. Требуется повторное ревью.")
    add_label(owner, name, pr_id, "re-review-required")

    return {"status": "code review required"}
