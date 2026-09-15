# Slack channel audit — 2026-09-15

Status: Audit and candidate verification completed; deployment not performed. Owning issue: #186.

Read all 64 channel-level messages and all 253 replies in 49 threads available at the initial
snapshot of Corca `#trace-agent-test` (2026-09-07 through 2026-09-15). Every thread response
reported no remaining pages. A final reread found four additional roots/replies (three audit probes and one colleague test), also read completely: 68 roots, 53 threads and 257 replies in total; no older thread reply counts changed. Links require workspace
access; this record deliberately omits raw private conversation text and participant identities.

| Finding | Current evidence and disposition |
| --- | --- |
| F1 Request authority | Reproduced: a yielded/restarted Slack drive omitted requested-work authorization; new-work prefix stripping also broke exact source matching. Repair and regression tests in this PR. Historical approval hashes remain compatibility commands, not the default flow. |
| F2 Task transitions | Reproduced: blocked completion swallowed subsequent questions; explicit new work during uncertainty stayed response-only. Preserve pending receipts and retained original delivery ownership while admitting independent work. |
| F3 Capability/model answers | Reproduced: configured model identity absent from reasoning input; response-only inventory confused with global service capability. Project scoped availability and configured identity without granting tools to uncertain dialogue. |
| F4 Progress/latency | Reproduced: visible elapsed time resets between drive slices. Persist its initial start; show skill lookup, generation and result-check stages. Historical outage duration is unproven without deployment logs. |
| F5 Evidence identifiers | Reproduced in installed real-model rehearsal: mistyped 64-character evidence ID blocked a finished draft. Actor and assessor confused host evidence handles with output/receipt digests. Constrain both provider selections to exact supplied IDs; retain host proof checks. |
| F6 Completion context | Reproduced in actual-model assessment: actor-visible user facts and capability limits absent from assessor, leading to rejection and repeated work. Give assessor the same scoped reference facts, never effect authority. Actual six-image generation also reproduced an unsolicited visual-review gate; ordinary generation now delivers verified artifacts, while explicit review still requires review evidence. |
| F7 Dialogue/memory/scope | Actual eight-turn rehearsal recalled corrected facts in a new thread, excluded another fictional project, and retained Korean constraints. It exposed rejection of an already-stored memory readback; the repaired actual assessor accepts verified existing memory without claiming a new write. Scoped storage/skill-authoring baseline fixtures pass. |
| F8 Research | Historical sources included search snippets and unverified post links. Current installed real search and real model returned two actual search-result URLs, Traditional Chinese, and explicit snippet-only/targeting uncertainty. Historical source-quality error was not reproduced. Direct Threads page access remains unverified. |
| F9 Unsupported integrations | At the probe snapshot, arbitrary recurring analytics/Threads posting was unavailable in the configured test scope. During this audit main PR #185 added Threads/scheduling owners; this branch preserves them. Availability now depends on their actual configuration and account grants; the earlier unconfigured probe does not disprove those features. Frozen trace-post motifs are a documented workflow limit; custom media can use a different suitable capability. |
| F10 Repeated observation | Final live capability probe ended with `tool_idempotency_conflict`. Reproduced locally when the model refreshed identical read-only input within one Run. Main PR #189 independently landed the same repair during this audit. The integrated branch uses that canonical implementation and adds a repeated-read regression; persisted recovery and external/tenant-wide deduplication remain unchanged. |
| N Non-task | Join events, unmentioned chatter, human-to-human messages: read, excluded from agent defect counts. |

## Complete initial inventory

Each row includes the root and every reply; finding IDs refer to the dispositions above.

