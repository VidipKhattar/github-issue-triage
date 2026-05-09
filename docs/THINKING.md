### How I thought about this

This tool is useful if it provides **actionable, personalised signal, not more
regurgitated data**. A maintainer scrolling GitHub for 30 seconds sees titles,
labels, and timestamps. What they cannot see is which issues share a root cause,
which are genuinely critical versus noise, or what the comment
sentiment reveals about severity. The tool's job is to extract that signal immediately
on what actually warrants the users attention this week: priority list, reasoning,
suggested actions.

The target user is a maintainer who may not be full-time, likely manages multiple
repos, and is triaging on a Monday morning weekly with limited attention.
The tool needed to be cheap, fast, and personalised to tell them what matters, not everything that is there.

**Key decisions**

- **Single LLM call, not an agent loop.** The pipeline is fixed: fetch -> preprocess
  -> one LLM call -> render. An agent loop can only be used effectively when the next action
  depends on what was discovered in the fetching which is not true here. One call, quickly, cheaply
  less complex and appropriate relative to a ReAct Agent Loop.

- **Aggressive Preprocessing before the LLM.** Deterministic HTML stripping, truncation, signal extraction,  
  staleness, reaction counts, reporter type, milestone and comment counts all happen in
  Python before the model sees anything. This also cuts token count heavily and produces
  cleaner LLM outputs through a more focused input schema.

- **LiteLLM over manual abstraction.** "Provider-swappability" is a one-line `.env`
  change. LiteLLM is a proven abstraction solves our requirement for retries, model normalisation, and cost tracking.

- **Pydantic schema enforcement for auditability.** `reasoning` and `suggested_action`
  are required fields, the model cannot respond without justifying every decision.
  LLM-generated columns in the CLI are responsibly marked `*` so maintainers always know what is
  true GitHub data versus AI generated.
  The tool facilitates a triage suggested action items but it doesn't carry out those actions.

- **`--focus` flag as a lightweight personalisation layer.** Injects a maintainer directive
  straight into the system prompt for specific analysis.

**What I deliberately did not build**

- Web UI — a CLI that can provide JSON format for any future integration
- Conversational follow-up agent — natural v2/evolution but explicitly out of scope and costly
- Caching — worth adding if this ran on a schedule but unnecessary for on-demand CLI

**One thing I'd do differently with more time**

- **Smarter issue sampling.** The current issue fetch is newest-first up to `--max-issues`. For
  large repos, like microsoft/vscode, this gives the LLM a skewed view of the fetched issues.
  With more time I'd implement a more stratified way to fetch issues based on different types engagement and
  staleness, remainder from recent. it will be the same token use but more representative signal with better prioritisation.
