from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

GITEA_URL = os.getenv("GITEA_URL")
TOKEN = os.getenv("GITEA_TOKEN")


def api(method, url, data=None):
    return requests.request(
        method,
        f"{GITEA_URL}{url}",
        headers={"Authorization": f"token {TOKEN}"},
        json=data
    ).json()


def is_csproj_only(files):
    for f in files:
        if not f.endswith(".csproj"):
            return False
    return True


def get_approvals(owner, repo, pr_id):
    reviews = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/reviews")
    return [r for r in reviews if r["state"] == "APPROVED"]


def get_changed_files(owner, repo, pr_id):
    commits = api("GET", f"/api/v1/repos/{owner}/{repo}/pulls/{pr_id}/files")
    return [f["filename"] for f in commits]


def request_review_from_codeowners(owner, repo, pr_id):
    # упрощённо: берём codeowners файл (если есть)
    try:
        co = api("GET", f"/api/v1/repos/{owner}/{repo}/contents/CODEOWNERS")
        # дальше можно парсить, но оставим минимально
    except:
        pass


@app.post("/webhook")
async def webhook(req: Request):
    payload = await req.json()

    if payload.get("action") not in ["opened", "synchronized", "reopened"]:
        return {"ok": True}

    pr = payload["pull_request"]
    repo = payload["repository"]

    owner = repo["owner"]["username"]
    name = repo["name"]
    pr_id = pr["number"]

    approvals = get_approvals(owner, name, pr_id)

    if len(approvals) < 1:
        return {"status": "no approvals"}

    files = get_changed_files(owner, name, pr_id)

    csproj_only = is_csproj_only(files)

    # CASE 1: csproj changes
    if csproj_only:
        api(
            "POST",
            f"/api/v1/repos/{owner}/{name}/issues/{pr_id}/comments",
            {"body": "📦 Изменение версий пакетов"}
        )
        return {"status": "csproj ok"}

    # CASE 2: code changes
    api(
        "POST",
        f"/api/v1/repos/{owner}/{name}/issues/{pr_id}/comments",
        {"body": "❌ Изменение в коде. Требуется повторное ревью."}
    )

    # NOTE: Gitea НЕ умеет прям "запросить review",
    # но можно:
    # - добавить label
    # - или mention CODEOWNERS
    # - или fail status check

    api(
        "POST",
        f"/api/v1/repos/{owner}/{name}/issues/{pr_id}/labels",
        {"labels": ["re-review-required"]}
    )

    return {"status": "code review required"}
