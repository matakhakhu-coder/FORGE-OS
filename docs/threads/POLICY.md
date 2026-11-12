# Map lines and story threads: the rule

Owner decision: Matamela Ramovha, 11 November 2026, on approving the temporal-lines plan.

## What changed

Every checked batch up to now carries the rule "Standalone signals: no case, entity or map-line links." From 11 November 2026 that clause no longer forbids lines outright. A pin may be linked to another pin, or join a story thread, **only** under the rule below. The batch files keep their original text, because each one was approved as it stands; this file is the rule that now applies to links and threads, and `tools/checked_signals.py` and `tools/threads.py` enforce it at import and publish.

Second kind of line, 12 November 2026: **reported routes** join one South African case to a named city, port, airport or border post abroad, and show only in the "Reported routes" view. They have their own rule in `docs/routes/POLICY.md`; nothing below changes for threads.

## The rule

A line stands for something a source said, never for something we noticed.

1. **A source must tie the events together by name.** Each member of a thread needs its own source that places that event in the named conflict (for example "the CATA and CODETA route dispute"), with the quote and a link. Who says so is recorded (`claim_by`: police, source, court or `party: <name>`), and the page says it in words ("Codeta says").
2. **Never from closeness alone.** Same suburb, same week, same weapon, same kind of victim, or a pattern FORGE noticed is never a member and never a line.
3. **Arrests, charges, court dates and new victims are updates to the existing pin,** not new pins and not lines.
4. **Order in time, not cause.** Members are ordered by incident date. An arrow between two events is drawn only when their incident windows do not overlap; otherwise they are "same period, order unknown". Every view says: "Order in time. Sources place each event in this dispute. One did not cause the next."
5. **Context is text.** A ceasefire, a court order or a suspension is a dated entry in the thread's list with its source. It is never a pin and never a line end.
6. **Held candidates stay held.** Approving a link or a thread is not permission to publish a held incident. A held candidate joins only after it is published through its own approval and has its own source tying it to the conflict.
7. **Nothing public from a thread of one.** A thread reaches the page and `records.json` only when its status is `approved` and it has at least two approved members that are published pins. Until then it is checked and kept private.
8. **One-to-one follow-ons** (`follow_on` link) are allowed when the newer pin's source names the older event. The link sits on the newer pin, says who makes the claim, and is refused when the two incident windows overlap.
9. **Retraction.** A retracted pin cannot be a member; the import refuses a thread that still names one. Remove it from the thread, or retire the thread, in the same change.

## Where things live

- Threads: `docs/threads/<thread_id>.json` (schema `forge.thread`, version 1). `python tools/threads.py list` shows each thread and whether it is public.
- Pin-to-pin links: the `links` list on a checked signal, as before; `follow_on` is the new kind.
- Page: a pin in a public thread gets an "Over time" list in its drawer, the text twin of every line. "Show on map" opens thread view: numbered pins in time order, dashed lines, an arrow at each gap that is known, other pins dimmed. Back, Escape or "Back to live map" leaves it.
- Open data: `records.json` version 2 adds `threads` (public threads only) and a `thread_caveat`.
