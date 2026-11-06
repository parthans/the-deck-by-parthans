# The daily list and the reading roundup

Deck can show two things that change every day:

- **Focus**, on the Home page: 3 to 5 tasks for today. They arrive as *Proposed*. When you tap **Block**, Deck puts each one in a free 45-minute slot on your calendar. Nothing touches your calendar before that tap.
- **Read**, its own tab: a morning roundup of articles, posts and podcasts, by section.

Deck does not write either file. Something else does, every morning: a scheduled Claude task, a script, or you. Deck reads both from a GitHub repository with a read-only token, so the writer can run anywhere and your Mac can be asleep when it does.

## Setting it up

1. Pick a **private** GitHub repository for the files (a new empty one is fine).
2. Make a fine-grained token: GitHub, Settings, Developer settings, Fine-grained tokens. Give it that one repository with **Contents: Read-only**.
3. In `config.json`, set `plan.repo` (for example `"you/daily"`) and `plan.token`. Set `roundup.repo` too if the roundup lives in the same repository; the roundup uses the plan token unless you give it its own `token`.
4. Have your morning job write the files below, dated in your own time zone.

`{date}` in a path becomes today's date, `YYYY-MM-DD`. If today's roundup has not arrived, the Read tab shows yesterday's and says so.

## The list: `_plan/YYYY-MM-DD.json`

```json
{
  "date": "2026-11-06",
  "note": "One short line on why this order.",
  "tasks": [
    { "id": "t1", "title": "Send the proposal to Acme", "why": "They asked for it on Tuesday",
      "kind": "signature", "source": "Email" },
    { "id": "t2", "title": "Outline the first case study", "why": "The site has none yet",
      "kind": "case-study", "source": "Website" }
  ]
}
```

- `date` must be today, or Deck ignores the file.
- Up to 5 tasks are shown, in file order. `kind` and `source` are short labels; `kind: "signature"` is highlighted (work that brings in a signed client).
- Blocks go between `day_start` and `day_end`, skip `lunch`, keep `buffer_minutes` clear around meetings and `gap_minutes` between blocks. Each block is `block_minutes` long.
- Each block's notes carry a marker (`Deck task <date>-<id>`). Deck only ever renames or removes events with that marker: ticking a task renames its block to `Done: …`, and **Change blocks** removes Deck's blocks so you can approve again.
- A morning job can read yesterday's blocks (titles starting `Focus:` or `Done:`) to carry over what you did not finish.

## The roundup: `_roundup/YYYY-MM-DD.md`

```markdown
---
title: Morning roundup, 6 Nov 2026
date: 2026-11-06
items: 2
---

One or two sentences opening the day.

## Work

### Headline in plain words
A summary with the facts and full context, so you only click through if you want more.

Source: [Original title](https://example.com/article), Publication, article, 5 Nov 2026
Post idea: one line, only when there is one

---

### Next headline
...
```

- `## ` lines are sections (they become the filter buttons). Each item starts with `### `. A line holding only `---` ends an item.
- The `Source:` line gives the link, publication, type (article, blog post, podcast, video) and date.
- Links open on the iPad, or on the Mac with one tap. The Mac only opens links that are in today's file.

## A scheduled Claude task that writes the list

If you use Claude's scheduled tasks with the Microsoft 365 or Gmail connector, a prompt along these lines works. Adjust the sources to your own work.

> Prepare my focus list for today. Read, without changing anything: my mail from the last three days (replies waiting on me, promises I made, follow-ups with no answer for three working days), today's calendar, and yesterday's events titled "Focus:" (unfinished) or "Done:". Choose 3 tasks, at most 5, each about 45 minutes, most urgent first, with work that brings in paying clients before anything else. Every task must come from something you actually read. Write `_plan/<today>.json` in the format in docs/DAILY.md to the repository `<you/daily>`, commit only that file and push. Never send email and never change my calendar.
