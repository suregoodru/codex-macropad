# Codex Macropad Companion

A local macOS companion that shows live Codex task status on the display and RGB lighting of an Ergohaven M4CR0Pad v3. It uses Codex lifecycle hooks and the macropad's existing Raw HID interface, without firmware or Entropy modifications.

> Compatibility: macOS 13 or later and a USB-connected Ergohaven M4CR0Pad v3. Other devices and operating systems have not been tested.

## What it shows

| Codex state | Display | RGB |
| --- | --- | --- |
| Working | `ANALYZE`, `RESEARCH`, `REVIEW`, `EDIT`, `VERIFY`, or `RUN`; workspace, elapsed time, and active task count | Blue Solid |
| Waiting for manual approval or input | `APPROVAL: <workspace>` | Amber Breathing |
| Completed | `DONE: <workspace>` for 12 seconds | Green Breathing |
| No active tasks | The companion releases the display and exits | The previous RGB profile is restored |

Reliably identified `auto_review` and `guardian_subagent` requests remain `WORKING`. Missing, unreadable, malformed, or unknown reviewer context deliberately falls back to `APPROVAL` so a real user request is not missed. With concurrent tasks, the display priority is `approval > done > working`.

## Requirements

- macOS 13 or later;
- Swift 6.0 or later (Xcode Command Line Tools or Xcode);
- Python 3.9 or later;
- an Ergohaven M4CR0Pad v3 connected over USB.

Entropy may remain open while the companion is running.

## Install

```bash
git clone https://github.com/suregoodru/codex-macropad.git
cd codex-macropad
./scripts/install.sh
```

The installer builds `MacropadDisplay`, installs it together with the Python hook in `~/Library/Application Support/CodexMacropad/`, and idempotently adds six lifecycle handlers to `~/.codex/hooks.json`. Existing hooks and `~/.codex/config.toml` are preserved.

During install or uninstall, the scripts remove the project's old fake `~/Applications/Spotify.app` only when its bundle ID is exactly `ru.suregood.codex-macropad.spotify`. A real Spotify installation is preserved.

If Codex was open during the first installation, create a new task or restart the app so it reloads the hook configuration.

## How it works

Codex lifecycle hooks maintain a small local state file. The Swift helper reads that state and sends display text through the M4CR0Pad's existing Raw HID artist/title channel. It also applies temporary VialRGB profiles for working, approval, and completion states.

Before applying the first status color, the helper stores the current VialRGB tuple. After the last task ends, that tuple is restored. The companion never sends the VialRGB save command, so the macropad's EEPROM configuration is not changed.

The display refreshes every 0.5 seconds. Working and approval states expire after six hours without a new event. The helper uses a controller lock, so only one process controls the device at a time; no LaunchAgent or persistent background service is installed.

## Privacy

The state file contains only session, turn, and tool-call identifiers; the workspace directory basename; phase/status enums; timestamps; and, for tool calls that wait for direct input, the boolean `awaits_user_input`.

`rgb-baseline.json` temporarily stores the RGB tuple plus the device VID, PID, and serial needed to guard restoration. It remains local, uses mode `0600`, and is removed after a successful restore.

`state.json` and status processing do not persist prompts, responses, transcript contents, tool inputs or outputs, shell commands, permission descriptions, or workspace full paths.

The installer necessarily writes its managed hook command, including the installed hook's absolute path, to `~/.codex/hooks.json`. No event-derived content or these configuration values are sent over the network by this project.

All classification happens locally. The hooks do not call the Codex API, create model turns, or send data over the network.

The hook may read the current transcript locally to distinguish Auto-review from a real user approval and to recover terminal events. Transcript contents and reviewer metadata are not copied into the state file.

## Build and test

```bash
./scripts/build-helper.sh
python3 -m unittest discover -s tests -v
CLANG_MODULE_CACHE_PATH=/private/tmp/codex-macropad-clang-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/private/tmp/codex-macropad-swiftpm-cache \
  swift test --disable-sandbox
```

The standalone helper is written to `build/MacropadDisplay`.

## Uninstall

```bash
./scripts/uninstall.sh
```

The uninstaller removes only the installed helper, managed hook, companion state files, and the six handlers created by this project. It preserves other Codex settings and hooks.

As during installation, it removes the legacy fake `~/Applications/Spotify.app` only when the exact managed bundle ID matches; a real Spotify installation is preserved.

## Limitations

- Codex hooks do not expose chat read/unread state or a stable user-facing chat title, so the display uses the workspace name.
- Some hosted or specialized tools may not emit `PreToolUse` and `PostToolUse`; the display remains in `ANALYZE` while they run.
- Each display field is limited to 30 valid UTF-8 bytes by the existing device channel.
- If the device is disconnected before RGB restoration, the recovery file is retained for the next hook event. The macropad itself reloads its saved RGB configuration after a physical reconnect.

## License

MIT. See [LICENSE](LICENSE).

## Hardware

M4CR0Pad v3 is designed and manufactured by [Ergohaven](https://ergohaven.xyz/). This project currently targets and has only been tested with that device.
