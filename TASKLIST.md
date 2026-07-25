# Narratron Task List

## 1. App Deployment & Infrastructure
- [x] Database support for session artifacts (for scalability and interoperability). Export session to database on save.
- [ ] Cleanup daemon (triggers on session creation, removes sessions not marked durable and older than 7 days)

## 2. Additional Pages
- [ ] How-To page
- [ ] Pricing page (`pricing.txt` / pricing page content)
- [x] Stats page (number of accounts, active users in the last 7 days, session views)

## 3. Auth, Security & Monetization   
- [x] Password reset flow
- [x] Improve auth: ensure access tokens are invalidated when a user logs out
- [x] Payments setup (buy credits)

## 4. Narratron UI & Canvas Features
- [x] Cinematic mode: exterior panel that only shows UI on mouseover
- [x] Show image with transition (enhanced agent control)
- [x] Share link: easy link to share sessions
- [x] Disable doodling option: let orator/session owner control whether doodles will be displayed on canvas (persisted to session state)
- [x] Session export: gather all created images and download as a ZIP file
- [x] Look back: pop-up arrows to navigate previous and next images
- [x] Stream plugin: whenever mic is active in canvas, focus shifts in stream and returns when mic is off

## Chat
- [x] Have user name be associated with the chat message in the UI. Handling for anonymous viewers.

## Database Decoupling
- [x] Have a separate database manager implementation for a remote database accessed through cloud.