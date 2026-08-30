#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Idempotent patch for the noctalia "tadomika_ari/w-engine" plugin (community
# plugins repo, materialized copy) so its panel remembers the last monitor view
# ("All" or a specific output) and the multi-select mode across restarts.
#
# Without this patch the panel always reopens on the FOCUSED output (eDP-1 after
# a reboot), with multi-select forced on by whatever selection is stored per
# output, so every boot the user has to uncheck "select multiple", switch the
# output from eDP-1 to ALL and click their wallpaper again.
#
# The script is safe to run repeatedly: it skips files that already carry the
# patch marker. Run with the materialized plugin dir as first argument, or it
# defaults to ~/.local/state/noctalia/plugins/materialized/community/w-engine.

import os
import sys

TAB = "\t"

MARKER = "-- noctalia patch: persist panel state"


def default_base():
    return os.path.join(
        os.path.expanduser("~"),
        ".local/state/noctalia/plugins/materialized/community/w-engine",
    )


def read(path):
    with open(path, "r", errors="replace") as f:
        return f.read()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def new_adopt_status():
    return [
        "local function adoptStatus()",
        TAB + "local state = outputStatus()",
        TAB + "selected = {}",
        TAB + 'if outputName == "All" then',
        TAB + TAB + "-- Aggregate the per-output selections so the All view mirrors",
        TAB + TAB + "-- what is actually saved for every monitor.",
        TAB + TAB + "local seen = {}",
        TAB + TAB + "local outputs = type(status) == \"table\" and status.outputs or nil",
        TAB + TAB + 'if type(outputs) == "table" then',
        TAB + TAB + TAB + "for _, output in ipairs(noctalia.outputs()) do",
        TAB + TAB + TAB + TAB + 'local entry = type(outputs[output.name]) == "table" and outputs[output.name] or nil',
        TAB + TAB + TAB + TAB + 'if type(entry) == "table" and type(entry.selection) == "table" then',
        TAB + TAB + TAB + TAB + TAB + "for _, id in ipairs(entry.selection) do",
        TAB + TAB + TAB + TAB + TAB + TAB + 'if type(id) == "string" and not seen[id] then',
        TAB + TAB + TAB + TAB + TAB + TAB + TAB + "seen[id] = true",
        TAB + TAB + TAB + TAB + TAB + TAB + TAB + "table.insert(selected, id)",
        TAB + TAB + TAB + TAB + TAB + TAB + "end",
        TAB + TAB + TAB + TAB + TAB + "end",
        TAB + TAB + TAB + TAB + "end",
        TAB + TAB + TAB + "end",
        TAB + TAB + "end",
        TAB + "elseif type(state.selection) == \"table\" then",
        TAB + TAB + "for _, id in ipairs(state.selection) do",
        TAB + TAB + TAB + "table.insert(selected, id)",
        TAB + TAB + "end",
        TAB + "end",
        TAB + "if tonumber(state.cycle_minutes) then",
        TAB + TAB + "minutesText = tostring(math.floor(tonumber(state.cycle_minutes)))",
        TAB + "end",
        TAB + 'order = state.cycle_order == "random" and "random" or "sequential"',
        TAB + "-- Only force multi-select when no saved panel state exists.",
        TAB + "if panelState == nil and (state.cycle_enabled or #selected > 0) then",
        TAB +         TAB + "multiSelect = true",
        TAB + "end",
        "end",
        "",
    ]


def patch_start_luau(path):
    text = read(path)
    if MARKER in text:
        print("already patched: " + path)
        return
    lines = text.split("\n")

    # 1. Add panel_state to the service's data table.
    for i, line in enumerate(lines):
        if line.strip().startswith("options = {},"):
            lines.insert(
                i,
                TAB + "panel_state = {}, -- persisted panel UI state, see w-engine-panel.luau",
            )
            break
    else:
        raise SystemExit("start.luau: 'options = {},' anchor not found")

    # 2. Load panel_state on startup so saveData() does not wipe it.
    for i, line in enumerate(lines):
        if line.strip() == "data.favorites = asTable(decoded.favorites)":
            lines.insert(i + 1, TAB + "data.panel_state = asTable(decoded.panel_state)")
            break
    else:
        raise SystemExit("start.luau: favorites load anchor not found")

    text = "\n".join(lines).replace("--!nonstrict", "--!nonstrict\n" + MARKER, 1)
    write(path, text)
    print("patched: " + path)


