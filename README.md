# Red Pitaya Copilot — Beta

**Free beta for Red Pitaya users.** This is an early testing release. Expect bugs and changes, and report feedback through this repository's [Issues](https://github.com/Aandri99/Red-Pitaya-Copilot/issues).

Red Pitaya Copilot adds five workflows to a compatible local ChatGPT desktop/Codex client: application development, FPGA development, deployment planning, single-board loopback qualification, and two-board loopback qualification.

Supported pinned OS profiles: **2.00-35, 2.07-48, and 3.00-57**. Bundled plugin version: `3.0.0+codex.20260818172339`.

## Install the beta

You need a compatible desktop client that supports local plugin marketplaces. This package is intended for local desktop use.

1. Clone this repository: `git clone https://github.com/Aandri99/Red-Pitaya-Copilot.git`.
2. Keep the repository's `.agents` folder and `plugins` folder together.
3. Open the repository folder as a project in your compatible desktop/Codex client. Review the files and trust the project when prompted.
4. Restart the desktop app and reopen that project. Open **Plugins**, choose the **Red Pitaya Beta** marketplace/source, and install **Red Pitaya Copilot**.
5. Start a new chat in that project and try a prompt below.

This repository includes the local marketplace catalog and the complete plugin source. You do not need an invitation to this public repository. Installation on every recipient account and desktop version has not yet been verified. If the local source does not appear, report your operating system, app version, and the step that failed.

### Add it as a marketplace

The marketplace is at `.agents/plugins/marketplace.json`, and the plugin is at `plugins/red-pitaya-copilot`.

Tested installation with Codex CLI:

```sh
codex plugin marketplace add Aandri99/Red-Pitaya-Copilot
codex plugin add red-pitaya-copilot@red-pitaya-beta
```

The GitHub marketplace and plugin installation were tested successfully on Windows on October 28, 2026. Codex reported the plugin as installed and enabled; all 126 installed source files matched the GitHub copy and all five skills were present. Start a new chat after installation. You can also use the desktop plugin browser. See the [official local marketplace guide](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually).

## Try these prompts

- “Help me design a Red Pitaya instrument app. Ask me for my board model, OS release, and requirements first.”
- “Create a Red Pitaya FPGA project plan and explain the validation steps.”
- “Prepare an offline deployment plan for my Red Pitaya project. Ask me for missing board details.”

Start with offline planning. Live board changes, FPGA loading, and physical output tests require the evidence, checks, and explicit authorizations defined by the plugin. A beta or passing offline checks does not establish hardware safety or compatibility with an unverified board.

## Send feedback

Open an [issue](https://github.com/Aandri99/Red-Pitaya-Copilot/issues) and include:

- Desktop app version and operating system.
- Red Pitaya model and OS release, if relevant.
- What you asked, what you expected, and what happened.
- Reproduction steps and relevant error messages.

Remove passwords, private keys, account tokens, and unrelated personal information before posting.

## Updates and public release

Pull the latest changes with `git pull`, then refresh/reinstall the plugin through your client as needed. Restart the client and start a new chat.

This GitHub beta is publicly downloadable. Publication in the official Plugins Directory is a separate review process and has **not** been completed.

For the detailed development workflow and offline validation commands, see the [plugin documentation](plugins/red-pitaya-copilot/README.md).

## Validation status

On October 28, 2026, installation from this GitHub marketplace succeeded and the complete offline suite passed: **216 checks, zero failures**. Syntax checks and 106 relative documentation links also passed. The six local WebSocket checks blocked in the earlier restricted run passed when rerun with local networking available. See the [test report](TESTING.md) for scope and limitations. Live board operation and the quality of responses in a new chat remain unverified.
