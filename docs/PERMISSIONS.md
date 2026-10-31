# Permissions: exactly what to give the bot

Use the invite link in [SETUP.md](../SETUP.md) step 2 and you get exactly this list, nothing more. `/status` checks it for you.

## Give it these 13

Permission|Why the bot needs it
-|-
View Channels|See the channels it moderates and answers in
Send Messages|Answer questions and post case reports
Send Messages in Threads|Answer in `/help` threads
Embed Links|Case reports and warnings are embeds
Attach Files|Attach screenshot evidence to case reports
Read Message History|Read the rules channel and recent chat for context
Add Reactions|⏳ when someone asks again too fast
Manage Messages|Delete scam and piracy messages
Moderate Members|Time members out until a moderator reviews their case
Ban Members|Ban, **only** when a moderator presses Ban and confirms
Manage Server|Read Discord's AutoMod slur filter, used for hate speech
Create Private Threads|Open `/help` threads
Manage Threads|Close `/help` threads

Permissions number for the invite link: `1460289006692`.

## Never give it these

**Administrator** · Manage Roles · Manage Channels · Manage Webhooks · Mention @everyone · Kick Members · Manage Nicknames · Manage Expressions · Manage Events · View Audit Log · Create Invite · Move, Mute or Deafen Members

The bot never uses any of them, and each one would let a stolen token do real damage. If it has any, `/status` and the mod log tell you which to remove.

## In the Developer Portal (Bot page)

- **Message Content Intent:** on. Required, or the bot can't read messages.
- **Server Members Intent:** on only if you use the welcome message.
- **Presence Intent:** off.
- **Public Bot:** off, so only you can invite it.
- Invite scopes: `bot` and `applications.commands`. Nothing else.

## Role and channels

- **Bot role position:** above normal members, **below your staff roles**. It can then moderate members but can never touch your moderators.
- **Mod log:** a private channel only staff and the bot can see.
- **Rules channel:** the bot needs to see it and read its history. It never posts there.
- **Channels it should skip:** deny it View Channel there.

## What your own staff need

- **Moderate Members:** case buttons, `/warn`, `/warnings`, `/tune`, `/brake`, `/feedback`.
- **Ban Members:** the Ban button.
- **Manage Server:** `/status`, `/settings`, `/guardrails`, `/ai-mode`, `/reload-docs`.
- **Administrator** (or the server owner): keys, AI provider and the 🔒 settings in `/settings`.

[← Back to the README](../README.md)