def patch_panel_luau(path):
    text = read(path)
    if MARKER in text:
        print("already patched: " + path)
        return
    lines = text.split("\n")
    original = "\n".join(lines)

    # 1. Persistence helpers right after saveData().
    for i, line in enumerate(lines):
        if line.strip() == "local function saveData(data)":
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].strip() == "end":
                    helpers = [
                        "-- Panel UI state (output/multiSelect) lives in data.json so the",
                        "-- panel reopens with the monitor view the user last used",
                        "-- (e.g. All) instead of the focused output.",
                        "-- These are globals on purpose: header() (declared earlier) also",
                        "-- calls persistPanelState().",
                        "function loadPanelState()",
                        TAB + "local d = loadData()",
                        TAB + 'return type(d.panel_state) == "table" and d.panel_state or nil',
                        "end",
                        "",
                        "function savePanelState(nextState)",
                        TAB + "local d = loadData()",
                        TAB + "d.panel_state = nextState",
                        TAB + "saveData(d)",
                        "end",
                        "",
                        "function persistPanelState()",
                        TAB + "savePanelState({ output = outputName, multi_select = multiSelect == true })",
                        "end",
                    ]
                    lines[i + 1 : j + 1] = lines[i + 1 : j + 1] + helpers
                    break
            break
    else:
        pass
    if not any(line.strip().startswith("function loadPanelState()") for line in lines):
        raise SystemExit("panel: saveData anchor not found")

    # 2. Persist when the multi-select toggle changes.
    for i, line in enumerate(lines):
        if line.strip() == "multiSelect = not multiSelect":
            indent = line[: len(line) - len(line.lstrip())]
            lines.insert(i + 1, indent + "persistPanelState()")
            break
    else:
        raise SystemExit("panel: multiSelect toggle anchor not found")

    # 3. Persist when the monitor view changes.
    start = next(i for i, l in enumerate(lines) if l.strip() == "function outputSelected(_index, label)")
    for i in range(start, start + 12):
        if lines[i].strip() == "adoptStatus()":
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines.insert(i + 1, indent + "persistPanelState()")
            break
    else:
        raise SystemExit("panel: outputSelected adoptStatus anchor not found")

    # 4. Replace adoptStatus() with the All-aware version.
    i0 = next(i for i, l in enumerate(lines) if l.strip() == "local function adoptStatus()")
    i1 = next(i for i, l in enumerate(lines) if l.strip() == "function outputSelected(_index, label)")
    lines[i0:i1] = new_adopt_status()
    panel_state_inject = "panelState = loadPanelState()"

    # 5. Open with the saved view, not the focused output.
    i0 = next(i for i, l in enumerate(lines) if l.strip() == "function onOpen(_context)")
    for i in range(i0, i0 + 8):
        if lines[i].strip() == "outputName = noctalia.focusedOutputName() or outputName":
            for j in range(i, min(i + 6, len(lines))):
                if lines[j].strip() == "end":
                    block = [
                        TAB + "-- First try the monitor view from the last session,",
                        TAB + "-- falling back to the focused output when it is gone.",
                        TAB + panel_state_inject,
                        TAB + "if panelState == nil then",
                        TAB + TAB + "-- Nothing saved yet: default to All, single-click mode.",
                        TAB + TAB + "local outputs = noctalia.outputs()",
                        TAB + TAB + 'panelState = { output = #outputs > 1 and "All" or (outputs[1] and outputs[1].name or "All"), multi_select = false }',
                        TAB + TAB + "savePanelState(panelState)",
                        TAB + "end",
                        TAB + 'local savedOutput = (type(panelState) == "table") and panelState.output or nil',
                        TAB + "local validOutput = false",
                        TAB + 'if savedOutput == "All" then',
                        TAB + TAB + "validOutput = true",
                        TAB + 'elseif type(savedOutput) == "string" and savedOutput ~= "" then',
                        TAB + TAB + "for _, output in ipairs(noctalia.outputs()) do",
                        TAB + TAB + TAB + "if output.name == savedOutput then",
                        TAB + TAB + TAB + TAB + "validOutput = true",
                        TAB + TAB + TAB + "end",
                        TAB + TAB + "end",
                        TAB + "end",
                        TAB + "if validOutput then",
                        TAB + TAB + "outputName = savedOutput",
                        TAB + TAB + "multiSelect = panelState.multi_select == true",
                        TAB + "else",
                        TAB + TAB + "panelState = nil",
                        TAB + TAB + "outputName = noctalia.focusedOutputName() or outputName",
                        TAB + TAB + "if not outputName then",
                        TAB + TAB + TAB + "local outputs = noctalia.outputs()",
                        TAB + TAB + TAB + "outputName = outputs[1] and outputs[1].name or nil",
                        TAB + TAB + "end",
                        TAB + "end",
                    ]
                    lines[i : j + 1] = block
                    break
            break
    else:
        raise SystemExit("panel: onOpen output anchor not found")

    text = "\n".join(lines)
    if text == original:
        raise SystemExit("panel: no edits applied")
    text = text.replace("--!nonstrict", "--!nonstrict\n" + MARKER, 1)
    write(path, text)
    print("patched: " + path)


def seed_data_json(datadir):
    path = os.path.join(datadir, "data.json")
    if not os.path.exists(path):
        print("skip (no data.json yet): " + path)
        return
    import json

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("panel_state"):
        print("data.json already seeded")
        return
    # First-ever use: match the previous manual workflow (apply on all monitors,
    # single click) until the user picks something else in the panel.
    data["panel_state"] = {"multi_select": False, "output": "All"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("seeded data.json: " + path)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else default_base()
    patch_start_luau(os.path.join(base, "start.luau"))
    patch_panel_luau(os.path.join(base, "w-engine-panel.luau"))
    seed_data_json(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(base))),
            "data",
            "tadomika_ari",
            "w-engine",
        )
    )


if __name__ == "__main__":
    main()
