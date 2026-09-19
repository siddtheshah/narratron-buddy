# Using Beyond20 with a Narratron canvas

[Beyond20](https://beyond20.here-for-more.info/) lets you roll from a D&D Beyond character sheet and share the result with your table. Narratron can display those rolls as cards in the canvas chat and, when collaboration is enabled, pass the result to the live narrator.

## Before you begin

You need all of the following:

- Latest version of Beyond20.
- A D&D Beyond character sheet open in the same browser profile as Beyond20.
- The Narratron canvas open in another tab in that same browser profile.
- Permission to act as an orator in that theater. The theater owner, the active orator, and users in the theater's contributors list can send rolls. Spectators cannot publish a roll to the shared chat.

## Set up the extension

1. Install or update the Narratron-enabled Beyond20 extension, then make sure it is enabled.
2. In the browser extension settings, allow Beyond20 to access the Narratron site. If your browser offers a site-access choice, choose **On this site** (or **On all sites**) for the Narratron host you use. For a local development canvas, allow `http://localhost:8000` as well.
3. Open the Narratron canvas for the theater and sign in with the account that has orator access. Keep this tab open for the session.
4. Open your D&D Beyond character sheet in a separate tab. Click the Beyond20 toolbar icon once if the extension asks to activate or configure the destination.
5. Reload the canvas and the character-sheet tab after changing extension permissions or settings.

## Roll during play

Use a Beyond20 roll control on the D&D Beyond sheet as you normally would: select an ability, skill, save, attack, spell, or supported dice expression and use the Beyond20 roll button.

Narratron adds the completed result to the canvas chat as a dice card. It includes available attack/check totals, critical successes or failures, and damage totals. HP updates emitted by Beyond20 are also added to chat.

## Let the narrator react to rolls

In the canvas, turn on **Viewer collaboration** for the theater. When it is enabled, an authorized orator's completed Beyond20 roll is also sent to the live narrator as a concise D&D dice-roll message. With collaboration turned off, rolls remain visible in chat but are not sent to the narrator.

This division is deliberate: the extension can be useful as a public dice log without changing the narrator's turn unless the host has enabled collaborative input.

## Troubleshooting

### No roll appears in the canvas

- Confirm that the canvas and D&D Beyond are open in the same browser profile where Beyond20 is installed.
- Confirm the extension has permission to run on the Narratron host, then reload both tabs.
- Verify that you are the theater owner, active orator, or are listed as an contributor. Viewer accounts are blocked from publishing extension roll events.
- Check that the Narratron-enabled build is installed. A standard Beyond20 release without the Narratron destination cannot deliver events to the canvas.

### The roll appears in chat but Narratron does not react

Enable Viewer collaboration in the canvas. Chat display does not require collaboration; forwarding a result to the live narrator does.

### I changed permissions or destinations and it still does not work

Reload the Narratron canvas and D&D Beyond character sheet. Browser extensions normally inject their page integration when a tab loads.

## Privacy and table etiquette

Roll details are shared with everyone who can view the theater chat. Treat a roll as table-visible before sending it. Beyond20 whisper behavior and any private information available to the extension should be configured in Beyond20 before rolling.
