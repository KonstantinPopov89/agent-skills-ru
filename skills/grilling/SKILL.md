---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

## Mode

The default mode is a full interview. Interview the user relentlessly until you reach
shared understanding; map a **design tree** where each decision branches into its
dependent decisions.

When `grill-me` passes `audit-clarification` from `pizdej-check`, restrict the interview
to the supplied audit gap and acceptance criterion. The supplied facts and settled
decisions remain settled. Ask only questions whose answers can change the verdict or
unlock relevant evidence; inspect available facts yourself. Usually 1–3 independent
questions suffice, with dependent follow-ups only as necessary for that same gap.
Return answers, revised criteria/assumptions, evidence locators and remaining unknowns
to the calling audit as soon as the gap is resolved or cannot be resolved. Do not
require full-tree completion or a separate confirmation to resume the authorized
audit. A factual user report remains a source report until verified. A changed
criterion creates a revised scope; it does not retroactively erase an earlier defect.
Do not switch to a full interview without a separate user request.

The rounds and question-tool lifecycle below apply to both modes, within their scope.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the frontier through the host's native question widget. Split a large frontier into small batches within the current tool's limits, keeping dependent questions for later rounds.

## Native question widget

Use only a tool actually available and permitted in the current session, with its current schema and mode restrictions. The main agent owns the interview; sub-agents return facts, not questions to the user.

- **Codex:** prefer `request_user_input_async` when available and permitted. Put each complete, self-contained question in `title`; use `options` for meaningful choices, or omit them for free-text questions. Otherwise use `request_user_input` only when allowed by the current mode (commonly Plan mode); supply its required question IDs, headers, choices and descriptions. Do not switch modes merely to unlock a widget. Tool names may have a namespace prefix such as `functions.`.
- **Cursor:** use a question tool only if actually exposed and permitted, following its current schema. Do not assume Codex or Claude tool names exist. Otherwise use the ordinary-chat fallback below.
- **Claude Code:** use `AskUserQuestion` when available. Its usual limits are 1–4 questions per call, 2–4 choices per question and a header of at most 12 characters; the actual tool schema takes precedence. Use `multiSelect: true` only for compatible choices that can be selected together. Users can write a free answer through the widget's built-in Other field; do not add a duplicate Other option.
- Keep a batch small (normally 1–3 independent questions). Make choices distinct and explain the tradeoffs briefly. Put a justified recommendation first and label it as recommended, following the host's schema; do not invent a preference or meaningless choices merely to fill the widget. Keep the question and recommendation inside the widget rather than repeating a questionnaire in chat.
- After a synchronous tool call, use the user's returned answers. After an asynchronous call, `accepted: true` only confirms presentation, not an answer. Keep every question pending until its actual user reply arrives. A preselected option, empty result, silence or elapsed time is not an answer or agreement. Continue independent fact-finding while waiting; do not settle pending decisions or advance their dependent branches.
- **Codex async lifecycle:** while a question is pending, keep the current turn active. Do not send a `final` response, even an empty one or "waiting for your answer": ending the turn can dismiss the widget. If no independent work remains, call the available interruptible wait tool (for example `clock.sleep` with `duration_ms: 50000`). After each wait, process new user input; if the question remains pending, wait again. A wait finishing is not a deadline for the user. Brief progress messages belong in `commentary`; they must not end the turn or reissue the widget.
- A partial reply closes only the answered questions. A status request does not cancel the interview. If the user explicitly cancels, stops, or replaces the task, honor that instruction; never treat cancellation as a selected answer. If the user reports a missing widget, reissue only the unanswered questions once and keep the turn active. If the widget still fails, or no permitted interruptible wait is available, preserve the unresolved decision and use the ordinary-chat fallback below; never invent an answer.
- If no suitable widget is available or permitted, a tool call fails, or its schema cannot express the needed question, briefly explain the limitation and ask one concise question in ordinary chat, with a recommendation when justified. Do not imitate a widget with JSON or a numbered questionnaire, and do not repeatedly call an unavailable tool. Once explicitly switching to this fallback, send the question as the ordinary chat response and wait for the next user turn; the async keep-turn-active rule no longer applies, but the unanswered decision stays unresolved.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent to find it; don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

In a **full interview**, the session is done when the frontier is empty: every branch
of the design tree visited, nothing left silently assumed. Do not act on it until the
user confirms you have reached a shared understanding.

In **`audit-clarification`**, finish only the supplied gap and return to the calling
audit; do not add a full-interview approval gate. An explicit unknown, refusal or
cancellation of the clarification leaves the gap unresolved; continue independent
audit checks without guessing. Cancellation of the entire task stops the audit too.
Silence is not a reply: retain the pending-question lifecycle above.
