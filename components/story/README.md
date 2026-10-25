# Story processing

`StoryEngine` binds the recorder, responder, planner, journals, references, and
ADK session to a theater. `tools/story/story_tool.py` is the live-agent adapter.
Both spoken actions (including auto-begin) and committed game receipts enter
`canvas_state_manager.story.cycle` before any story role runs.

`story_planning.cycle_length` controls the collection window in seconds (default
15; zero means immediate serialized execution). Older configurations using
`cooldown_duration` or `action_cooldown_base_seconds` remain supported when
`cycle_length` is absent or null. The first pending input starts the window. Inputs
arriving during execution are retained for the next window; they never replace
one another. `wait_for_pending_events()` drains windows for deterministic tests
and harnesses.

At dispatch, the turn journal atomically freezes cycle membership. The sole
cycle worker records each game receipt, resolves all spoken actions and game
narrative briefs in one response, records that response, delivers it once, and
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
