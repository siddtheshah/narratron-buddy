# Story processing

`StoryEngine` binds the recorder, responder, planner, journals, references, and
ADK session to a theater. `tools/story/story_tool.py` is the live-agent adapter.
`StoryInputGatheringCycle` in `input_gathering_cycle.py` gathers spoken actions
(including auto-begin) and committed game receipts through
`canvas_state_manager.story.input_cycle` before any story role runs (`story.cycle`
remains an alias). It appends waiting input so every accepted action is retained.

`story_planning.input_cycle_length` controls the minimum interval between batch
starts in seconds (default 15 when absent or null; zero means immediate serialized
execution). The first input starts immediately, like `with_cycle_cooldown`.
Subsequent inputs are retained in one batch until both the active batch and its
interval finish; they never replace one another or extend the interval. If the
active batch takes longer than the interval, waiting input starts as soon as it
finishes. `wait_for_pending_events()` drains intervals for deterministic tests
and harnesses, including the latest pending output.

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
narrative briefs in one response, records that response, stages it once, and
runs one planning pass. Game-only cycles honor the recorder's explicit decision
to skip narration. Planner checkpoints commit progress for every member in one
transaction. Planning and foreground roles share the same flight.

Failures retain committed stage progress and the original cycle membership.
The next input, explicit retry, or engine restart resumes the missing stages.
A failed factual stage blocks dependent story processing while leaving intake
available; a failed planning stage can be retried without repeating recording
or narration. There is no automatic endless retry loop.
Partially processed saves from the earlier scheduler resume as separate cycles
so their existing responses and receipts cannot be merged or discarded. Queued
actions enter recent-story context only when their cycle starts.

The cycle lock covers queue bookkeeping only. Engine execution, game access,
notifications, and future completion happen outside that lock. No engine state
lock is held over model work. Game commits do not wait for story workers, and
their story callbacks execute outside the game lock. The engine drains the game
outbox in revision order so concurrent post-commit callbacks preserve causality.
Result callbacks are isolated from stage completion. Every worker exit releases
the flight, including exceptions before role execution. Shutdown drains accepted
input before closing the executor, session, and journals.

The earlier design and role contracts are recorded in
[`adr-001-recorder-planner-responder.md`](../../tools/story/adr/adr-001-recorder-planner-responder.md).
Its foreground wait hook and tool-owned scheduling are superseded by this cycle.
