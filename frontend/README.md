# Frontend modules

Sources and adjacent `*_test.ts` browser tests are grouped by their consumers:

| Directory | Responsibility |
| --- | --- |
| `canvas/` | Canvas orchestration, drawing, navigation, and canvas controls |
| `pages/` | Site page entry points, including theater tools, OBS, and popout chat |
| `remote/` | Mobile remote controls, login, audio, and game integration |
| `audio/` | Audio capture, PCM conversion, and the audio worklet |
| `game/` | Shared game runtime, rendering, and HTML panels |
| `shared/` | Cross-page auth, chat, polling, effects, and site controls |
| `testing/` | Shared Playwright fixtures and their self-tests |
| `static/` | Stylesheets, vendor scripts, brand images, static fragments, and gitignored `generated/` JS |
| `templates/` | HTML page templates, favicons, and join-page carousel artwork |

`npm run build` compiles production sources to matching paths under
`frontend/static/generated/`. For example, `pages/profile.ts` is served at
`/static/generated/pages/profile.js`. Use these canonical URLs in templates and
browser tests, and `.js` extensions in relative module imports. Shared modules
must have one canonical URL so consumers share their singleton state.

Run `npm run typecheck`, `npm run build`, and `npm run test:ui` to verify changes.
Shared Playwright fixtures in `frontend/testing/` are included in type checking
and excluded from production builds. Test Lab browser modules live in
`testlab/frontend/`. Local Python startup uses `utils/frontend_build.py`
to check sources across every frontend directory.
