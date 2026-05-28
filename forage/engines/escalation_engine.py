from datetime import datetime, timezone


# Thresholds calibrated to FORAGE signal gravity range (0.0–0.70 typical).
# Original values (0.75/0.45) were set for a richer NLP pipeline.
# Lower them until a full spaCy/NER pipeline is integrated.
ESCALATE_THRESHOLD = 0.55
MONITOR_THRESHOLD  = 0.35


def rate_limit_check(db):
    """
    Prevent runaway case creation.
    Max 5 auto cases per hour.
    """
    row = db.execute("""
        SELECT COUNT(*) FROM cases
        WHERE auto_generated = 1
        AND created_at >= datetime('now', '-1 hour')
    """).fetchone()

    return (row[0] if row is not None else 0) < 5


def handle_escalation(conclusion, signal_id, db):
    """
    Decides whether to create events or cases based on Conclave output.
    Non-destructive and idempotent.
    """
    gravity = conclusion.gravity
    recommendation = conclusion.recommendation

    existing = db.execute(
        "SELECT case_id FROM cases WHERE trigger_signal_id = ?",
        (signal_id,)
    ).fetchone()

    if existing:
        return

    if gravity >= ESCALATE_THRESHOLD and recommendation == "ESCALATE":
        if not rate_limit_check(db):
            return
        return create_case(conclusion, signal_id, db)

    elif gravity >= MONITOR_THRESHOLD and recommendation in {"MONITOR", "ESCALATE"}:
        return create_event(conclusion, signal_id, db)

    return None


def create_event(conclusion, signal_id, db):
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO events (title, description, created_at, confidence_score, automated)
        VALUES (?, ?, ?, ?, 1)
    """, (
        f"Detected: {conclusion.intent}",
        f"Auto-generated from signal {signal_id}",
        datetime.now(timezone.utc),
        conclusion.confidence
    ))

    event_id = cursor.lastrowid

    db.execute("""
        UPDATE signals
        SET conclave_meta = json_patch(
            COALESCE(conclave_meta, '{}'),
            json_object('event_id', ?)
        )
        WHERE signal_id = ?
    """, (event_id, signal_id))

    db.commit()
    return event_id


def create_case(conclusion, signal_id, db):
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO cases (name, description, created_at, auto_generated, trigger_signal_id)
        VALUES (?, ?, ?, 1, ?)
    """, (
        f"Case: {conclusion.intent}",
        f"Escalated from signal {signal_id}",
        datetime.now(timezone.utc),
        signal_id
    ))

    case_id = cursor.lastrowid

    db.execute("""
        UPDATE signals
        SET conclave_meta = json_patch(
            COALESCE(conclave_meta, '{}'),
            json_object('case_id', ?)
        )
        WHERE signal_id = ?
    """, (case_id, signal_id))

    db.commit()
    return case_id