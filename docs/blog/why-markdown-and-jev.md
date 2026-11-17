---
title: "Why does PAI use both Markdown documents and the JEV API?"
status: draft
date: 2026-11-12
---

# Why does PAI use both Markdown documents and the JEV API?

PAI is a small workbench for running coding agents on your own repositories. Two parts of it
look as if they could replace each other: plain Markdown files, which hold every task and
every piece of evidence, and JEV, an API from TypeSafe that answers typed questions with
probabilities. This post follows one task from intake to review to show why each does a
job the other cannot.

## The task starts as a note

You write a note in your Obsidian vault, `Tasks/rename-slugify.md`:

```markdown
---
status: inbox
priority: 2
checks: [unit]
acceptance:
  - All callers use make_slug
---
# Rename slugify to make_slug

Rename slugify() in utils/text.py and update its callers.
```

That file is the task. You can read it on a plane, edit it in any editor, and see every
change to it in Git or in your vault's history. Nothing about the task lives anywhere else.

## Intake: is this note ready to hand to an agent?

Markdown cannot answer that. The front matter is structured, but the body is prose, and
"is this specific enough to start without questions?" is a judgment. Asking a large
language model would work, but it costs far more tokens than the question deserves.

This is where JEV comes in, in two ways you choose explicitly. `pai_swarm.py triage` is a
command you run on `status: inbox` notes: it sends each note's title and body (after
removing email addresses, `key=value` secrets and claude.ai links) with two typed
questions, how likely the note is actionable and which tier of model it needs, and then
moves the note to `todo` (with a `model:` field) when it is clearly actionable, to
`needs-detail` when it clearly is not, and leaves it in the inbox in between. Each move is a
one-line change in a file you can read, revert or edit. `pai run --jev-triage` asks the
same questions about the notes a batch is about to take and only records the answer, for
example `jev_intake: actionable 0.93`; a low score prints a warning and the batch starts
anyway.

**When JEV is off or unavailable**, nothing stops. The intake line reads
`jev       a              rename-slugify: unavailable (skipped: TYPESAFE_API_KEY is not set)
(optional signal, not a gate)`, a timeout or malformed reply is reported the same way,
and `triage` leaves the inbox untouched. PAI never needs JEV to do its job.

## Work: the agent gets a pointer, not a copy

When the agent's input box is ready, PAI sends one line naming the note's path and its
title. The note stays the single source of truth; the agent reads it like you do. PAI
records that it sent the task once, and if it cannot tell whether the agent received it,
it says `dispatch unknown` and waits for you rather than sending it again.

## Review: evidence goes back into the note

`pai pr check a` runs only the checks you approved in your project configuration (a note
can name a check, never supply a command). It appends a `## Review` section to the same
note: each command, its exit code and run time, the commit SHA and author, a SHA-256 of
the exact snapshot the checks saw, and your acceptance items as unticked boxes. That
evidence is Markdown too, so you can read it offline, quote it in a PR, and see in a diff
exactly when it changed.

Markdown cannot tell you whether a report that says "done" is believable. With
`--jev-verify`, PAI asks JEV two more typed questions about the recorded results: does it
look finished, and were checks actually run. The answer is recorded next to the checks as
`agrees with the checks`, `DISAGREES`, or `unavailable`.

## Publication: checks and your decisions govern

`pai pr open a` runs the approved checks again at the exact commit it is about to push,
rejects unwanted Claude attribution and session links in the commits and the PR text, and
only then pushes. A failed check blocks the push. JEV saying "done" does not change that:
the recorded verdict reads "Jev says done but checks failed; the checks decide". You still
merge, in GitHub, yourself.

## Why not Markdown alone?

Plain files cannot weigh prose. They can say what the task is and record what happened,
but "is this ready?" and "does this evidence actually show completion?" need judgment.
Doing that judgment with a general model on every note and every review would cost more
than the work it protects. JEV answers narrow typed questions for a fraction of a cent: at
TypeSafe's listed $0.042 per million input tokens (output is free), our estimate for four
workers doing five tasks a night, twenty nights a month, is roughly one to four cents.

## Why not an API-only design?

If the task, its acceptance criteria, the evidence and the handoff lived in a service, you
could not read them offline, edit them in your own tools, or review their history in Git.
A failed request would stop the workflow. And the decision about what gets published would
sit behind a score you cannot inspect. In PAI the score is one more line in a file you
already read.

## What has been validated

The JEV parts have been tested against local mock endpoints only: the request shape, the
redaction, setup with and without a key, and how invalid keys, rate limits, timeouts and
malformed replies are told apart. No live JEV request has been made by the developer.
`pai jev check` sends exactly one request with a fixed synthetic task when you choose to
run it, and reports the usage TypeSafe returns.

Pricing source: [docs.typesafe.ai/models](https://docs.typesafe.ai/models) and the
[API reference](https://docs.typesafe.ai/api/), checked 2026-11-12. The token counts in the
estimate are PAI's own approximations, not TypeSafe's tokenizer.
