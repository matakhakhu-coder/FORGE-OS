def compute_pipeline_health(db) -> str:
    """
    Computes overall pipeline health based on latest run per component.

    Rules:
    - "critical" if any component has status != SUCCESS
    - "warning" if any component is stale (>6 hours)
    - "ok" otherwise
    """
    import datetime

    try:
        rows = db.execute("""
            SELECT component, status, run_at
            FROM   pipeline_runs pr
            WHERE  run_at = (
                SELECT MAX(run_at) FROM pipeline_runs pr2
                WHERE  pr2.component = pr.component
            )
            GROUP BY component
        """).fetchall()

        now = datetime.datetime.utcnow()
        stale_threshold = datetime.timedelta(hours=6)

        health = "ok"

        for r in rows:
            status = (r["status"] or "").lower()
            run_at = r["run_at"]

            # Parse timestamp safely
            try:
                run_time = datetime.datetime.fromisoformat(run_at)
            except Exception:
                continue

            # Critical: failed component
            if status not in ("success", "ok"):
                return "critical"

            # Warning: stale component
            if now - run_time > stale_threshold:
                health = "warning"

        return health

    except Exception:
        return "critical"