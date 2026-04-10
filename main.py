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
    sha = pr.get("head", {}).get("sha")

    if not sha:
        return {"ok": True}

    state = PR_STATE.setdefault(pr_id, {
        "last_status": None,
        "last_comment": None,
        "last_comment_sha": None
    })

    reviews = get_reviews(owner, name, pr_id)
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    files = get_files(owner, name, pr_id)

    has_approvals = len(approvals) >= 1
    csproj_only = is_csproj_only(files)
    code_change = has_code_changes(files)

    # -------------------------
    # STATUS HELPERS (ANTI-SPAM)
    # -------------------------
    def set_status_once(state_value, desc):
        key = f"{state_value}:{desc}"
        if state["last_status"] == key:
            return

        set_status(owner, name, sha, state_value, desc)
        state["last_status"] = key

    def comment_once(text, comment_type):
        if state["last_comment_sha"] == sha and state["last_comment"] == comment_type:
            return

        comment(owner, name, pr_id, text)
        state["last_comment_sha"] = sha
        state["last_comment"] = comment_type

    # -------------------------
    # RULE 1: NO APPROVALS
    # -------------------------
    if not has_approvals:
        set_status_once("failure", "No approvals")
        return {"status": "blocked"}

    # -------------------------
    # RULE 2: CSPROJ ONLY
    # -------------------------
    if csproj_only:
        set_status_once("success", "csproj-only change")
        return {"status": "allowed"}

    # -------------------------
    # RULE 3: CODE CHANGE → HARD BLOCK
    # -------------------------
    if code_change:
        set_status_once(
            "failure",
            "Code changed after approval"
        )

        comment_once(
            "❌ Code changed after approval. Merge blocked until re-review.",
            "code-change"
        )

        return {"status": "blocked code change"}

    # -------------------------
    # SAFE STATE
    # -------------------------
    set_status_once("success", "approved state")

    return {"status": "ok"}
