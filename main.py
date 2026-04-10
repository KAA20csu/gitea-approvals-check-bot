from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL").rstrip("/")
TOKEN = os.getenv("GITEA_TOKEN")

# -------------------------
# STATE STORAGE (in-memory)
# -------------------------
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
    return files and all(f.endswith(".csproj") for f in files)


def get_approvals(owner, repo, pr_id):
    reviews = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews")
    return [r for r in reviews if r.get("state") == "APPROVED"]


def get_files(owner, repo, pr_id):
    files = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files")
    return extract_files(files)


def comment(owner, repo, pr_id, text):
    api("POST", f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments", {"body": text})


def label(owner, repo, pr_id, lbl):
    api("POST", f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/labels", {"labels": [lbl]})


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

    # -------------------------
    # commit id (PR head)
    # -------------------------
    commit_id = pr.get("head", {}).get("sha")

    if not commit_id:
        return {"ok": True}

    # -------------------------
    # INIT STATE
    # -------------------------
    state = PR_STATE.setdefault(pr_id, {
        "last_commit": None,
        "processed": set(),
        "invalidated": False
    })

    # -------------------------
    # DEDUP
    # -------------------------
    if commit_id in state["processed"]:
        print("⛔ Already processed commit")
        return {"ok": True}

    state["processed"].add(commit_id)
    state["last_commit"] = commit_id

    print(f"📌 PR #{pr_id} commit {commit_id}")

    # -------------------------
    # DATA
    # -------------------------
    approvals = get_approvals(owner, name, pr_id)
    files = get_files(owner, name, pr_id)

    print("🟢 approvals:", len(approvals))
    print("📦 files:", files)

    csproj_only = is_csproj_only(files)

    # -------------------------
    # RULE 1: <1 approval
    # -------------------------
    if len(approvals) < 1:
        print("⛔ No approvals")
        return {"status": "skip"}

    # -------------------------
    # RULE 2: csproj only
    # -------------------------
    if csproj_only:
        if state.get("last_comment_type") != "csproj":
            comment(owner, name, pr_id, "📦 Изменение версий пакетов")
            state["last_comment_type"] = "csproj"

        return {"status": "csproj ok"}

    # -------------------------
    # RULE 3: CODE CHANGE
    # -------------------------
    if state.get("last_comment_type") != "code":

        comment(
            owner,
            name,
            pr_id,
            "❌ Изменение в коде. Требуется повторное ревью. Аппрувы считаются устаревшими."
        )

        label(owner, name, pr_id, "re-review-required")

        state["invalidated"] = True
        state["last_comment_type"] = "code"

    return {"status": "code review required"}
