# Token usage analysis: context compaction vs. full context

## Setup

Both runs solve the vendored SWE-bench task `django__django-15368` with the same model
(`aliyun/deepseek-v4-flash-0731`), the same `CodeAgent`, the same `submit-task` skill, and a
step limit of 200. They differ only in context handling:

- **Compaction run** (`artifacts/django__django-15368-trajectory.json`): `--compact-threshold-tokens 6000`,
  1 recent step kept verbatim, a 3,000-token budget per summary.
- **Full-context run** (`artifacts/django__django-15368-baseline-trajectory.json`): `--compact-threshold-tokens 0`,
  so nothing is ever summarized.

Token counts are the provider-reported `usage` values stored in each trajectory. Compaction calls are
recorded separately, in `compactions[].compaction_response.usage`, and are added to the compaction run's
total here. Compaction is triggered by the agent's calibrated estimate: the provider-reported `prompt_tokens` of the
previous request plus a characters / 4 estimate of what was appended since. The "Estimate before -> after"
column in the events table is the logged pure characters / 4 estimate of the whole prompt, so it can be below
6,000 for an event that did trigger (events 2 and 3), and it is unchanged when a compaction fails.
Each configuration was run once; the compaction run came first, then the baseline.

## Summary

| Metric                                           |      Full context |        Compaction |
| ------------------------------------------------ | ----------------: | ----------------: |
| ReAct steps (action requests)                    |                25 |                22 |
| Peak prompt size in one request                  |            17,198 |             7,200 |
| Mean prompt size per request                     |            11,425 |             4,326 |
| Largest message list sent in one request         |                56 |                22 |
| Action requests: prompt tokens                   |           285,631 |            95,167 |
| Action requests: completion tokens               |             7,851 |             4,668 |
| Compaction calls (ok / failed)                   |                 0 |         4 (2 / 2) |
| Compaction calls: prompt tokens                  |                 - |            19,634 |
| Compaction calls: completion tokens              |                 - |             9,673 |
| **All tokens, including compaction calls** | **293,482** | **129,142** |
| Patch graded by `evaluate_swebench.py`         |    RESOLVED: yes  |     RESOLVED: yes |

Relative to the full-context run, the compaction run used 66.7% fewer prompt tokens in action
requests, a 58.1% lower peak prompt, and 56.0% fewer tokens overall once its compaction
calls are counted (61.4% fewer if the 2 failed compaction calls are excluded).

## Prompt size per request

| Step | Full context | Compaction | Compaction event                                             |
| ---: | -----------: | ---------: | ------------------------------------------------------------ |
|    1 |        1,681 |      1,681 |                                                              |
|    2 |        2,152 |      2,023 |                                                              |
|    3 |        3,635 |      2,841 |                                                              |
|    4 |        5,150 |      3,137 |                                                              |
|    5 |        6,167 |      3,868 |                                                              |
|    6 |        8,013 |      4,790 |                                                              |
|    7 |        9,790 |      5,598 |                                                              |
|    8 |       10,649 |      4,141 | compaction 1 (ok) ran before this request                    |
|    9 |       10,872 |      4,370 |                                                              |
|   10 |       11,115 |      4,589 |                                                              |
|   11 |       11,210 |      5,165 |                                                              |
|   12 |       11,369 |      5,344 |                                                              |
|   13 |       11,583 |      5,572 |                                                              |
|   14 |       12,415 |      5,876 |                                                              |
|   15 |       13,514 |      6,194 | compaction 2 (failed, empty summary) ran before this request |
|   16 |       14,524 |      7,200 | compaction 3 (failed, empty summary) ran before this request |
|   17 |       14,685 |      2,734 | compaction 4 (ok) ran before this request                    |
|   18 |       14,900 |      3,256 |                                                              |
|   19 |       15,027 |      3,687 |                                                              |
|   20 |       15,217 |      3,930 |                                                              |
|   21 |       15,671 |      4,339 |                                                              |
|   22 |       15,816 |      4,832 |                                                              |
|   23 |       16,438 |          - |                                                              |
|   24 |       16,840 |          - |                                                              |
|   25 |       17,198 |          - |                                                              |

## Compaction events

| # | When          | Estimate before -> after | Actual prompt, previous -> next request | Call prompt / completion | Reasoning tokens | Finish reason | Result              |
| -: | ------------- | ------------------------ | --------------------------------------- | ------------------------ | ---------------: | ------------- | ------------------- |
| 1 | after step 7  | 6,735 -> 3,344           | 5,598 -> 4,141                          | 4,000 / 1,548            |              738 | stop          | summary used        |
| 2 | after step 14 | 5,616 -> 5,616           | 5,876 -> 6,194                          | 4,805 / 3,000            |            3,000 | length        | empty, context kept |
| 3 | after step 15 | 6,704 -> 6,704           | 6,194 -> 7,200                          | 5,084 / 3,000            |            3,000 | length        | empty, context kept |
| 4 | after step 16 | 6,917 -> 1,718           | 7,200 -> 2,734                          | 5,745 / 2,125            |            1,274 | stop          | summary used        |

## Observations

