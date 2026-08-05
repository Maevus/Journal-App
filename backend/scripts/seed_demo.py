"""Seed a self-contained demo database for showcasing the app.

Builds ~60 hand-crafted entries spanning ~6 months across all three sources,
mood scores matching each entry's tone, so chart/filter/drill-down/synthesis
all have something real to show. Never touches the real `journal.db`.

Semantic search is dormant here — the vector store is pgvector (Postgres-only),
so `embed_and_store` no-ops on a SQLite target. Keep external_ids are fake
(`demo-keep-…`); "Open in Keep" links render but won't resolve. Timestamps are
relative to "now" so the demo always looks fresh.

Usage (from backend/):

    .venv/bin/python scripts/seed_demo.py     # rebuilds demo db from scratch
    APP_MODE=demo uvicorn main:app            # serve the demo stack

APP_MODE=demo also turns Keep sync off, so real notes can't sync into the demo db.
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

# DATABASE_URL set → seed the shared Postgres demo user (shape B, search works).
# Unset → fall back to the SQLite demo db (APP_MODE=demo, search dormant).
# Must be set before importing config, which reads the env at import time.
if not os.getenv("DATABASE_URL"):
    os.environ["APP_MODE"] = "demo"

from sqlmodel import Session, select  # noqa: E402

import config  # noqa: E402
from clock import utcnow_naive  # noqa: E402
from models.journal import JournalEntry, engine  # noqa: E402
from models.keys import UserApiKey  # noqa: E402
from models.usage import LLMUsage  # noqa: E402
from models.user import User, UserSession  # noqa: E402
from services.auth import DEMO_EMAIL, DEMO_GOOGLE_SUB  # noqa: E402
from services.embeddings import embed_and_store  # noqa: E402
from services.ingestion import compute_hash  # noqa: E402

# (days_ago, hour-of-day, source, mood_score, content) — arc: settling in →
# crunch → burnout dip → recovery → upswing → wobble → strong stretch → flu → rebound.
DEMO_ENTRIES: list[tuple[int, int, str, float, str]] = [
    (168, 21, "goodnotes", 0.62,
     "New notebook, fresh start. Moved my desk next to the window and the whole room "
     "feels different. Wrote a list of things I want more of this year: climbing, "
     "actual sleep, finishing things. Short list on purpose — past me always wrote "
     "ten goals and resented all of them by February."),
    (166, 18, "google_keep", 0.70,
     "Climbing gym opened around the corner!! Tried the V2s, arms are jelly, grinning anyway."),
    (164, 9, "text", 0.58,
     "Morning brain dump: picking up running again, two mornings a week, nothing heroic. "
     "Also need to stop buying notebooks as a substitute for using notebooks."),
    (161, 22, "goodnotes", 0.65,
     "Good week. Lighthouse project kickoff went well and pairing with Sam is genuinely "
     "fun — we caught each other's gaps all afternoon. Slept eight hours two nights in a "
     "row, which feels illegal. Want to remember this pace exists."),
    (158, 10, "google_keep", 0.55,
     "Forgot the standup AGAIN. Set three alarms this time. Three."),
    (156, 17, "google_keep", 0.68,
     "Long walk by the canal. Herons. Brain went quiet for a whole hour, no podcast needed."),
    (154, 21, "goodnotes", 0.60,
     "Noticing the pattern again: mornings are gold, afternoons are soup. Rearranged my "
     "calendar so the deep work happens before lunch and meetings get the soup hours. "
     "If it works it works."),
    (151, 20, "text", 0.52,
     "Meh day. Doomscrolled longer than I'd like to admit, but cooked an actual dinner "
     "with vegetables in it, so we call it a draw."),
    (149, 22, "goodnotes", 0.50,
     "Lighthouse scope grew again. Third time. Trying not to panic about it — wrote the "
     "whole thing out as a list and it's big but it's finite. Lists are load-bearing "
     "at this point."),
    (146, 13, "google_keep", 0.45,
     "Three meetings back to back. Whose idea was this. (Mine. It was mine.)"),
    (144, 19, "google_keep", 0.48,
     "Deadline moved up two weeks?? Cancelled climbing. Again. Forearms and I are both sad."),
    (141, 23, "goodnotes", 0.40,
     "Crunch mode. Working late every night this week, skipped both runs, and I was "
     "short with Anna on the phone for no reason. Called her back to apologise. She said "
     "'you always do this in February' and honestly, rude, but also data."),
    (138, 22, "text", 0.35,
     "Everything is Lighthouse. My dreams are Lighthouse. I opened this app to write "
     "about something else and I cannot think of a single other topic."),
    (136, 8, "google_keep", 0.30,
     "Slept 5h. Coffee number four. Hands are buzzing and not in a fun way."),
    (133, 23, "goodnotes", 0.32,
     "Hyperfocused the entire weekend on the migration script. Looked up and it was "
     "Sunday night: missed Dad's call, ate cereal twice, didn't go outside. The work is "
     "good but I know this trade and I keep making it anyway. Guilt sitting heavy tonight."),
    (131, 16, "google_keep", 0.28,
     "Code review came back rough. Took it personally. I know I shouldn't. Took it personally anyway."),
    (128, 14, "google_keep", 0.22,
     "Cried in the bathroom at work. Cool cool cool. Totally fine. Everything's fine."),
    (126, 23, "goodnotes", 0.18,
     "Can't start anything. Sat down to write the deploy plan and just stared at the "
     "wall for I don't know how long. It's not laziness, it's like the ignition is gone. "
     "Scared that this is the burnout everyone warned me about. Don't want to tell anyone. "
     "Writing it here so at least it's said somewhere."),
    (123, 19, "text", 0.25,
     "Told Sam I'm drowning. Just said it, out loud, in the kitchen at work. They took "
     "two tickets off my plate before I finished the sentence. Relief and shame in equal "
     "measure, but mostly relief."),
    (120, 18, "goodnotes", 0.30,
     "Therapy. She named it before I did: burnout, the regular kind, the kind people "
     "recover from. We made a boring little plan — fixed sleep window, no laptop after "
     "nine, one walk a day. It felt almost insultingly small. She said small is the point."),
    (117, 21, "google_keep", 0.35,
     "Day three of the laptop curfew. Read an actual paper book. Weird. Good weird."),
    (114, 18, "google_keep", 0.40,
     "Went back to the climbing gym. Just easy traverses, nothing scary. The chalk smell "
     "alone fixed something."),
    (111, 22, "goodnotes", 0.45,
     "Energy is coming back in patches, like sun between clouds. Did two pomodoros this "
     "morning and stopped while it still felt good — that's the new experiment, stopping "
     "while it's fun instead of squeezing the lemon to rind. Counterintuitive. Promising."),
    (108, 17, "text", 0.50,
     "Lighthouse demo went... fine?? Nobody died. The bug I was sure would surface didn't. "
     "Note to self: the catastrophe is usually imaginary."),
    (105, 20, "google_keep", 0.55,
     "Anna visited. We made dumplings, there was flour absolutely everywhere, laughed "
     "until I wheezed."),
    (102, 21, "goodnotes", 0.58,
     "Quietly good week. Sleep mostly over seven hours, focus noticeably better, and the "
     "monstera grew a new leaf, which I'm taking full credit for. Named the plant Brian. "
     "No regrets."),
    (99, 7, "google_keep", 0.60,
     "Run in the rain. Soaked through. Completely alive."),
    (96, 19, "goodnotes", 0.65,
     "The light is coming back in the evenings and so am I, apparently. Planted basil "
     "and mint on the balcony. There's a version of me that rolls eyes at 'gardening "
     "fixed my brain' and that version has never grown basil."),
    (93, 19, "google_keep", 0.72,
     "SENT THE V3. The one I've been projecting for weeks. The whole gym clapped. "
     "(Two people. But still.)"),
    (90, 16, "text", 0.68,
     "Lighthouse phase one shipped. The retro was kind — people said 'sustainable pace' "
     "unprompted. Maybe I'm okay at this job."),
    (87, 15, "google_keep", 0.75,
     "Birthday picnic for Jess. Sun, profoundly bad frisbee, excellent cake."),
    (84, 21, "goodnotes", 0.70,
     "Writing down what's actually working so future-me can find it during the next dip: "
     "body-doubling with Sam for boring tasks, timers for everything, morning deep work, "
     "and forgiving myself faster when a day goes sideways. The forgiving part does more "
     "than the timers, honestly."),
    (81, 9, "google_keep", 0.62,
     "Slept badly but didn't spiral about it. Growth???"),
    (78, 20, "goodnotes", 0.74,
     "Weekend at Mum and Dad's. Walked the dog along the old route, helped Dad stake the "
     "tomatoes, got gently roasted about my houseplant having a name. Came home with eggs, "
     "rhubarb, and the particular calm that only that house produces."),
    (75, 14, "google_keep", 0.80,
     "Promotion conversation went WELL. Manager used the word 'indispensable'. Floating."),
    (72, 18, "text", 0.66,
     "Busy week but the good kind of busy — the kind where you're tired like after "
     "climbing, not tired like after a fight."),
    (69, 8, "google_keep", 0.70,
     "Morning pages actually helping. Brain less like a browser with ninety tabs open."),
    (66, 22, "goodnotes", 0.60,
     "Overcommitted again — three social things in one weekend because each one sounded "
     "lovely on its own. Cancelled one, apologised, survived the guilt (mostly). "
     "Calendar tetris is still my weakest event."),
    (63, 19, "google_keep", 0.55,
     "Rain all week. Joints grumpy. Made soup, watched gloriously bad movies."),
    (60, 21, "goodnotes", 0.50,
     "Energy dipped this week and the old fear showed up right on cue — 'here we go "
     "again'. But it's not February. I'm sleeping, I'm climbing, it's just a dip. "
     "Being patient with it instead of panicking is new. Let's see."),
    (57, 15, "google_keep", 0.45,
     "Focus completely shot today. Wrote four lines of code, deleted six. Net negative programmer."),
    (55, 20, "text", 0.52,
     "Middling day but kept the streak: gym, journal, dishes. The boring trifecta holds "
     "the whole thing up."),
    (52, 21, "goodnotes", 0.60,
     "And we're back. Great pairing session with Sam on the gnarly caching bug, hard "
     "climb after work, asleep by eleven. The dip lasted nine days. Last time it was "
     "nine weeks. Writing that down so I believe it."),
    (49, 12, "google_keep", 0.65,
     "Brian has THREE new leaves. Plant dad of the year. No further questions."),
    (46, 20, "goodnotes", 0.68,
     "Trip planning with Anna — Lisbon in June! Spent a whole evening on tram maps and "
     "pastel de nata research ('for science'). Having something to look forward to is "
     "doing visible things to my mood graph, I'd bet money on it."),
    (43, 16, "google_keep", 0.72,
     "Quarterly review: 'exceeds expectations.' February me would not believe this. "
     "February me was crying in a bathroom."),
    (40, 18, "text", 0.70,
     "Six months ago the wheels were coming off and I couldn't see it. Reading back "
     "through the winter entries is like watching someone walk into a glass door in "
     "slow motion. Grateful for the records. Grateful it's spring."),
    (37, 17, "google_keep", 0.78,
     "Perfect day off: market, climb, nap, unreasonable amount of pasta. Repeat forever pls."),
    (34, 21, "goodnotes", 0.75,
     "Long grateful entry because why not: friendships feel deeper this year, work is "
     "demanding but sustainable, the flat has plants in it, and I still lose my keys "
     "every single morning but who cares. The keys were never the real problem."),
    (31, 11, "google_keep", 0.80,
     "Led my first project kickoff. Didn't die. Possibly nailed it. Several nods observed."),
    (28, 22, "goodnotes", 0.82,
     "Shipped, slept, socialised — the trifecta, all in one week, without borrowing "
     "energy from next week to do it. This is what I thought 'having it together' "
     "would feel like. It's quieter than expected. It's mostly just Tuesday."),
    (25, 18, "google_keep", 0.85,
     "OUTDOOR climbing for the first time. Real rock!! Terrified, ecstatic, scraped everywhere."),
    (22, 19, "text", 0.76,
     "Good stretch continues. Knocking on every wooden surface in the flat."),
    (19, 21, "google_keep", 0.50,
     "Throat tickle. Ignoring it professionally."),
    (18, 10, "google_keep", 0.35,
     "Definitely sick. Cancelled everything. Deeply resentful of my own immune system."),
    (16, 20, "goodnotes", 0.32,
     "Proper flu. Fever dreams about unanswered Slack messages, which feels like a "
     "personal attack from my own subconscious. Frustrated mostly about losing momentum — "
     "the streak, the climbing, the rhythm. Trying to remember that rest is not a detour."),
    (14, 15, "text", 0.40,
     "Still horizontal. Watched three documentaries about volcanoes. Anna is sending "
     "soup memes on a strict schedule."),
    (12, 13, "google_keep", 0.45,
     "First day mostly upright. Soup is fine. The walls were getting boring anyway."),
    (10, 18, "goodnotes", 0.55,
     "Gentle walk to the corner and back, like a Victorian convalescent. Opened the "
     "inbox: not the apocalypse. The body wants to ease back in and for once I'm "
     "going to let it."),
    (8, 9, "google_keep", 0.65,
     "Back at work, half days. The team just... covered everything. Soft feelings about it."),
    (6, 21, "goodnotes", 0.72,
     "Fully back, and here's the headline: I got sick, lost two weeks, and didn't "
     "spiral. February me would have catastrophised the lost momentum into a verdict "
     "about my whole character. June me made tea and watched volcano documentaries. "
     "That's the difference six months makes."),
    (4, 18, "google_keep", 0.78,
     "First climb post-flu. Weak as overcooked spaghetti but SO happy. The V3 will wait."),
    (3, 20, "text", 0.70,
     "Lisbon packing list drafted. Printing it on paper because I WILL lose the note. "
     "Some things about my brain are weather, not climate."),
    (1, 22, "goodnotes", 0.80,
     "Last journal before the holiday. Calm and excited at the same time, which used to "
     "feel impossible — excitement always came bundled with dread. Six months of dots on "
     "a chart say otherwise now. Bring on the trams and the custard tarts."),
    (0, 8, "google_keep", 0.82,
     "Bags half packed. Brian entrusted to Jess with a two-page care document. Lisbon in 48h!!"),
]


def _run_migrations() -> None:
    """Bring the target DB to head via Alembic — builds `entry_embeddings` on
    Postgres (so demo search works); no-op if already at head."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


