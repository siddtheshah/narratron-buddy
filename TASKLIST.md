# Narratron Task List

## App Deployment & Infrastructure
- [ ] Cleanup daemon (triggers on session creation, removes sessions not marked durable and older than 7 days)
- [ ] Ensure session dir works with mounted google cloud run filesystem. Create flag to set up resource paths per environment. 

## Frontend
- [ ] About page. Need to have links to related projects and community servers. Bug reporting guidelines.
- [ ] Clean up frontend page. Add blurred background carousel.
- [ ] Add a favicon for tabs.


## Auth, Security & Monetization
- [ ] Determine fair pricing, based on image tool calls and live minutes used.
- [ ] Cloudflare

## Narratron UI & Canvas Features

- [ ] Agent state observability for music
- [ ] Style defaults. Let user give a style spec which will be fed to agent's create_image tool.
- [ ] Smart music randomization. Have the playlist pick a random song, and then subsequent songs will be picked from the remaining, until looped.
- [ ] Glamorize images. Simple animation layer in a JS library that applies to existing images, rather than creating large video.
