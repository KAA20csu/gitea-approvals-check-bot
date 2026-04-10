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


def get_approvals(owner, repo, pr_id):
    reviews = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews"
    )
    return [r for r in reviews if r.get("state") == "APPROVED"]


def get_files(owner, repo, pr_id):
    files = api(
        "GET",
        f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files"
    )
    return extract_files(files)


def comment(owner, repo, pr_id, text):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/comments",
        {"body": text}
    )


def label(owner, repo, pr_id, lbl):
    api(
        "POST",
        f"/api/v1/repos/{owner}/{repo}/issues/{pr_id}/labels",
        {"labels": [lbl]}
    )


def block_state(state):
    state["blocked"] = True


def invalidate(state):
    state["invalidated"] = True


# -------------------------
# LOGIC HELPERS
# -------------------------
def is_csproj_only(files):
    return bool(files) and all(f.endswith(".csproj") for f in files)


def has_non_csproj(files):
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

    # -------------------------
    # STATE
    # -------------------------
    state = PR_STATE.setdefault(pr_id, {
        "last_comment_type": None,
        "blocked": False,
        "invalidated": False
    })

    # -------------------------
    # DATA
    # -------------------------
    approvals = get_approvals(owner, name, pr_id)
    files = get_files(owner, name, pr_id)

    print(f"PR #{pr_id}")
    print("approvals:", len(approvals))
    print("files:", files)

    has_approvals = len(approvals) >= 1
    csproj_only = is_csproj_only(files)
    code_changes = has_non_csproj(files)

    # -------------------------
    # RULE 1: NO APPROVALS
    # -------------------------
    if not has_approvals:
        if state["last_comment_type"] != "no-approval":
            comment(
                owner,
                name,
                pr_id,
                "❌ Нет активных аппрувов. Мерж запрещён."
            )
            state["last_comment_type"] = "no-approval"

        block_state(state)
        return {"status": "blocked no approvals"}

    # -------------------------
    # RULE 2: ONLY CSPROJ
    # -------------------------
    if csproj_only:
        if state["last_comment_type"] != "csproj":
            comment(
                owner,
                name,
                pr_id,
                "📦 Изменения только в .csproj. Аппрувы остаются валидными. Мерж разрешён."
            )
            state["last_comment_type"] = "csproj"

        state["blocked"] = False
        return {"status": "csproj allowed"}

    # -------------------------
    # RULE 3: CODE CHANGES
    # -------------------------
    if code_changes:
        if state["last_comment_type"] != "code":

            comment(
                owner,
                name,
                pr_id,
                "⚠️ Обнаружены изменения в коде после/во время аппрува. Требуется повторное ревью."
            )

            label(owner, name, pr_id, "re-review-required")

            invalidate(state)
            block_state(state)

            state["last_comment_type"] = "code"

        return {"status": "code review required"}

    return {"status": "ok"}
