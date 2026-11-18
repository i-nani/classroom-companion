# Classroom Companion implementation note

The pilot follows one class from assignment creation through review. Teachers and students work in Telegram. The web pages expose outstanding work, recent class activity, submissions and feedback. Python and SQLite keep local setup small and make the interview scenario repeatable.

A person belongs to a school. Class membership grants teaching or learning access. A coordinator can invite a teacher or authorize a second teacher for a class. Each assignment owns shared instructions and a deadline, while each target has its own progress. Text submissions and teacher feedback retain their history. Queries for a class or submission start from the signed-in actor. The server checks membership before reading or changing records.

Private invitations bind a Telegram ID to a single roster entry. They expire after a day and support one redemption. A learner who joins late receives current work with the latest deadline. Names and handles are display information.

The model extracts assignments and deadlines, interprets progress, writes class briefings and words reminder messages. Code validates model output and controls recipients, transitions and permissions. Teachers preview an exact local deadline before publishing. Ambiguous student updates prompt for an assignment ID. Invalid model output leaves existing work intact.

A published assignment can be clarified, rescheduled or cancelled. A student can acknowledge, report progress, request help or submit. A teacher can complete the work or request a revision. Overdue is a computed flag, so it preserves a learner’s blocked status.

The scheduler uses class-local quiet hours. A blocked learner alerts a teacher. A silent learner near the deadline gets a progress check. Submitted and completed work stop generating reminders. Outbox entries carry assignment versions and reminder state, so an edit or submission can invalidate a queued message. Update IDs prevent duplicate incoming actions. Telegram errors trigger bounded retries. A crash after Telegram accepts a message can still repeat an outbound notification.

The pilot handles text submissions and uses one polling worker. Next improvements would move model calls outside write transactions, add durable worker leases, and evaluate interpretation on ambiguous student messages before piloting with real school data. The included offline scenario uses scripted provider replies and captured delivery; it tests application behavior independently of external accounts. OpenAI Codex assisted with implementation, tests, documentation and recording. No starter repository was used.

