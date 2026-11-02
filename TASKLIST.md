# Narratron Task List

## Image Tools

- [ ] Use Decision for safety reduction checks.

## Game Engine Upgrades

- [x] Input and canvas API
- [x] Allow direct HTML for game panel.
- [ ] Game storage repository and attachability in theater-editor.

## Remote

- [ ] Show Narratron current thought in the remote watch tab as a separate sub-tab.
- [ ] Keep unmuted when moving been top level tabs if a remote watcher unmuted.
- [ ] Game integration with mobile? Separate panel html required.

## Adventure Mode

- [x] Enable narration, not just character voicing as an option.
- [ ] Investigate image gen release on story turn completion.

## Theater Builder

- [ ] Move and drag files around in left hand pane. 

### Evaluation and decision

- [ ] Add a reproducible benchmark harness for both tracks, including cold load, steady-state turns, output parity, resource exhaustion, and payload sizes.
- [ ] Compare native CEL alone, client-side rendering alone, and their combination before choosing a migration. Require measurable end-to-end improvement and preservation of the restricted authoring/security model.

## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Quality Testing
- [ ] Test Deepseek V4.1-flash as an alternative to gemini.
   - Can use LiteLLM as a wrapper to get compatibility with ADK agents in story modules.


## Refactors
- [ ] Eliminate traces of 'named element' terminology.
- [ ] Refactor canvas.html to be more modular.
- [ ] Reduce direct image file os path checking. Make better interfaces to access image content within a theater.

