# AI Collaboration Log

- **Initial Architecture Evaluation**
  Before writing any code, debated architecture with Claude i.e. LiteLLM vs manual abstraction, single LLM call vs LangGraph, GitHub REST vs Search API. Claude favoured manual abstraction, I pushed back because LiteLLM comes with retries and provider normalisation out of the box. As for LangGraph, a fixed topology means specialised agents add time and complexity without value so not in scope.

- **Reviewed every line of vibe-code before accepting it.** codebase planned and implemented with edits via Github Copilot from a detailed functional prompt, reviewed every line before accepting. Caught two issues: issue fetch pagination had no date awareness so added early exit when created_at exceeds since_days. Copilot used open_issues_count which returns count of issues and PRs so switched to Search API with is:issue filter instead.

- **Iterative prompt engineering and testing.**
  Iterated on the system prompt through live testing across many repos. Initial draft returned priorities without sufficient reasoning — made reasoning a required Pydantic field rather than a prompt instruction. Schema enforcement is more reliable. Also caught social engineering edge case on langchain-ai/langchain and added explicit system prompt guidance.
