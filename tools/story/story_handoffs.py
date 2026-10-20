"""Validated content passed between story roles, separate from routing metadata."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from components.canvas.game_runtime import GameAction


class ProposedFactChange(BaseModel):
    """A responder claim for reconciliation, never an authorized state mutation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=100)
    info: str = Field(
        min_length=1,
        max_length=500,
        description="Proposed complete sticky value; encode field-backed notes as JSON.",
    )
    evidence: str = Field(
        min_length=1,
        max_length=1000,
        description="Resolved outcome, actual dice result, or game receipt supporting the change.",
    )


class PlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class NarrationRequest(BaseModel):
    """A recorder request for the responder to narrate a significant game development."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_action: str = Field(min_length=1, max_length=2000)
    nudge: str = Field(default="", max_length=1000)


class CommittedGameReceipt(BaseModel):
    """A validated Narratron game action and the state snapshot produced when it committed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    parent_event_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    source_revision: int = Field(ge=0)
    action: GameAction
    receipt: dict[str, JsonValue]


class GameControlDecision(BaseModel):
    """The game-control outcome selected for one committed player game event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    narration_request: NarrationRequest | None = None


class GameControlOutcome(BaseModel):
    """Durable result of recording and controlling a player game action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    player_recorded_revision: int = Field(ge=0)
    narratron_receipts: tuple[CommittedGameReceipt, ...] = ()
    final_recorded_revision: int = Field(ge=0)
    narration_request: NarrationRequest | None = None
    narration_triggered: bool = False
