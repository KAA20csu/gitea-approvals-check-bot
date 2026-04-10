from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")


# -------------------------
# DEBUG SAFE API WRAPPER
# -------------------------
def api(method, url, data=None):
    full_url = f"{GITEA_URL}{url}"

    print(f"➡️ API CALL: {method} {full_url}")

    r = requests.request(
        method,
        full_url,
        headers={"Authorization": f"token {TOKEN}"},
        json=data
    )

    print(f"⬅️ STATUS: {r.status_code}")

    try:
        result = r.json()
        print(f"⬅️ RESPONSE: {result}")
        return result
    except Exception:
        print("⬅️ RAW RESPONSE:", r.text)
        return {}


# -------------------------
# HELPERS
# -------------------------
def extract_filename(f):
    return f.get("filename") if isinstance(f, dict) else f


def is_csproj_only(files):
    if not files:
        return False

    return all(extract_filename(f).endswith(".csproj") for f in files)


def get_approvals(owner, repo, pr_id):
    reviews = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews"
    )

    if not isinstance(reviews, list):
        return []

    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    print(f"🟢 APPROVALS COUNT: {len(approvals)}")
    return approvals


def get_changed_files(owner, repo, pr_id):
    files = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files"
    )

    if not isinstance(files, list):
        return []

    filenames = [extract_filename(f) for f in files]

    print(f"📦 FILES CHANGED: {filenames}")
    return filenames


def add_comment(owner, repo, pr_id, text):
    return api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": text}
    )


def add_label(owner, repo, pr_id, label):
    return api(
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
    print("ACTION:", payload.get("action"))

    if payload.get("action") not in ["opened", "synchronized", "reopened"]:
        print("⛔ Ignored event")
        return {"ok": True}

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    owner = repo.get("owner", {}).get("username")
    name = repo.get("name")
    pr_id = pr.get("number")

    print(f"📌 PR: {owner}/{name} #{pr_id}")

    # -------------------------
    # APPROVALS
    # -------------------------
    approvals = get_approvals(owner, name, pr_id)

    if len(approvals) < 1:
        print("⛔ Less than 1 approval → skip")
        return {"status": "no approvals"}

    # -------------------------
    # FILES
    # -------------------------
    files = get_changed_files(owner, name, pr_id)

    if not files:
        print("⚠️ No files detected")
        return {"status": "no files"}

    csproj_only = is_csproj_only(files)

    print("🧪 csproj_only:", csproj_only)

    # -------------------------
    # CASE 1: csproj ONLY
    # -------------------------
    if csproj_only:
        print("📦 CASE: csproj only")

        add_comment(
            owner, name, pr_id,
            "📦 Изменение версий пакетов"
        )

        return {"status": "csproj ok"}

    # -------------------------
    # CASE 2: CODE CHANGE
    # -------------------------
    print("🚨 CASE: code change detected")

    add_comment(
        owner, name, pr_id,
        "❌ Изменение в коде. Требуется повторное ревью."
    )

    add_label(owner, name, pr_id, "re-review-required")

    print("🔒 Merge should be blocked via branch rules")

    return {"status": "code review required"}
