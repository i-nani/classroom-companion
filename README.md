# Classroom Companion

A Telegram-first classroom pilot. A teacher publishes work once, each learner has an independent progress record, and teachers review submissions and blockers in a small web workspace. The project uses Python, SQLite, Telegram long polling and an OpenAI-compatible language-model endpoint. The two web views share a URL and are selected by the signed-in user's class membership.

## Run the offline demonstration first

Use Python 3.10 or newer. From the directory containing this README, run:

~~~sh
python -m pip install -r requirements.txt
python -m unittest discover -v
python -m companion demo
python -m companion web --db .state/replay.sqlite3
~~~

Open **http://127.0.0.1:8080/** and sign in with a key printed at the end of the demo. Mira is the teacher/coordinator. Asha has completed work and feedback. Ravi has an outstanding assignment and a reminder. Use a separate browser profile or sign out to switch views. Open an assignment to see the submission history. The demo prints its message transcript and refuses to overwrite an existing database. Use `python -m companion demo --db .state/second.sqlite3` for another run, and give the web command the same path.

Offline replay uses explicit sample model responses and an in-memory Telegram transport. It exercises the same incoming-message handler, database, scheduler and delivery queue as live mode. Live mode always calls the configured model for natural-language work. No starter repository or framework scaffold was used. The implementation, documentation and recorded walkthrough were created with OpenAI Codex assistance.

## Run the live bot and web UI

1. Create a Telegram bot with **@BotFather** using `/newbot`. Save the bot handle and token. Use a dedicated bot with no active webhook or other poller.
2. Copy `.env.example` to `.env`. Set `TELEGRAM_BOT_TOKEN` and `MODEL_API_KEY`. The default model endpoint is the OpenAI Chat Completions API. Override `MODEL_BASE_URL` and `MODEL_NAME` for a compatible endpoint that supports JSON-object output. Use a model enabled in your account. For an unauthenticated local endpoint that ignores the authorization header, set a nonempty local placeholder.
3. Run `python -m companion setup` once. Save the three private invite commands and web keys in your local rehearsal notes. Give each participant only their own invitation. Handles and display names do not grant access. In a live rehearsal use three distinct Telegram accounts.
4. Start `python -m companion worker` in one terminal. Start `python -m companion web` in another terminal from the same directory. Both use `.state/classroom.sqlite3` by default. Long polling only requires outbound access to Telegram and the model endpoint. A public tunnel is unnecessary for the bot.
5. Open the new bot in each account's private chat. Link Mira and Asha first using the printed commands. Keep Ravi's invitation for the late-link step below. Each user can send `/web` to rotate their private sign-in key. The web interface is on the host's loopback address.
6. For the **live integration check**, create and publish a fresh task. Verify delivery on Asha’s device, send an original progress sentence, ask for `/summary`, and compare the Telegram response, web state and worker logs. Exercise reminders during the rehearsal described below.

### Configuration

| Variable | Purpose |
|---|---|
| TELEGRAM_BOT_TOKEN | Private BotFather token, required by the worker |
| MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME | Live language-model credentials, base URL and model ID |
| CLASS_TIMEZONE | IANA timezone applied to newly created classes, default Asia/Kolkata |
| CLASSROOM_DB | Optional database path shared by CLI, web and worker |
| COOKIE_SECURE | Set to 1 when serving the web interface over HTTPS |
| DEMO_CLOCK_FILE | Optional private file for a simulated rehearsal clock; empty in real operation |

## Ten-minute interview runbook

To demonstrate a silent learner immediately, configure `DEMO_CLOCK_FILE=.state/rehearsal-clock` on your **fresh rehearsal database** before setup or starting either process. Set a morning time in the class timezone, for example `python -m companion clock --at 2026-11-18T10:00:00+05:30`. Later advance it to noon on that same date. This moves the demo clock without touching any assignment records. Use a separate database and leave the variable empty for ordinary operation.

| Actor | Action | Observable result |
|---|---|---|
| Mira | Send “Assign problems 1–5 from chapter 4 by tomorrow evening.” | A draft shows the exact date, timezone and recipient count. |
| Mira | Send `/publish 1`, then `/edit 1 Move the deadline to 10 am two days from today`. | Asha receives the work and correction. The assignment keeps ID 1. |
| Ravi | Redeem his private invite. | Ravi gets the current instructions and revised deadline. |
| Asha | Send “Finished the first two” and “I'm stuck on the fractions in question 3.” | Her status changes independently of Ravi’s. |
| Mira | Advance the rehearsal clock to noon, then click **Check my classes for reminders** or run `python -m companion remind`. Ask the bot for `/summary`. | The worker delivers a teacher alert about Asha and checks in with Ravi. A repeated scan creates no duplicate reminders. |
| Asha | Send `/submit 1 I used a common denominator. My answer is 7/8.` | Mira gets a submission ID. Her web view shows work awaiting review. |
| Mira | Send `/feedback 1 complete Good use of a common denominator.` | Asha receives feedback. Her web detail page shows the answer, feedback and completed state. |
| Ravi | Sign in as Ravi and open `/work/1?class=1&student=2`. | The server returns 404. Ravi's own assignment remains visible. |

