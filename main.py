from fastapi import FastAPI, Request
import requests
import os

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL")
TOKEN = os.getenv("GITEA_TOKEN")


def gitea_get(url):
    return requests.get(
        f"{GITEA_URL}{url}",
        headers={"Authorization": f"token {TOKEN}"}
    ).json()


def is_version_change_only(files):
    allowed = (".csproj",)
    for f in files:
        if not f.endswith(allowed):
            return False
    return True


@app.post("/webhook")
async def webhook(req: Request):
    payload = await req.json()

    if payload.get("action") not in ["synchronized", "opened", "reopened"]:
        return {"ok": True}

    pr = payload["pull_request"]
    repo = payload["repository"]

    owner = repo["owner"]["username"]
    name = repo["name"]
    pr_id = pr["number"]

    # 1. approvals
    reviews = gitea_get(f"/api/v1/repos/{owner}/{name}/pulls/{pr_id}/reviews")

    approvals = [r for r in reviews if r["state"] == "APPROVED"]

    if len(approvals) < 2:
        return {"status": "not enough approvals"}

    last_approval_time = max(r["submitted_at"] for r in approvals)

    # 2. commits after approval
    commits = gitea_get(f"/api/v1/repos/{owner}/{name}/pulls/{pr_id}/commits")

    bad_commits = [
        c for c in commits
        if c["created"] > last_approval_time
    ]

    if not bad_commits:
        return {"status": "ok"}

    # 3. files in those commits
    all_files = set()

    for c in bad_commits:
        files = gitea_get(
            f"/api/v1/repos/{owner}/{name}/commits/{c['id']}"
        )["files"]

        for f in files:
            all_files.add(f["filename"])

    # 4. policy check
    if is_version_change_only(all_files):
        return {"status": "csproj-only ok"}

    # 5. reject + comment
    requests.post(
        f"{GITEA_URL}/api/v1/repos/{owner}/{name}/issues/{pr_id}/comments",
        headers={"Authorization": f"token {TOKEN}"},
        json={"body": "❌ PR changed code after approvals → re-review required"}
    )

    return {"status": "re-review triggered"}