def _wipe_demo_user(session: Session) -> None:
    """Idempotent reset — delete ONLY the demo user's rows, never anyone else's.

    Deletes the demo user's owned rows first (FKs block deleting the user
    otherwise); entry_embeddings cascades from journalentry on Postgres.
    """
    demo = session.exec(select(User).where(User.google_sub == DEMO_GOOGLE_SUB)).first()
    if demo is None:
        return
    for model in (JournalEntry, LLMUsage, UserApiKey, UserSession):
        for row in session.exec(select(model).where(model.user_id == demo.id)).all():
            session.delete(row)
    session.commit()  # clear children before the parent (FK order)
    session.delete(demo)
    session.commit()


def _demo_reasoning(mood: float) -> str:
    """Canned, score-band reasoning so demo entries have hover text without an
    LLM call (real entries get a per-entry reasoning from the scorer)."""
    if mood >= 0.7:
        return "Upbeat and energised — clear positive momentum and a sense of things going well."
    if mood >= 0.55:
        return "Generally positive with a steady, content tone; small wins outweigh the friction."
    if mood >= 0.4:
        return "Mixed and in-between — some progress, some strain, no strong pull either way."
    if mood >= 0.25:
        return "Low and effortful; signs of stress, fatigue, or things feeling stuck."
    return "Heavy and depleted — a clearly difficult, low-energy stretch."


