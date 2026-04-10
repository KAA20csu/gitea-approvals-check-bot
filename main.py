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
        "last_comment_type": None,
        "last_comment_sha": None,
        "last_approved_sha": None
    })

    reviews = get_reviews(owner, name, pr_id)
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]

    files = get_files(owner, name, pr_id)

    csproj_only = is_csproj_only(files)
    code_change = has_code_change(files)

    has_approvals = len(approvals) >= 1

    # ----------------------------------------
    # 1. нет аппрувов → просто выходим
    # ----------------------------------------
    if not has_approvals:
        return {"status": "no approvals"}

    # ----------------------------------------
    # 2. csproj-only → фиксируем approval SHA
    # ----------------------------------------
    if csproj_only:
        state["last_approved_sha"] = head_sha

        # анти-спам
        if state["last_comment_sha"] != head_sha or state["last_comment_type"] != "csproj":
            comment(owner, name, pr_id,
                    "📦 Только .csproj изменения — аппрувы валидны, мерж разрешён.")

            state["last_comment_sha"] = head_sha
            state["last_comment_type"] = "csproj"

        return {"status": "ok csproj"}

    # ----------------------------------------
    # 3. code change
    # ----------------------------------------
    if code_change:

        # 👉 если уже комментировали на этом SHA — ничего не делаем
        if state["last_comment_sha"] == head_sha and state["last_comment_type"] == "code":
            return {"status": "already handled"}

        comment(
            owner,
            name,
            pr_id,
            "❌ Обнаружены изменения в коде после аппрува. Требуется повторное ревью. Старые аппрувы больше невалидны."
        )

        state["last_comment_sha"] = head_sha
        state["last_comment_type"] = "code"

        # ❗ ВАЖНО: вместо "invalidate approvals" делаем это логически
        state["last_approved_sha"] = None

        return {"status": "code invalidated"}

    return {"status": "ok"}
