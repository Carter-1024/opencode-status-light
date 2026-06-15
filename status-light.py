#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango


DEFAULT_STATUS_FILE = "/tmp/opencode-status-light.json"
DEFAULT_QUOTA_FILE = "/tmp/opencode-quota-status.json"
DEFAULT_DB_FILE = str(Path.home() / ".local/share/opencode/opencode.db")

COLORS = {
    "green": "#27d86c",
    "yellow": "#ffd447",
    "red": "#ff4d4f",
    "gray": "#7b8492",
}

TRAFFIC_LIGHTS = ("red", "yellow", "green")


def time_since(mtime):
    return datetime.now(timezone.utc).timestamp() - mtime


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def status_from_event(event_type, data):
    part = data.get("part") if isinstance(data.get("part"), dict) else {}
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    part_type = part.get("type")
    tool_status = state.get("status")
    finish = part.get("reason") or info.get("finish")
    tool = part.get("tool")
    part_time = part.get("time") if isinstance(part.get("time"), dict) else {}
    updated_at = now_iso()

    is_permission_event = "permission" in event_type or part_type == "permission"
    is_permission_resolved = "replied" in event_type or "resolved" in event_type

    if is_permission_event and not is_permission_resolved:
        return {
            "state": "permission-wait",
            "color": "red",
            "label": "等待权限确认",
            "detail": "opencode 正在等待你批准操作",
            "updatedAt": updated_at,
        }
    if is_permission_event and is_permission_resolved:
        return {
            "state": "permission-resolved",
            "color": "yellow",
            "label": "继续处理",
            "detail": "权限已确认，opencode 继续执行",
            "updatedAt": updated_at,
        }
    if tool_status in ("failed", "error") or finish == "error":
        return {
            "state": "error",
            "color": "red",
            "label": "执行出错",
            "detail": f"{tool} 失败" if tool else "opencode 报告错误",
            "updatedAt": updated_at,
        }
    if part_type == "tool" and tool_status in ("running", "pending"):
        return {
            "state": "running-tool",
            "color": "yellow",
            "label": "正在执行工具",
            "detail": f"工具：{tool}" if tool else "工具调用进行中",
            "updatedAt": updated_at,
        }
    if part_type == "tool" and tool_status == "completed":
        return {
            "state": "tool-completed",
            "color": "yellow",
            "label": "工具完成",
            "detail": f"{tool} 执行完成，整理结果中" if tool else "工具执行完成，整理结果中",
            "updatedAt": updated_at,
        }
    if part_type == "reasoning":
        return {
            "state": "thinking",
            "color": "yellow",
            "label": "正在思考",
            "detail": "模型正在生成 reasoning",
            "updatedAt": updated_at,
        }
    if part_type == "text":
        completed = bool(part_time.get("end"))
        return {
            "state": "responding",
            "color": "green" if completed else "yellow",
            "label": "回复完成" if completed else "正在回复",
            "detail": "assistant 已输出文本" if completed else "assistant 正在输出文本",
            "updatedAt": updated_at,
        }
    if part_type == "step-start":
        return {
            "state": "thinking",
            "color": "yellow",
            "label": "正在处理",
            "detail": "新的 assistant step 已开始",
            "updatedAt": updated_at,
        }
    if part_type == "step-finish" or (isinstance(info.get("time"), dict) and info["time"].get("completed")):
        return {
            "state": "waiting-input",
            "color": "green",
            "label": "等待输入",
            "detail": f"上一步结束：{finish}" if finish else "思考结束，等待你的下一条消息",
            "updatedAt": updated_at,
        }
    if event_type == "session.updated.1":
        return {
            "state": "session-updated",
            "color": "green",
            "label": "会话已更新",
            "detail": "opencode 已同步会话状态",
            "updatedAt": updated_at,
        }
    return None