def main() -> None:
    _run_migrations()  # schema to head (builds entry_embeddings on Postgres)

    now = utcnow_naive()
    goodnotes_page = 0
    counts: dict[str, int] = {}

    with Session(engine) as session:
        _wipe_demo_user(session)  # idempotent, demo-scoped — never touches real users
        # The seeded demo account. Signed-out visitors resolve to it under
        # PUBLIC_DEMO; its entries are what they see.
        demo_user = User(google_sub=DEMO_GOOGLE_SUB, email=DEMO_EMAIL)
        session.add(demo_user)
        session.commit()
        session.refresh(demo_user)

        for i, (days_ago, hour, source, mood, content) in enumerate(DEMO_ENTRIES):
            timestamp = (now - timedelta(days=days_ago)).replace(
                hour=hour, minute=(i * 17) % 60, second=0, microsecond=0
            )
            timestamp = min(timestamp, now)  # day-0 entries must not land in the future

            if source == "google_keep":
                external_id: str | None = f"demo-keep-{i:03d}"
            elif source == "goodnotes":
                goodnotes_page += 1
                external_id = f"Demo Journal.pdf#p{goodnotes_page}"
            else:
                external_id = None

            entry = JournalEntry(
                source=source,
                content=content,
                timestamp=timestamp,
                mood_score=mood,
                mood_reasoning=_demo_reasoning(mood),
                content_hash=compute_hash(content),
                external_id=external_id,
                imported_at=now,
                user_id=demo_user.id,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            # Populates pgvector on Postgres (demo search works); no-ops on the
            # SQLite fallback (search dormant there).
            embed_and_store(entry.id, content, user_id=demo_user.id)
            counts[source] = counts.get(source, 0) + 1

    span_days = max(days_ago for days_ago, *_ in DEMO_ENTRIES)
    print(f"Seeded {sum(counts.values())} entries over the last {span_days} days")
    print(f"into {config.DATABASE_URL} as the demo user (id {demo_user.id}):")
    for source, n in sorted(counts.items()):
        print(f"  {source:12s} {n}")
    print(
        "\nServe it: point the app at the same DB and set PUBLIC_DEMO=1 so signed-out\n"
        "visitors see the demo (or APP_MODE=demo for a standalone everyone-is-demo run)."
    )


if __name__ == "__main__":
    main()