IDs above assume a fresh setup. Use the IDs in the bot replies for later runs. The CLI reminder command enqueues work. Keep the worker running to deliver it.

## Architecture

~~~mermaid
flowchart TD
  T["Telegram private chat"] --> P["Polling worker"]
  P --> H["Message handler"]
  H --> L["Language model"]
  H --> D[("SQLite")]
  W["Teacher and student web views"] --> D
  W --> S["Reminder scan"]
  P --> S
  S --> L
  S --> D
  D --> Q["Outbox delivery"]
  Q --> T
~~~

| File | Responsibility |
|---|---|
| companion/store.py | Schema, transactions, school membership, hashed invitations, clocks and seeding |
| companion/workflow.py | Message routing, class authorization, draft confirmation, student lifecycle and feedback |
| companion/model.py | Provider adapter, JSON validation, timestamp checks and bounded text |
| companion/jobs.py | Reminder eligibility, current-state checks, outbox retries and Telegram long polling |
| companion/web.py | Scoped queries, private views, session cookies, CSRF validation and manual scan |
| companion/demo.py | Scripted offline scenario with isolated provider and delivery fixtures |
| tests/test_companion.py | Reproducible integration and failure-path coverage |

## Product decisions and operations

- A class can have several teachers. A coordinator can use `/teacher NAME` to create and invite a teacher, or `/share TEACHER_ID` to add an existing teacher from the same school to the selected class. A teacher can use `/newclass NAME`, `/student NAME`, `/roster` and `/invite ID`. Use `/classes` and `/class ID` to change context. The coordinator uses the teacher workspace for classes where they teach.
- The default audience is the selected class. Use `/to 2,3 TASK WITH DEADLINE` for one learner or a subset, then publish. An invite identifies one roster record and class, expires in 24 hours, and is consumed once. A fresh invite invalidates earlier ones for that membership. An unlinked learner can collect current work when they join.
- Assignment stages are draft, assigned and cancelled. Each recipient moves among assigned, acknowledged, in-progress and blocked, then submitted. The reviewer chooses needs-revision or completed. A revised answer creates another submission record. Overdue is a time-based flag on open work so a learner can remain blocked and overdue at once. `/clarify ID TEXT`, `/edit ID NEW DATE` and `/cancel ID` update the assignment version.
- The class timezone controls displayed deadlines, relative-time interpretation and quiet hours. The model receives an explicit clock. “Evening” defaults to 18:00. A draft makes the interpreted deadline visible before publication. A student with several open assignments uses `/progress ID MESSAGE`. Malformed model responses roll back the action, and ambiguity prompts a clarification.
- Reminders run during 08:00–19:59 class time. Blocked learners generate a help request to a linked class teacher, preferring the assignment creator. Other open work qualifies within 48 hours of the deadline or when overdue, after at least an hour without activity. Limit one alert of each kind per learner/assignment/local day. Unlinked learners stay visible on the roster. A successful submission pauses reminders. Manual scans use exactly the same policy.
- Incoming Telegram update IDs and actions are recorded in one transaction. Duplicate updates replay safely. Explicit submit/publish transitions also tolerate repeated commands. An outbox stores messages alongside state changes. Before dispatch, the worker drops stale versions and stale reminder status. A restart resumes the queue. Transient delivery failures back off; permanent errors or ten unsuccessful attempts mark the message failed. Use `python -m companion status` to inspect counts. Monitor worker output for model failure, submission received, feedback sent and reminder sent. Delivery logs omit message bodies and tokens.
- To restart, stop with Ctrl+C, retain the SQLite directory and .env, and run the same web and worker commands. Run only one poller per bot. Back up with SQLite's backup API or stop both processes before copying the state directory. For an interview environment use a fresh database path to repeat setup. If polling returns 409, stop the other poller or remove the previous webhook using the bot owner’s Telegram configuration.
- Scope decisions: text submissions with revision history; unsupported attachments get a recovery message. This pilot uses synchronous provider calls, SQLite and one worker. External delivery can repeat if the process stops after Telegram accepts a message and before acknowledgment is committed. The UI refreshes on navigation. Class rosters, student messages and prompts need a retention and provider-consent policy before a real school pilot. For production, add worker leases, a dedicated web server with TLS, key rotation/session revocation, model evaluations and file handling. The seeded long web keys are intended for a private pilot.
- The recording is an offline replay. Configure your own service credentials and carry out the live integration check above before calling it a live Telegram demonstration.

See [the implementation note](docs/implementation-note.md) for the submission summary.