class StatusLight(Gtk.Window):
    def __init__(self, status_file: str, quota_file: str, poll_ms: int, follow_tui: bool, db_file: str, follow_window_id: str, top_offset: int):
        super().__init__(title="opencode status")
        self.status_file = Path(status_file)
        self.quota_file = Path(quota_file)
        self.db_file = Path(db_file)
        self.top_offset = top_offset
        self.poll_ms = poll_ms
        self.follow_window_id = follow_window_id.strip()
        self.follow_tui = follow_tui and (
            self.follow_window_id or shutil.which("xprop") is not None or shutil.which("xdotool") is not None
        )
        self.drag_origin = None

        self.set_decorated(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_resizable(False)
        self.set_app_paintable(True)
        self.set_default_size(760, 82)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.content_box.set_margin_top(10)
        self.content_box.set_margin_bottom(10)
        self.content_box.set_margin_start(16)
        self.content_box.set_margin_end(16)
        self.add(self.content_box)

        self.traffic_body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        self.traffic_body.get_style_context().add_class("traffic-body")
        self.traffic_body.set_valign(Gtk.Align.CENTER)
        self.traffic_body.set_margin_top(0)
        self.traffic_body.set_margin_bottom(0)
        self.traffic_body.set_margin_start(0)
        self.traffic_body.set_margin_end(0)
        self.content_box.pack_start(self.traffic_body, False, False, 0)

        self.lights = {}
        for light_name in TRAFFIC_LIGHTS:
            light = Gtk.EventBox()
            light.set_size_request(28, 28)
            style = light.get_style_context()
            style.add_class("traffic-light")
            style.add_class(light_name)
            style.add_class("off")
            self.traffic_body.pack_start(light, False, False, 0)
            self.lights[light_name] = light

        text_panel = Gtk.EventBox()
        text_panel.get_style_context().add_class("text-panel")
        self.content_box.pack_start(text_panel, True, True, 0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        text_box.set_margin_top(10)
        text_box.set_margin_bottom(10)
        text_box.set_margin_start(16)
        text_box.set_margin_end(16)
        text_box.set_valign(Gtk.Align.CENTER)
        text_panel.add(text_box)

        badge = Gtk.Label(label="OPENCODE")
        badge.set_xalign(0)
        badge.get_style_context().add_class("badge")
        self.label = Gtk.Label(label="读取状态中")
        self.label.set_xalign(0)
        self.label.get_style_context().add_class("title")
        self.detail = Gtk.Label(label=self.status_file.as_posix())
        self.detail.set_xalign(0)
        self.detail.set_ellipsize(Pango.EllipsizeMode.END)
        self.quota = Gtk.Label(label="额度读取中")
        self.quota.set_xalign(0)
        self.quota.set_ellipsize(Pango.EllipsizeMode.END)
        self.quota.get_style_context().add_class("quota")
        text_box.pack_start(badge, False, False, 0)
        text_box.pack_start(self.label, False, False, 0)
        text_box.pack_start(self.detail, True, True, 0)
        text_box.pack_start(self.quota, True, True, 0)

        self.color = "gray"
        self.blink = False
        self.last_mtime = 0.0
        self.last_quota_mtime = 0.0
        self.last_follow_position = None

        self.connect("destroy", Gtk.main_quit)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)

        self.apply_css()
        self.update_status()
        self.update_quota()
        self.move_to_top_bar()
        GLib.timeout_add(self.poll_ms, self.update_status)
        GLib.timeout_add(2000, self.update_quota)
        GLib.timeout_add(450, self.tick_blink)
        if self.follow_tui:
            GLib.timeout_add(900, self.follow_active_window)

    def apply_css(self):
        css = b"""
        window {
          background: rgba(9, 12, 18, 0.90);
          border-radius: 18px;
          border: 1px solid rgba(255,255,255,0.12);
        }
        label { color: rgba(255,255,255,0.88); font-size: 13px; }
        label.badge {
          color: rgba(196,208,230,0.78);
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 2px;
        }
        label.title { color: #ffffff; font-size: 19px; font-weight: 800; }
        label.quota { color: #bfe8ff; font-size: 13px; font-weight: 700; }
        .text-panel {
          background: rgba(0,0,0,0.34);
          border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.08);
        }
        .traffic-body {
          background: linear-gradient(180deg, #15171d, #08090d);
          border-radius: 20px;
          padding: 10px;
          border: 1px solid rgba(255,255,255,0.10);
        }
        .traffic-light {
          border-radius: 14px;
          min-width: 28px;
          min-height: 28px;
          border: 1px solid rgba(255,255,255,0.18);
        }
        .traffic-light.red { background: #ff4d4f; box-shadow: 0 0 18px rgba(255,77,79,0.88); }
        .traffic-light.yellow { background: #ffd447; box-shadow: 0 0 18px rgba(255,212,71,0.88); }
        .traffic-light.green { background: #27d86c; box-shadow: 0 0 18px rgba(39,216,108,0.88); }
        .traffic-light.red.dim { background: #8a2226; box-shadow: 0 0 8px rgba(255,77,79,0.35); }
        .traffic-light.off {
          background: #242832;
          box-shadow: inset 0 2px 8px rgba(0,0,0,0.75);
          border-color: rgba(255,255,255,0.07);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def move_to_top_bar(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        win_w, _win_h = self.get_size()
        x = geometry.x + max((geometry.width - win_w) // 2, 0)
        y = geometry.y + self.top_offset
        self.move(x, y)

    def update_light_style(self):
        active_color = self.color if self.color in TRAFFIC_LIGHTS else None
        for light_name, light in self.lights.items():
            style = light.get_style_context()
            style.remove_class("off")
            style.remove_class("dim")
            if light_name != active_color:
                style.add_class("off")
            elif light_name == "red" and self.blink:
                style.add_class("dim")

    def update_status(self):
        try:
            stat = self.status_file.stat()
            stale = time_since(stat.st_mtime) > 4.0
            if stat.st_mtime == self.last_mtime and not stale:
                return True
            self.last_mtime = stat.st_mtime
            status = self.read_db_status() if stale else None
            if status is None:
                status = json.loads(self.status_file.read_text(encoding="utf-8"))
            self.apply_status(status)
        except FileNotFoundError:
            status = self.read_db_status()
            if status is None:
                self.color = "gray"
                self.label.set_text("等待 opencode 事件")
                self.detail.set_text(f"未找到 {self.status_file}")
                self.update_light_style()
            else:
                self.apply_status(status)
        except Exception as exc:
            self.color = "red"
            self.label.set_text("状态读取失败")
            self.detail.set_text(str(exc))
            self.update_light_style()
        return True

    def update_quota(self):
        try:
            stat = self.quota_file.stat()
            if stat.st_mtime == self.last_quota_mtime:
                return True
            self.last_quota_mtime = stat.st_mtime
            quota = json.loads(self.quota_file.read_text(encoding="utf-8"))
            label = quota.get("label") or "未找到额度信息"
            updated_at = quota.get("updatedAt")
            if updated_at:
                label = f"{label} · {updated_at[11:16]}"
            self.quota.set_text(label)
        except FileNotFoundError:
            self.quota.set_text("额度等待中")
        except Exception:
            self.quota.set_text("额度读取失败")
        return True

    def apply_status(self, status):
        self.color = status.get("color", "gray")
        self.label.set_text(status.get("label", "未知状态"))
        detail = status.get("detail") or status.get("state") or ""
        source = status.get("source")
        updated_at = status.get("updatedAt")
        if updated_at:
            detail = f"{detail} · {updated_at[11:19]}"
        if source == "db-fallback":
            detail = f"{detail} · DB"
        self.detail.set_text(detail)
        self.update_light_style()

    def read_db_status(self):
        if not self.db_file.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{self.db_file}?mode=ro", uri=True, timeout=0.2)
            try:
                rows = conn.execute(
                    """
                    select type, data
                    from event
                    where aggregate_id = (select id from session order by time_updated desc limit 1)
                    order by seq desc
                    limit 40
                    """
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return None

        for event_type, data in rows:
            try:
                event_data = json.loads(data)
            except json.JSONDecodeError:
                continue
            status = status_from_event(event_type, event_data)
            if status is not None:
                status["source"] = "db-fallback"
                return status
        return None

    def tick_blink(self):
        self.blink = not self.blink
        if self.color == "red":
            self.update_light_style()
        return True

    def on_button_press(self, _widget, event):
        if event.button == 1:
            self.drag_origin = (event.x_root, event.y_root, *self.get_position())
        elif event.button == 3:
            Gtk.main_quit()

    def on_button_release(self, _widget, _event):
        self.drag_origin = None

    def on_motion(self, _widget, event):
        if self.drag_origin is None:
            return
        start_x, start_y, win_x, win_y = self.drag_origin
        self.move(int(win_x + event.x_root - start_x), int(win_y + event.y_root - start_y))

    def follow_active_window(self):
        try:
            geometry = self.get_follow_window_geometry()
            if geometry is None:
                return True

            win_w, _win_h = self.get_size()
            target_x = geometry["x"] + geometry["width"] - win_w - 18
            target_y = geometry["y"] + 28
            target = (max(target_x, 0), max(target_y, 0))
            if target != self.last_follow_position:
                self.move(*target)
                self.last_follow_position = target
        except Exception:
            pass
        return True

    def get_follow_window_geometry(self):
        if self.follow_window_id and shutil.which("xwininfo") is not None:
            return self.get_xwininfo_geometry(self.follow_window_id)

        if shutil.which("xprop") is not None and shutil.which("xwininfo") is not None:
            active_id = self.get_xprop_active_window_id()
            if active_id:
                name = self.get_xprop_window_name(active_id)
                if "opencode status" not in name.lower():
                    return self.get_xwininfo_geometry(active_id)

        if shutil.which("xdotool") is None:
            return None

        active_id = subprocess.check_output(["xdotool", "getactivewindow"], text=True, timeout=0.4).strip()
        if not active_id:
            return None
        active_name = subprocess.check_output(["xdotool", "getwindowname", active_id], text=True, timeout=0.4).strip()
        if "opencode status" in active_name.lower():
            return None
        return self.get_xdotool_geometry(active_id)

    def get_xdotool_geometry(self, window_id):
        geometry = subprocess.check_output(["xdotool", "getwindowgeometry", "--shell", window_id], text=True, timeout=0.4)
        values = {}
        for line in geometry.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.lower()] = int(value)
        return {"x": values["x"], "y": values["y"], "width": values["width"], "height": values["height"]}

    def get_xwininfo_geometry(self, window_id):
        info = subprocess.check_output(["xwininfo", "-id", window_id], text=True, timeout=0.4)
        patterns = {
            "x": r"Absolute upper-left X:\s+(-?\d+)",
            "y": r"Absolute upper-left Y:\s+(-?\d+)",
            "width": r"Width:\s+(\d+)",
            "height": r"Height:\s+(\d+)",
        }
        values = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, info)
            if match is None:
                return None
            values[key] = int(match.group(1))
        return values

    def get_xprop_active_window_id(self):
        output = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], text=True, timeout=0.4)
        match = re.search(r"#\s+(0x[0-9a-fA-F]+|0)", output)
        if match is None or match.group(1) == "0":
            return None
        return match.group(1)

    def get_xprop_window_name(self, window_id):
        try:
            output = subprocess.check_output(["xprop", "-id", window_id, "WM_NAME", "_NET_WM_NAME"], text=True, timeout=0.4)
        except Exception:
            return ""
        names = re.findall(r'"(.*?)"', output)
        return " ".join(names)


def main():
    parser = argparse.ArgumentParser(description="Floating opencode status light")
    parser.add_argument("--status-file", default=os.environ.get("OPENCODE_STATUS_LIGHT_FILE", DEFAULT_STATUS_FILE))
    parser.add_argument("--quota-file", default=os.environ.get("OPENCODE_STATUS_LIGHT_QUOTA_FILE", DEFAULT_QUOTA_FILE))
    parser.add_argument("--db-file", default=os.environ.get("OPENCODE_DB", DEFAULT_DB_FILE))
    parser.add_argument("--follow-window-id", default=os.environ.get("OPENCODE_STATUS_LIGHT_WINDOWID", os.environ.get("WINDOWID", "")))
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--follow-tui", action="store_true", help="Follow the active terminal/TUI window instead of staying under the top bar")
    parser.add_argument("--top-offset", type=int, default=int(os.environ.get("OPENCODE_STATUS_LIGHT_TOP_OFFSET", "34")))
    args = parser.parse_args()

    window = StatusLight(args.status_file, args.quota_file, args.poll_ms, args.follow_tui, args.db_file, args.follow_window_id, args.top_offset)
    window.show_all()
    window.move_to_top_bar()
    Gtk.main()


if __name__ == "__main__":
    main()
