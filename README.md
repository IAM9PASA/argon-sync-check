# argon-sync-check

Compare Roblox Studio with your [Argon](https://argon.wiki) project folder before you connect, and pull Studio's side onto disk when Studio is the source of truth.

It exists for projects where the game is edited in Studio and the repo lags behind. Connecting Argon with the wrong side winning can overwrite real scripts with stale ones and delete Studio-only content (maps, meshes, rigs).

## What it checks

| Report line | Meaning |
|---|---|
| scripts differing | the script exists on both sides with different source (Studio wins) |
| scripts only in Studio | Argon would delete them |
| Studio instances Argon would delete | non-script instances under disk-managed folders |
| on disk but not in Studio | Argon will add them |

`Packages/` (Wally) is skipped: disk wins there.

## Requirements

- Python 3, or the Windows exe from a [release](https://github.com/IAM9PASA/argon-sync-check/releases)
- Argon on your PATH (`install` uses `argon build`)
- Roblox Studio with HTTP requests allowed for the plugin

## Exe

Push a `v*` tag. The release workflow builds `sync_check.exe` with PyInstaller and attaches it to that GitHub release.

```bash
sync_check.exe
sync_check.exe --fix
sync_check.exe install
```

Run it from the Argon project folder, then click **Sync Check** in Studio.

## Install the Studio plugin

```bash
python sync_check.py install
```

This builds `SyncCheck.rbxm` and copies it to `%LOCALAPPDATA%\Roblox\Plugins`. Restart Studio or reload plugins. A **Sync Check** button appears in the Plugins tab. A prebuilt `SyncCheck.rbxm` is included.

## Use

Run from your project folder (the one with `default.project.json`):

```bash
python path/to/sync_check.py              # report only
python path/to/sync_check.py --fix        # also write Studio's scripts to disk
python path/to/sync_check.py --project=dev.project.json
```

Then click **Sync Check** in Studio and read the Output window.

- `CLEAN: safe to connect Argon` means connect.
- Otherwise run with `--fix`, click again, and confirm it says CLEAN.

`--fix` writes Studio's script sources to the matching files, creates files for scripts that exist only in Studio, and adds `keepUnknowns` to the `init.meta.json` of folders holding Studio-only content. Commit first so you can undo.

## Limitations

- Scripts that have children, have names that are invalid as file names, or sit under a script parent are listed as "move it to disk by hand".
- Studio-only non-script instances are not exported, only protected with `keepUnknowns`.
- Property differences (for example Lighting or Workspace settings) are not compared. Check Argon's Diff view for those.
- The server uses port 34873 on localhost.
- `.rbxm` plugin builds and the Plugins path are Windows-oriented.

## Files

- `sync_check.py`: local server and CLI
- `SyncCheck.server.luau`: plugin source
- `plugin.project.json`: Argon project that builds the plugin
- `SyncCheck.rbxm`: built plugin
