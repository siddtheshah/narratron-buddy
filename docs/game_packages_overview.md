# Theater game packages

Game packages add playable games to a Narratron adventure. A package defines
the rules, tracks progress, and presents a board or controls on the canvas.
Each adventure can bring its own game, such as checkers, a trading encounter,
or a turn-based puzzle.

## The basic concepts

- **State** is the current situation: whose turn it is, where pieces are, or what each side owns.
- **Actions** are the choices that change that situation, such as moving a piece or proposing a trade. The game's rules decide which actions are allowed.
- **View** is what you see and interact with: the board, buttons, and information about the game.

## What kinds of games work?

Narratron supports **turn-based transactional** games: play advances through
distinct decisions and settled outcomes. Either side can take time to respond
without changing the result.

A game needs clear limits on what each side can do before the other gets a say.
Checkers uses turns; a trade requires the recipient to accept the offered terms
before goods change hands. Selecting a piece or exploring an offer can happen
before committing a decision.

Board games, turn-based challenges, and negotiated exchanges fit this model.
Games that depend on quick reactions, continuous movement, or real-time physics
do not.

## Playing together

When an adventure includes a game, open it with the game button on the canvas.
The active orator can play. With Collaboration Mode enabled, viewers who have
contributor permission can also use actions the game allows them to take.
Other viewers can follow the shared game.

Some games let you preview choices before submitting them. Those choices remain
private until you commit them; everyone else sees the committed result.
The game checks each submitted action against its rules before updating play.

Narratron can take its own actions when the adventure is configured for it.
A game can also be used without a live story agent.

## Creating a game

Start with the experience you want: what each side can do, when control passes,
and how the game ends. Then define the rules and how players will see their
choices.

For authoring details and examples, see the
[full game package guide on Narratron's GitHub](https://github.com/siddtheshah/narratron-buddy/blob/main/docs/game_packages.md).
