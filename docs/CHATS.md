# Chat messages

*Status: implemented in [`extract/chat.py`](../src/adhocdocs/extract/chat.py)
(reading exports, making chat days) and
[`locators.py`](../src/adhocdocs/locators.py) (`msg=`).*

Chats are a large part of most collections now, and they don't fit the
document model as they come: a Slack channel can run for years in one
export, and a single message is too small to review on its own. So a chat
export goes in the way a mailbox does. The export is a container. Its
children are **chat days**: one document for each conversation on each day
that has messages, with that day's attachments as its children, as an
email's are. A day is the usual unit for reviewing chats and for
producing them (RSMF productions are commonly cut by day too).

## What is read

| Export | What it looks like | Times |
|---|---|---|
| **Slack** workspace export | a zip of `users.json`, `channels.json`, `groups.json`, `dms.json`, `mpims.json` and a folder of day files for each conversation (at the top, or inside one folder) | UTC |
| **WhatsApp** "Export chat" | `_chat.txt` or `WhatsApp Chat with ….txt`, alone or zipped with its media; iPhone (`[15/01/2024, 09:03:12] Name: …`) and Android (`15/01/2024, 09:03 - Name: …`) layouts | the phone's local time, as written |
| **RSMF** (Relativity Short Message Format) | an `.rsmf` email carrying `rsmf.zip`, or that zip alone, with `rsmf_manifest.json`. This is how chats are usually produced in discovery: Teams, Slack, iMessage, SMS, Bloomberg and others | as written, with their offsets |

Teams and iMessage are read through RSMF, which is what review platforms
and collection tools (Purview among them) produce for them. A Slack export
unzipped into a folder is read file by file, as JSON: zip it first.

Things that need care:

- **WhatsApp dates.** Whether `3/4/24` is March or April depends on the
  phone's settings, and the export doesn't say. It is read from the chat
  itself: a first number over 12 means day first, a second number over 12
  means month first. If nothing in the chat settles it, a 12-hour clock
  reads month first and a 24-hour clock day first, and every day says so
  in a `Note:` line in its header.
- **Slack files** are named but not in a standard export (Slack keeps only
  links). The day says `[attachment: name, not in the export]`.
  WhatsApp media and RSMF attachments are in their exports, and become the
  day's children.
- **Days** split at midnight in the times as the export gives them: UTC
  for Slack, the phone's local time for WhatsApp, and each message's own
  offset for RSMF. The header says which (`Date: 2024-01-15 (times UTC)`).

## A chat day

The text starts with a header block, then one message to a line (a
message over several lines keeps its lines):

```
Conversation: #shipping
Platform: Slack
Members: Alice Smith, Bob Jones
Date: 2024-01-15 (times UTC)

06:26 @Bob Jones has joined the channel
09:03 Alice Smith: Morning @Bob Jones, did the shipment go out?
    :eyes: Bob Jones
  ↳ 09:07 Bob Jones: Yes, this morning.
    (edited 09:08)
    [attachment: slip.pdf, not in the export]
```

The time and name, the `↳` of a reply in a thread, and the indented notes
(edited, deleted, reactions, attachments) are ours, as an email's header
lines are. What was written is literal, apart from Slack's markup, which
is shown as Slack shows it (`<@U123>` as `@Alice Smith`, a link as its
text). A reply whose thread started on an earlier day says `(in a thread
from earlier)`. Join and leave notices and WhatsApp's system lines have a
time but no sender.

Segments (TEXTLAYERS.md): `header` (named `conversation`, `platform`,
`members`, `date`, `note`); `message` over each message's whole block,
with its `id`, `author`, `name`, `time` (ISO), `parent` for a reply and
`event` for a notice; inside it `sender`, `message-text` (what was
written), `chat-note` and `attachment` (its `name`, and `part` when the
file is a child). The viewer shows senders in bold and the notes muted.

The day itself is stored as a small JSON file (`2024-01-15.chat.json`,
format `adhocdocs-chat-day`) made from the export, the way a PST's
messages are stored as emails: the conversation, that day's messages,
and the attachments, base64. That makes a day self-contained: it reads the
same wherever it is, whatever happens to the export.

Metadata: `kind: chat`, `platform`, `conversation`, `title` (`#shipping,
2024-01-15`), `date` and `end_date` (the first and last message),
`messages`, `participants` (who wrote that day), `zone`, and `from`/`to`
(addresses of those who wrote, and of members who didn't) where the
export has them (Slack profiles, RSMF participants), which is how chats
count toward `people`. The export itself is `kind: chat-export`, with
counts of conversations, days and messages.

## Citing messages

`msg=<id>` names a message, and `msg=<first>,<last>` a run of them
(LOCATORS.md). It resolves to the message as shown, sender and time
included, so a quote in a page reads `09:03 Alice Smith: Morning…`. IDs are
the platform's: Slack's `ts` (`1705309380.000100`), RSMF event IDs, and
for WhatsApp, which has none, the message's number within its day.

## Conversations

A conversation's days are a **thread** (METADOCS.md): `thread:D` is every
day of D's conversation, oldest first, and `thread+family:D` adds the
attachments. Copies of a day from several custodians are copies, as
copies of an email are. A conversation is known by the platform's own ID
where there is one (a Slack channel); otherwise by its name and who took
part, so two people's chats "with Alice" stay apart and two phones' copies
of one group chat come together.
