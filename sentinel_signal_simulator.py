import random, uuid, time, json
from core.pipeline.wiki_logger import WikiLogger

wiki_logger = WikiLogger()

# Config: number of mock signals and actors
NUM_ACTORS = 5
NUM_SIGNALS = 50

actors = [f"Actor-{i+1}" for i in range(NUM_ACTORS)]
events = ["pulse_detected", "artifact_updated", "state_change", "signal_emitted", "context_shift"]

print("Starting Signal Simulator...")

for _ in range(NUM_SIGNALS):
    actor_id = random.choice(actors)
    event_id = random.choice(events)
    artifact = f"Artifact-{uuid.uuid4().hex[:6]}"
    narrative = f"Simulated event for {actor_id} → {artifact} at {time.time()}"
    context = {
        "pulse_strength": round(random.uniform(0, 1), 3),
        "sentinel_flags": random.sample(["alpha", "beta", "gamma", "delta"], 2),
        "graph_node": f"Node-{random.randint(1,20)}"
    }

    wiki_logger.log(
        actor_id=actor_id,
        event_id=event_id,
        artifact=artifact,
        narrative=narrative,
        context=context
    )

    print(f"Logged {event_id} for {actor_id} -> {artifact}")

print("Signal simulation complete: Graph populated with mock Sentinel Pulse data!")