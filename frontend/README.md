# Frontend modules

Sources and adjacent `*_test.ts` browser tests are grouped by their consumers:

| Directory | Responsibility |
| --- | --- |
| `canvas/` | Canvas orchestration, drawing, navigation, and canvas controls |
| `pages/` | Site page entry points, including theater tools, OBS, and popout chat |
| `remote/` | Mobile remote controls, login, audio, and game integration |
| `testlab/` | Developer lab and benchmark entry points |
| `audio/` | Audio capture, PCM conversion, and the audio worklet |
| `game/` | Shared game runtime, rendering, and HTML panels |
| `shared/` | Cross-page auth, chat, polling, effects, and site controls |

`npm run build` compiles production sources to matching paths under
`static/generated/`. For example, `pages/profile.ts` is served at
`/static/generated/pages/profile.js`. Use these canonical URLs in templates and
browser tests, and `.js` extensions in relative module imports. Shared modules
must have one canonical URL so consumers share their singleton state.

Run `npm run typecheck`, `npm run build`, and `npm run test:ui` to verify changes.
Shared browser fixtures remain in `testing/ui/`. Local Python startup uses
`utils/frontend_build.py` to check sources across every frontend directory.