**1. Without compaction the prompt grows with every step; with compaction it stays bounded.**
The full-context prompt rises almost monotonically from 1,681 to 17,198 tokens (10.2x) because
every request resends the whole history, including each earlier file listing and test output. The message
list grows to 56 entries. In the compaction run the prompt follows a sawtooth: it climbs toward the 6,000
threshold, then drops sharply after a successful compaction (5,598 -> 4,141 after event 1,
7,200 -> 2,734 after event 4), and the message list never exceeds 22 entries. Since each request
pays for the entire history, the total input cost is roughly the sum of a growing series, which is why the
action-request prompt total differs by 3.0x even though the step counts are close.

**2. Compaction is not free, and its calls must be counted.**
The two successful compactions cost 13,418 tokens together. Each one re-reads the old prefix as its
prompt and generates 1.5-2.1K completion tokens, about half of them reasoning; the summary text itself is
only 2,862 and 3,076 characters (roughly 0.7-0.8K tokens). That is small compared with the 190,464 prompt
tokens saved in action requests, but it is a real cost that does not appear in the usual per-step usage.
Ignoring it would overstate the saving (66.7% versus the 56.0% above).

**3. A reasoning model can spend the entire summary budget on thinking.**
Events 2 and 3 produced no summary. Both hit the 3,000-token completion cap with all 3,000 tokens spent on
reasoning (`finish_reason=length`, empty content). The agent correctly kept the full context instead of
replacing it with nothing, but the two calls still cost 15,889 tokens, and because the context stayed above
the threshold the agent retried on the very next step. The only requests above 6,000 tokens in this run are
steps 15, 16, which are exactly the steps right after these failures. A larger summary budget, or a retry
back-off, would avoid this. The fixed `reasoning_effort="medium"` in the provided compaction call could not be
changed. This was not re-run with a larger budget.

**4. Task quality was not visibly hurt, but one sample cannot prove that.**
The compaction run's patch is graded RESOLVED: yes. The first summary kept the objective, the root cause
(`F` does not subclass `Expression`), the culprit line in `django/db/models/query.py`, the existing precedent for
duck typing, the test file to extend, and the commands already run. After that compaction the agent re-viewed
lines 668-678 of `query.py` once (it had read them at step 2) before editing, and otherwise did not repeat
earlier exploration. The two runs took different routes and produced different fixes (`isinstance(attr, (Expression, F))` versus a `hasattr(attr, 'resolve_expression')` check), and the step counts (22 versus 25) are within
the run-to-run noise to expect from sampling, so no claim is made about step efficiency.

**5. Raw token savings do not by themselves prove a cost saving, because of prompt caching.**
The provider reported 70,656 of 95,167 action-request prompt tokens as cached in the compaction run
(74.2%) versus 258,048 of 285,631 (90.3%) in the full-context run. This is consistent with, but does not
prove, the expected mechanism: an append-only history keeps a stable prefix that the provider can reuse,
while compaction rewrites the prefix, so part of the cache is lost after each compaction. Run order may also
contribute (the compaction run went first, and the baseline's first request already reported 1,024 cached
tokens). Counting only non-cached prompt tokens, the compaction run is not cheaper: 24,511 in action requests
plus 11,442 in compaction calls is 35,953, versus 27,583 for full context. It also generated more completion
tokens (4,668 + 9,673 = 14,341 versus 7,851). If the provider bills cached input at a steep discount,
compaction could cost as much as or more than full context for a task this short, even though it sends
56.0% fewer raw tokens. Pricing was not checked, and cache hits depend on provider state, so these figures
are indicative only.

## Trade-offs

|                     | Full context                                                         | Compaction                                                                                              |
| ------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Per-request context | Grows without bound; this 25-step task already reached 17,198 tokens | Bounded near the threshold (7,200 peak here)                                                            |
| Total tokens        | Highest, because each step resends everything                        | Lower in raw tokens after paying for the summaries; billed cost also depends on caching (observation 5) |
| Information         | Lossless; the model can reread exact outputs                         | Lossy; exact error text or line numbers can be dropped and must be re-fetched                           |
| Extra calls         | None                                                                 | One summarizer call per compaction; latency and tokens, and it can fail                                 |
| Caching             | Prefix stays stable and cacheable                                    | Rewriting the prefix invalidates part of the cache                                                      |
| Risk for long tasks | May exceed the model's context window                                | Keeps long tasks inside the window, if summaries are good                                               |
| Tuning              | Nothing to tune                                                      | Threshold, kept-recent steps, and summary budget all matter                                             |

For a short task like this one, the full-context run fits comfortably in the context window, and the
threshold of 6,000 is deliberately low so that compaction triggers at all. The benefit grows with
task length, since full-context cost grows with the whole history at every step while compaction caps it,
and compaction becomes necessary once the history no longer fits in the context window.

## Limitations

- One run per configuration; sampling makes the trajectories and fixes differ, so the numbers are a
  demonstration of the trend, not a statistical comparison.
- Token counts come from the provider's `usage`; the compaction trigger and the logged before/after estimates
  use a rougher characters / 4 estimate.
- Cached-token counts depend on provider cache state and on the order the runs were made.
