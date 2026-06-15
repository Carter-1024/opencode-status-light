# opencode Status Light

Ubuntu top-bar floating status UI for opencode. It shows opencode runtime state, current account quota summary, and autostarts with opencode.

## Features

- Horizontal traffic-light bar under the Ubuntu top panel.
- Global opencode plugin, works from any project directory.
- Runtime states from opencode internal events.
- Quota summary using the same query logic as `opencode-mystatus`.
- Read-only SQLite fallback if event status file stops changing.
- Autostart when opencode starts; no manual UI launch needed.

## Install

Install Ubuntu GTK dependencies:

```bash
sudo apt update
sudo apt install -y python3-gi gir1.2-gtk-3.0
```

Install the status light into your opencode config:

```bash
npm run install:local
```

The installer copies files to:

```text
~/.config/opencode/status-light/
```

It also updates:

```text
~/.config/opencode/opencode.jsonc
```

Then restart opencode:

```bash
opencode
```

## Uninstall

```bash
npm run uninstall:local
```

Restart opencode after uninstalling.

## Manual Run

```bash
~/.config/opencode/status-light/status-light.py
```

Adjust vertical position under the Ubuntu top panel:

```bash
~/.config/opencode/status-light/status-light.py --top-offset 40
```

Disable autostart for one opencode launch:

```bash
OPENCODE_STATUS_LIGHT_AUTOSTART=0 opencode
```

## Status Mapping

| opencode state | UI |
| --- | --- |
| Waiting for input / step finished | Green, `等待输入` |
| Session updated | Green, `会话已更新` |
| Text response completed | Green, `回复完成` |
| Step started | Yellow, `正在处理` |
| Reasoning / thinking | Yellow, `正在思考` |
| Text streaming | Yellow, `正在回复` |
| Tool pending / running | Yellow, `正在执行工具` |
| Tool completed | Yellow, `工具完成` |
| Permission resolved | Yellow, `继续处理` |
| Permission wait | Red, `等待权限确认` |
| Error / failed tool | Red, `执行出错` |
| No events yet | Gray, `opencode 未启动或暂无事件` |

## Files

- `plugin/status-light.ts`: opencode plugin source.
- `status-light.py`: GTK floating UI.
- `scripts/install.mjs`: installs and registers the plugin globally.
- `scripts/uninstall.mjs`: removes installed files and unregisters the plugin.

Runtime files:

- `/tmp/opencode-status-light.json`: current opencode state.
- `/tmp/opencode-quota-status.json`: account quota summary.

## Quota Refresh

Quota refreshes after each completed assistant turn. A 5-minute timer is kept as a fallback.

```bash
OPENCODE_STATUS_LIGHT_QUOTA_REFRESH_MS=60000 opencode
```

Non-forced refreshes are rate-limited to 15 seconds by default:

```bash
OPENCODE_STATUS_LIGHT_QUOTA_MIN_REFRESH_MS=30000 opencode
```

## Optional Follow Mode

The default UI is a top-bar overlay. You can still use TUI-following mode manually:

```bash
~/.config/opencode/status-light/status-light.py --follow-tui
```

For X11 window pinning:

```bash
~/.config/opencode/status-light/status-light.py --follow-tui --follow-window-id "$WINDOWID"
```
