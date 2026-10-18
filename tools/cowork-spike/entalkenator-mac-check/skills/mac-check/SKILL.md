---
name: mac-check
description: Run the enTalkenator Mac check. Use only when asked to "run the enTalkenator Mac check" or to test whether enTalkenator's Mac helper is reachable.
---

This checks whether the plugin's local helper runs on the person's Mac rather than in your
sandbox. The helper is the `mac-check` connector; `mac-check-inline` is the same helper
started a second way, in case the first can't find its files. Run every step even if one
fails, and don't fix or install anything.

1. Note which of the two connectors' tools you have. Call `where_am_i` on each you have.
2. Run `uname -a` and `pwd` in your own shell, so we can compare your sandbox with the helper.
3. Write a file named `entalkenator-marker-` plus eight random letters plus `.txt`, containing
   one line ("marker"), into the folder the person connected to this task (not your outputs
   folder). Note that folder's path as you see it. Call `find_folder` with the file name.
   Then delete the marker.
4. Tell the person a dialog is about to appear on their Mac, then call `show_dialog`.
5. Call `wait` with 50 seconds, then with 130, then with 330. Record whether each returned or
   failed, and after how long.
6. Subagents. List the agent types your Agent tool offers (names only). Spawn this plugin's
   `echo` agent (its type may be `entalkenator-mac-check:echo` or `echo`) with: "Remember the
   word tangerine." Then, if you have a SendMessage tool (load it with ToolSearch if it's
   deferred), send that same agent "What was the word?" and record its reply. If you can't
   message it again, say so.
7. List the skills you have whose names include narrate, entalkenate or setup, and say where
   each comes from: this plugin, the connected folder's `.claude/skills`, or elsewhere.

Report a short table: for each step, what happened. Include, from `where_am_i`: platform,
machine, macOS, python, this file, cwd, the parent process, `env (selected)`, the `mac tools`
and the `enTalkenator on this Mac` flags. Include both folder paths from step 3 (yours and the
Mac's). Include steps 6 and 7. Then say in one line whether the helper runs on the Mac.
