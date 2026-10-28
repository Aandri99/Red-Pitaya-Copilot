# Red Pitaya Copilot — Beta

**Free beta for Red Pitaya users.** This is an early testing release: expect bugs and changes.

Includes application development, FPGA development, deployment planning, and single-board/two-board loopback qualification. Pinned OS profiles: 2.00-35, 2.07-48, and 3.00-57.

## Download

The beta ZIP upload is pending. These instructions apply once `red-pitaya-copilot-beta.zip` is available here.

## Installation

Use a compatible local ChatGPT desktop/Codex client that supports local plugin marketplaces. Uploading a ZIP into a normal chat does not install it.

1. Download and extract the entire beta ZIP to a permanent folder.
2. Keep the hidden `.agents` folder and the `plugins` folder together.
3. Open the extracted folder as a project. Review and trust it when prompted.
4. Restart the desktop app and reopen that project. In **Plugins**, select **Red Pitaya Beta** and install **Red Pitaya Copilot**.
5. Start a new chat in the project.

[Official local marketplace guide](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually). Installation on recipient accounts remains unverified.

## First prompt

“Prepare an offline deployment plan for my Red Pitaya project. Ask for my board model, OS release, and missing details first.”

Start with offline planning. Live board changes, FPGA loading, and physical output tests require the checks and explicit authorizations defined by the plugin.

## Feedback

[Open an issue](https://github.com/Aandri99/Red-Pitaya-Copilot/issues) with your desktop app version, operating system, board model and OS release, steps to reproduce, expected result, and actual result. Remove passwords, keys, tokens, and unrelated personal information.

## Testing limitations

Syntax checks, 106 documentation links, and 16 shared profile/contract tests passed. Application self-tests reported 98 passing checks and six local WebSocket checks blocked by Windows socket permissions (WinError 10013). The full suite did not pass; later stages and live hardware tests remain unverified.

## Updates

Download the latest beta ZIP, extract it to a new folder, install from its marketplace, restart, and start a new chat.

This GitHub repository is public. Official Plugins Directory publication is a separate review process and has not been completed.
