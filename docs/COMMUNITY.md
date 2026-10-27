# Appeals, help threads, feedback and the welcome message

Four features that help members and save moderators time. They need no other bot.

| Feature | Members see | Moderators see |
| --- | --- | --- |
| [Appeals](#appeals) | An **Appeal** button in their warning DM | An appeal in the mod log with **Accept** and **Reject** |
| [Help threads](#help-threads) | `/help` opens a private thread with the bot | A **Get a human** request, and a `/feedback` list |
| [Feedback](#feedback-on-answers) | 👍 👎 under every answer | `/feedback` lists answers marked unhelpful |
| [Welcome message](#welcome-message) | A greeting when they join | Nothing to do |

Everything here can be set from `/settings` → **Community** (and **Moderation → Allow appeals**). See [SETTINGS.md](SETTINGS.md).

## New permissions

Help threads need two permissions the bot didn't have before: **Create Private Threads** and **Manage Threads**. A bot that is already in your server doesn't get them automatically. Do one of these:

- Open **Server Settings → Roles**, pick the bot's role, and turn both on. Or
- Use the new invite link in [SETUP.md](../SETUP.md) to re-invite the bot. It keeps its role and settings.

`/status` lists any permission that is still missing.

## Appeals

When the bot warns a member and sends them a DM, the message now has an **✉️ Appeal this warning** button.

1. The member presses it and writes why the warning was a mistake (10 to 1,000 characters).
2. The bot posts the appeal in your mod-log channel, linked to the case, with **Accept appeal** and **Reject appeal**.
3. A moderator decides. Moderators need **Moderate Members**.
   - **Accept** works like "Not a violation": the warning is removed, any timeout is lifted and the case closes.
   - **Reject** keeps the case as it is.
4. The member gets a DM with the result either way.

Rules:

- A case can be appealed **once**.
- Only the member the warning was for can appeal it.
- A case that ended in a ban, or was already dismissed, can't be appealed.
- A case a moderator closed with "timeout lifted, warning kept" **can** still be appealed.
- The appeal and its outcome are added to the case report's history.
- If a member has DMs closed, they don't get the warning, so they have no button. They can ask a moderator directly.
- Turn it off with `APPEALS_ENABLED=false` (the DM then tells members to message a moderator).

Buttons keep working after the bot restarts, because appeals are stored in the database.

## Help threads

`/help question:…` gives a member a **private thread** with the bot.

1. The bot creates a private thread, adds the member, and answers their question there, using the docs as the source of truth.
2. The member keeps typing in the thread and the bot keeps answering. Only the member who opened the thread gets answers, so a moderator who joins can talk freely.
3. **Get a human** pings your staff role (if you set one) and posts a note in the mod log with a link. The bot then **stops answering** in that thread.
4. **Close this thread** archives and locks it. The member or a moderator can close it. A thread also archives itself after a day of silence.

Good to know:

- Each member can have **one open help thread** at a time, and `/help` has a one-minute cooldown.
- Private threads are visible only to the member, the bot, and people with **Manage Threads** (moderators and administrators usually have it). A role ping does not add people to a private thread, so give your moderator role **Manage Threads**.
- The bot's moderation still checks messages inside help threads.
- Where the thread is created: set a **Help channel** in `/settings`, or leave it empty and the thread is made in the channel where the member typed `/help`.
- `HELP_STAFF_ROLE_ID` is the role pinged by **Get a human**. Empty means only the mod-log note.
- Turn the feature off with `HELP_ENABLED=false`.

## Feedback on answers

Every answer from `/ask`, a mention or a help thread has 👍 and 👎 buttons under its last message.

- Anyone can vote once per answer. Pressing the same button again takes the vote back, and pressing the other one changes it.
- The button shows the running count.
- Moderators run **`/feedback`** (Moderate Members) to see the answers that have more 👎 than 👍, newest first, with the question, the answer and a link.
- That list is the most useful thing the bot gives you for improving the docs: a question that keeps getting 👎 usually means the docs don't cover it, or cover it badly.

Only the question, the answer and the votes are stored, in `data/moderation.sqlite3`. Voters are stored by member ID, so nobody is voted for twice. Answers from before this feature existed have no buttons.

## Welcome message

When a member joins, the bot posts a greeting in the channel you choose. It is **off** until you pick a **Welcome channel** in `/settings` → Community.

The text can use these placeholders:

| Placeholder | Becomes |
| --- | --- |
| `{user}` | A mention of the new member |
| `{server}` | The server's name |
| `{rules}` | A link to your rules channel |
| `{help}` | `/help` |

The default message greets the member, points to the rules, and explains `/ask` and `/help`. Change it with **Welcome message**. An empty value restores the default. The message is plain text, uses no AI, and costs no AI quota. Only the new member is pinged. Bots that join don't get a welcome.

**It needs one extra setting in the Developer Portal.** Hearing who joined is a privileged permission. Open your application at discord.com/developers → **Bot** → **Privileged Gateway Intents** and turn on **Server Members Intent**, then restart the bot.

If you set a welcome channel before turning that on, the bot **doesn't go offline**. It starts without the welcome message and posts a warning in the mod log telling you what to switch on.

## Where things are stored

All of it is in `data/moderation.sqlite3` next to the cases and warnings: appeals, answers and votes, and help threads. Back that file up with the rest of `data/`.
