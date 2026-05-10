MAINTAINER FOCUS (applies to top_priorities only): {focus}

For the top_priorities section ONLY:

- Include only issues matching this focus.
- Return up to 5 matching issues. If fewer than 5 match, return fewer.
- Do NOT substitute non-matching issues, even if they contain severity
  keywords like "security", "crash", "regression", or "data loss".
- If genuinely zero issues match the focus, return an empty list and
  explain in the summary which non-focus issues would otherwise have
  been priorities.

All other sections (stale_issues, quick_wins, duplicate_groups, clusters,
issue_categories, summary) are NOT filtered by focus. Return them in full
based on the complete input.

The user has explicitly chosen what they want in priorities. Respect that
over the default severity weighting, but match generously — an issue
about documentation gaps in a security feature still counts as docs.
