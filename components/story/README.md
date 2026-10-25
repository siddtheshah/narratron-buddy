# Story processing

`StoryEngine` binds the recorder, responder, planner, journals, references, and
ADK session to a theater. `tools/story/story_tool.py` is the live-agent adapter.
`StoryInputGatheringCycle` in `input_gathering_cycle.py` gathers spoken actions
(including auto-begin) and committed game receipts through
`canvas_state_manager.story.input_cycle` before foreground story roles run (`story.cycle`
remains an alias). It appends waiting input so every accepted action is retained.

`story_planning.input_cycle_length` controls the minimum interval between batch
starts in seconds (default 15 when absent or null; zero means immediate serialized
execution). The first input starts immediately, like `with_cycle_cooldown`.
Subsequent inputs are retained in one batch until both the active batch and its
interval finish; they never replace one another or extend the interval. If the
active batch takes longer than the interval, waiting input starts as soon as it
finishes. `wait_for_pending_events()` drains intervals for deterministic tests
and harnesses, including the latest pending output and background planning.

`StoryOutputDisplayCycle` in `output_display_cycle.py` independently controls
canvas narration and dialogue through `story.output_cycle`. It replaces waiting
output so only the newest scene becomes the next cycle's content.
`story_planning.output_cycle_length` sets the minimum display time in seconds
(default 15; zero presents every ready output immediately). The first output
appears immediately. During its dwell, newer outputs replace the single waiting
slot without extending the current dwell. After the dwell expires, the latest
waiting output is shown; if nothing is waiting, the current scene stays visible.
The display interval starts after scene preparation commits to canvas state, so
preparation time cannot shorten the current content's display time.
Input processing, recording, and planning continue during display dwell.

Each output carries its originating input batch's first durable journal position.
Displayed positions only increase: outputs 1, 3 are allowed when 3 supersedes
waiting output 2, but 2 can never appear after 3, even on late delivery or retry.
Narration and dialogue advance together. Beautification and speech synthesis
start only when a scene is promoted, so waiting or superseded scenes cannot
cancel the current scene's speech. The waiting slot and ordering watermark are
saved with canvas state and restored when an engine starts. Engine shutdown
cancels output timers while retaining that checkpoint.

At dispatch, the turn journal atomically freezes cycle membership. The sole
cycle worker records each game receipt, resolves all spoken actions and game
narrative briefs in one response, records that response, and stages it once.
Game-only cycles honor the recorder's explicit decision to skip narration.

`BackgroundPlanning` independently consumes a durable stream of accepted player
intentions, committed game actions, generated responses, and recorded outcomes.
Input gathering and output display intervals never gate planning, and planner
work or failure never holds the foreground cycle. Updates arriving during a
planning invocation enter its next model request, preserving tool exchanges and
role isolation in the shared ADK session. Inputs remain intentions until an
output or recorded outcome establishes what happened. Responses enter planning
even while they wait for display or are superseded on the canvas.

`story_planning.deep_planning.token_budget` bounds generated and thinking tokens
across model/tool continuations since the latest assimilated update (default
16384). New content replenishes this allowance. Without new content, the planner
finishes naturally or stops at the budget; there are no idle refinement passes.
Per-request output/thinking limits, lore-call limits, and the invocation timeout
also remain in place. Planner note publication and update acknowledgements commit
atomically. Foreground roles can use older planning guidance: planner-only
changes never invalidate recorder work. Publication preserves the newest planner
notes while still enforcing sticky ownership, schema validation, and freshness
of factual notes and shared topics being written. The planner reconciles against
fresh state before publishing stale work.

Failures retain committed stage progress and the original cycle membership.
The next input, explicit retry, or engine restart resumes the missing stages.
A failed factual stage blocks dependent story processing while leaving intake
available. Pending planner updates recover independently on new input, explicit
retry, or restart, without repeating recording or narration. There is no automatic
endless retry loop.
Partially processed saves from the earlier scheduler resume as separate cycles
so their existing responses and receipts cannot be merged or discarded. Queued
actions enter the responder's recent-story context only when their cycle starts;
the planner receives them immediately.

The cycle lock covers queue bookkeeping only. Engine execution, game access,
notifications, and future completion happen outside that lock. No engine state
lock is held over model work. Game commits do not wait for story workers, and
their story callbacks execute outside the game lock. The engine drains the game
outbox in revision order so concurrent post-commit callbacks preserve causality.
Result callbacks are isolated from stage completion. Every worker exit releases
the flight, including exceptions before role execution. Shutdown drains accepted
input and joins the planner before closing the session and journals.

The earlier design and role contracts are recorded in
[`adr-001-recorder-planner-responder.md`](../../tools/story/adr/adr-001-recorder-planner-responder.md).
Its foreground wait hook and tool-owned scheduling are superseded by this cycle.
