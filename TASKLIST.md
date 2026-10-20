# Narratron Task List

## Game Engine Upgrades
- [x] Raise `game.json` package size limit (`MAX_GAME_BYTES`) from 64 KiB to 128 KiB (131,072 bytes).
- [x] Support loading, validating, packaging, and rendering SVG graphics placed in an adventure's `game/` folder.
- [x] Allow game actions (`transitions` / `action` in `game.json`) to be annotated with `'contributor'` permission.
- [x] Enable viewers with contributor permissions to take `'contributor'`-annotated game actions on the canvas when the active orator has collaboration enabled, triggering Narratron narration as usual.

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

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.

