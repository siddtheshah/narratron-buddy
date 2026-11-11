# Narratron Theater Authoring

Build your whole theater in your AI app, then upload it to Narratron when ready.

## ChatGPT

Download the authoring kit from Narratron. Attach the ZIP to a chat with file creation enabled and ask:

> Read the Narratron create-theater skill and bundled references in this kit. Help me create a complete theater, keep editing the actual files as we work, and give me a validated theater ZIP for upload when I am ready. Start by helping me develop my premise.

The attachment workflow supplies instructions and references; it does not install a plugin. A chat that cannot read ZIPs can use the extracted skill and reference files as attachments instead.

For a local ChatGPT Work or Codex plugin installation, extract the kit to a dedicated workspace. It includes a `.agents/plugins/marketplace.json` catalog pointing to the `narratron-theater` plugin. Follow the [official local plugin instructions](https://developers.openai.com/plugins/build/plugins) to register that workspace's marketplace and install the plugin. Keep existing marketplace catalogs when installing into an established workspace. Invoke the installed create-theater skill in a new chat. Plugin availability depends on the app and workspace configuration. This package has not been published to the public plugin directory.

## Antigravity and other AI editors

Extract the kit, open its folder as a workspace, and ask the agent to read `narratron-theater/AGENTS.md` and follow `skills/create-theater/SKILL.md` inside that plugin folder. Keep the new theater in a separate sibling folder. If you already have a theater, open that folder alongside the kit and ask the agent to continue editing it.

## Upload when ready

Ask for a ZIP with `theater.yaml` at its root. Extract it, open https://narratron.app/theater-editor, and select **Upload a folder**. Choose the extracted theater folder, then **Save & test**. For an existing theater use **＋ Folder**; matching paths are replaced, but omitted files are not deleted. Keep a download of the previous version if you want a backup.

The kit contains public authoring instructions, reference documentation, and default configuration. To continue a theater you have already started in Narratron, also download that theater and give it to your AI app. Opening ChatGPT or Antigravity does not transfer files automatically.

## Development

The download is assembled from this plugin plus maintained files in `site_docs/` and `theater_default.yaml` by `services/theater_authoring.py`. The generated plugin bundles references and assets so users do not need to clone the Narratron repository. Update the plugin version when changing the distributed workflow.
