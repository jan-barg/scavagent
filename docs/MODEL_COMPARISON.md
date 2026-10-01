# Model comparison

Lou runs on Claude Sonnet 5.5 through Anthropic's API. If Claude can't answer, Gemini 3.5 Flash-Lite takes the turn automatically. Sonnet passed every check and wrote stories nearly as good as Opus's at about half the cost and time.

On September 29, 2026, each model ran the same app on the three grader queries plus two themed walks (25 minutes of music history from West 72nd Street, and two hours to FiDi for a fan of The Strokes). Stories were judged against the principles in [STORY_DESIGN.md](STORY_DESIGN.md).

| Model | Checks passed | Planning turn | Cost, 10 test turns | Stories |
|---|---|---|---|---|
| Claude Opus 5.5 | 18 of 18 | 76 to 124 s | $1.88 | The best |
| Claude Sonnet 5.5 | 18 of 18 | 39 to 77 s | $0.84 | Nearly as good |
| Gemini 3.5 Flash | 15 of 18 | 52 to 104 s | $1.08 | Best outside Claude; rate-limited |
| Kimi K2 Thinking | 17 of 18 | 66 to 97 s | $0.34 | Cast real people as characters |
| Gemini 3.5 Flash-Lite | 18 of 18 | 23 to 47 s | $0.11 | Thin: no briefing, stops earn nothing |
| Qwen3 235B | 14 of 18 | 86 to 122 s | $0.12 | Invented history |
| gpt-oss-120b | 6 of 14 | 2 to 4 min | n/a | Most calls refused (rate limit) |

After the story redesign, Opus and Sonnet passed all 26 checks and Flash-Lite 24. Claude on Vertex AI had no quota for this project, so Claude goes through Anthropic's API.
