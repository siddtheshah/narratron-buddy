# Narratron Task List

## 1. App Deployment & Infrastructure
- [x] Database support for session artifacts (for scalability and interoperability). Export session to database on save.
- [ ] Cleanup daemon (triggers on session creation, removes sessions not marked durable and older than 7 days)

## 2. Additional Pages
- [ ] How-To page
- [ ] Pricing page (`pricing.txt` / pricing page content)
- [x] Stats page (number of accounts, active users in the last 7 days, session views)

## 3. Auth, Security & Monetization
- [ ] Password reset flow
- [ ] Improve auth: ensure access tokens are invalidated when a user logs out
- [ ] Payments setup (buy credits)

## 4. Narratron UI & Canvas Features
- [ ] Cinematic mode: exterior panel that only shows UI on mouseover
- [ ] Show image with transition (enhanced agent control)
- [ ] Autofade: let images decay to dark unless model sets `persist` to true
- [x] Share link: easy link to share sessions
- [x] Disable doodling option: let orator/session owner control whether doodles will be displayed on canvas (persisted to session state)
- [ ] Session export: gather all created images and download as a ZIP file
- [ ] Look back: pop-up arrows to navigate previous and next images
- [ ] Stream plugin: whenever mic is active in canvas, focus shifts to stream and returns when mic is off