| Root | Replies read | Findings |
| --- | ---: | --- |
| [1789371849.903539](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789371849903539) | 31 | F1 F2 F3 |
| [1789369013.511429](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789369013511429) | 5 | F1 F4 |
| [1789361317.410259](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789361317410259) | 3 | F7 |
| [1789360418.800739](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789360418800739) | 24 | F1 F2 F4 |
| [1789354405.584779](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789354405584779) | 9 | F1 F3 |
| [1789354248.527859](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789354248527859) | 1 | F1 |
| [1789353828.606469](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789353828606469) | 3 | F1 |
| [1789351835.127459](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789351835127459) | 1 | F1 F7 |
| [1789111318.975819](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789111318975819) | 1 | F1 F7 |
| [1789108399.093559](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789108399093559) | 29 | F1 F3 F7 |
| [1789100652.699879](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789100652699879) | 1 | F7 |
| [1789097347.379769](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789097347379769) | 1 | F6 |
| [1789097254.909479](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789097254909479) | 0 | N |
| [1789096358.823799](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789096358823799) | 17 | F1 F3 |
| [1789021873.319009](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789021873319009) | 5 | F9 |
| [1789020095.666049](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789020095666049) | 5 | F3 F8 |
| [1789018797.721329](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789018797721329) | 1 | F7 |
| [1789018605.401239](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789018605401239) | 9 | F1 F7 |
| [1789018170.489329](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789018170489329) | 5 | F7 |
| [1789018124.947639](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789018124947639) | 0 | F7 |
| [1789017610.090789](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789017610090789) | 0 | N |
| [1789017580.267819](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789017580267819) | 1 | F7 |
| [1789017378.099399](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789017378099399) | 5 | F7 |
| [1789017346.802539](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789017346802539) | 0 | F7 |
| [1789008163.123059](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789008163123059) | 1 | F4 F7 |
| [1789007217.403599](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789007217403599) | 1 | F7 |
| [1789006908.081629](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789006908081629) | 5 | F8 |
| [1789006772.584599](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789006772584599) | 1 | F7 |
| [1789006742.634769](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789006742634769) | 1 | F7 |
| [1789006697.065879](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789006697065879) | 5 | F7 |
| [1789006657.703879](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789006657703879) | 3 | F7 |
| [1788961722.987659](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788961722987659) | 1 | F7 |
| [1788961678.322959](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788961678322959) | 3 | F7 |
| [1788939760.362099](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788939760362099) | 5 | F3 |
| [1788933397.778589](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788933397778589) | 7 | F8 |
| [1788924872.885469](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788924872885469) | 1 | F7 |
| [1788924820.687749](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788924820687749) | 1 | F7 |
| [1788924639.929499](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788924639929499) | 11 | F7 |
| [1788923249.695499](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788923249695499) | 1 | F7 |
| [1788923185.944059](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788923185944059) | 3 | F7 |
| [1788923141.141259](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788923141141259) | 3 | F7 |
| [1788922916.380329](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788922916380329) | 1 | F7 |
| [1788922877.071169](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788922877071169) | 3 | F7 |
| [1788848854.918809](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848854918809) | 1 | F4 F7 |
| [1788848854.540279](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848854540279) | 1 | F4 F7 |
| [1788848854.129549](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848854129549) | 1 | F4 F7 |
| [1788848843.064949](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848843064949) | 0 | N |
| [1788848842.705239](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848842705239) | 0 | N |
| [1788848842.347029](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848842347029) | 0 | N |
| [1788848783.901819](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788848783901819) | 3 | F2 F7 |
| [1788847310.844999](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788847310844999) | 15 | F2 F3 |
| [1788845978.146279](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788845978146279) | 1 | F3 |
| [1788845915.530639](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788845915530639) | 9 | F3 |
| [1788845880.618819](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788845880618819) | 1 | F6 |
| [1788841558.155789](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788841558155789) | 0 | F4 |
| [1788841502.068019](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788841502068019) | 0 | F4 |
| [1788841450.431839](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788841450431839) | 0 | N |
| [1788841450.318459](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788841450318459) | 0 | N |
| [1788836726.876689](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788836726876689) | 2 | F1 |
| [1788836089.822879](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788836089822879) | 5 | F8 |
| [1788829784.375519](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788829784375519) | 0 | N |
| [1788760770.321939](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788760770321939) | 0 | N |
| [1788760747.892649](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788760747892649) | 0 | N |
| [1788760745.011709](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1788760745011709) | 0 | N |

## Additional roots read at closeout

| Root | Replies read | Findings |
| --- | ---: | --- |
| [1789431930.869719](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789431930869719) | 1 | F3 F4 F10 |
| [1789432105.711469](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789432105711469) | 1 | F3 F7 |
| [1789432254.897929](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789432254897929) | 1 | F1 F9 |
| [1789440805.257089](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789440805257089) | 1 | F6 F9 |

The additional colleague cloud-motif request ended with `completion_unsatisfied`; the frozen
trace-post workflow does not support that motif. This is not counted as successful generation.

## Verification

Main integration: `60c9d20` (PRs #185 and #189). A fresh integrated non-editable wheel passed **177 focused tests in 39.06 seconds** across the affected service, Slack, notification and provider boundaries. The actual-model probes below were performed before that integration in explicitly bounded tool scopes. Do not interpret their unavailable-capability answers as the latest global catalog.

- Installed main baseline: 73 focused tests passed; failing-first regressions exposed gaps in those fixtures.
- Non-editable candidate wheel, outside checkout: 146 focused tests passed in 32.95 seconds. After the final observation-identity change, a fresh final wheel passed 61 directly affected tests in 10.17 seconds; the earlier 146-test result is not counted again. Changed production owners: basedpyright reported zero errors/warnings.
- Actual Codex six-turn rehearsal: five completed answers and one honest missing-integration question. Reviewer inspected each answer: catalog, one Korean draft, same draft without explanation, English translation, 7.5% aggregate conversion, no invented scheduled job. Observed turn durations ranged 26–173 seconds; no latency SLA is claimed.
- Actual trace-post provider produced six PNGs (KR/JP/TW wallpaper and scene). The first assessment withheld all files for missing unsolicited visual review. The repaired installed assessor accepted the same canonical artifacts; local Slack consumer performed six complete file-upload sequences (18 API-stage calls) with original-PNG download guidance. No image regeneration/replay occurred during recovery. One generated Japanese scene was visually inspected; no full visual certification is claimed.
- Actual memory rehearsal: corrected fictional budget 41만원 recalled in a new thread, unrelated fictional-project budget correctly unknown. Reassessment of the authenticated existing-memory readback passed without a redundant write. The original intermediate verification failures remain recorded as defects, not passing turns.
- Actual public research: two search-result post URLs and their Traditional Chinese snippets were reported with explicit source limits; no fabricated full-page verification.
- Live production probes: [capability lookup](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789431930869719), [English/model answer](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789432105711469), [image request](https://corcaai.slack.com/archives/C0BV9T0AT1B/p1789432254897929). Capability lookup ended with `tool_idempotency_conflict`; English passed; model identity was unavailable; image request reported `awaiting_approval`. That requested cloud motif is outside the frozen trace-post motif list, so it does not prove generation should have succeeded.
- Candidate Slack sends used a local capture receiver. Actual workspace upload/download, production activation, general Threads access and arbitrary account scheduling are not proven by these checks. The new PR is not merged.

Private raw provider/Slack receipts are retained locally; this inventory is the sanitized review record.
