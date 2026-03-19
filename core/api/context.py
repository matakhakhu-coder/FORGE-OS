from core.diagnostics.health import compute_pipeline_health


def inject_globals(get_db):
    """
    Provides global template context.
    Receives get_db as dependency injection.
    """

    def _inject():
        db = get_db()

        priority_count = db.execute(
            "SELECT COUNT(*) FROM signals WHERE is_priority = 1 AND status = 'raw'"
        ).fetchone()[0]

        sentinel_count = db.execute(
            "SELECT COUNT(*) FROM sentinel_alerts WHERE status = 'new'"
        ).fetchone()[0]

        discovery_count = db.execute(
            "SELECT COUNT(*) FROM discovery_targets WHERE status='pending'"
        ).fetchone()[0]

        pipeline_health = compute_pipeline_health(db)

        return dict(
            priority_count=priority_count,
            sentinel_count=sentinel_count,
            discovery_count=discovery_count,
            pipeline_health=pipeline_health,
        )

    return _inject
