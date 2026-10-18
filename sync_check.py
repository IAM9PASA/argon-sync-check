"""
sync_check: compare Studio against disk before connecting Argon, and pull Studio's work onto disk.

Studio is where the game is edited, so Studio wins for scripts. Packages/ is Wally's, so disk wins there
and it is never checked.

    python tools/sync_check/sync_check.py install    build the Studio plugin (.rbxm) and copy it into Roblox's Plugins folder
    python tools/sync_check/sync_check.py            report only
    python tools/sync_check/sync_check.py --fix      report, then write Studio's scripts to disk and add
                                                     keepUnknowns where Argon would delete Studio content

then click "Sync Check" in Studio's Plugins tab. the server keeps running so you can click again.
"""

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# the project to check: --project path/to/x.project.json, else default.project.json in the current folder
PROJECT = os.path.abspath(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--project=")), "default.project.json"))
ROOT = os.path.dirname(PROJECT)
PORT = 34873
SEP = "/"  # instance names can hold dots ("ryanlua_satchel@1.4.1"), never slashes
SKIP_PATHS = {"ReplicatedStorage/Packages"}  # wally's, disk wins
SCRIPT_SUFFIX = {"Script": ".server.luau", "LocalScript": ".client.luau", "ModuleScript": ".luau"}


# project walking -------------------------


def script_class(name: str):
    for suffix, cls in ((".server.luau", "Script"), (".server.lua", "Script"),
                        (".client.luau", "LocalScript"), (".client.lua", "LocalScript"),
                        (".luau", "ModuleScript"), (".lua", "ModuleScript")):
        if name.endswith(suffix):
            return name[: -len(suffix)], cls
    return None, None


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def meta_keep(meta: dict) -> bool:
    return bool(meta.get("keepUnknowns") or meta.get("ignoreUnknownInstances"))


def add(index, path, **entry):
    entry.setdefault("keep", False)
    index[path] = entry


def walk_model(index, path, node, file):
    # *.model.json children are whatever the json lists; studio extras under them get removed
    add(index, path, kind="model", file=file)
    for child in node.get("children", node.get("Children", [])):
        walk_model(index, path + SEP + child.get("name", child.get("Name")), child, file)


def walk_dir(index, path, folder, keep=False):
    entries = sorted(os.listdir(folder))
    init = next((e for e in entries if script_class(e)[0] == "init"), None)
    meta_path = os.path.join(folder, "init.meta.json")
    meta = read_json(meta_path) if os.path.exists(meta_path) else {}
    entry = {"kind": "dir", "dir": folder, "keep": keep or meta_keep(meta)}
    if init:
        entry.update(kind="script", file=os.path.join(folder, init), cls=script_class(init)[1])
    index[path] = entry

    for e in entries:
        full = os.path.join(folder, e)
        if e.startswith("init.") or e.endswith(".meta.json"):
            continue
        if os.path.isdir(full):
            if os.path.exists(os.path.join(full, "default.project.json")):
                add(index, path + SEP + e, kind="opaque", keep=True)
            else:
                walk_dir(index, path + SEP + e, full)
            continue
        name, cls = script_class(e)
        if cls:
            sib_meta = os.path.join(folder, name + ".meta.json")
            keep_file = meta_keep(read_json(sib_meta)) if os.path.exists(sib_meta) else False
            add(index, path + SEP + name, kind="script", file=full, cls=cls, keep=keep_file)
        elif e.endswith(".model.json"):
            walk_model(index, path + SEP + e[: -len(".model.json")], read_json(full), full)
        elif e.endswith((".rbxm", ".rbxmx")):
            add(index, path + SEP + e.rsplit(".", 1)[0], kind="opaque", keep=True)
        elif "." in e:
            add(index, path + SEP + e.rsplit(".", 1)[0], kind="data", file=full)


def walk_node(index, path, node):
    # nodes without $path keep unknown children unless told otherwise, $path nodes don't
    flag = next((k for k in ("$keepUnknowns", "$ignoreUnknownInstances") if k in node), None)
    keep = bool(node[flag]) if flag else "$path" not in node
    if path in SKIP_PATHS:
        add(index, path, kind="opaque", keep=True)
        return
    if "$path" in node:
        target = os.path.join(ROOT, node["$path"])
        if os.path.isdir(target):
            walk_dir(index, path, target, keep)
        elif os.path.exists(target):
            name, cls = script_class(os.path.basename(target))
            add(index, path, kind="script" if cls else "data", file=target, cls=cls, keep=keep)
        else:
            add(index, path, kind="opaque", keep=True)
    elif path:
        add(index, path, kind="node", keep=keep)
    for key, child in node.items():
        if not key.startswith("$"):
            walk_node(index, path + SEP + key if path else key, child)


def build_index() -> dict:
    index = {}
    walk_node(index, "", read_json(PROJECT)["tree"])
    return index


# report and fix -------------------------


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def write_source(file: str, source: str):
    with open(file, "w", encoding="utf-8", newline="\n") as f:
        f.write(norm(source) + "\n")


def add_keep(entry: dict, path: str) -> str:
    if entry.get("dir"):
        meta = os.path.join(entry["dir"], "init.meta.json")
    elif entry["kind"] == "script":
        name, _ = script_class(os.path.basename(entry["file"]))
        meta = os.path.join(os.path.dirname(entry["file"]), name + ".meta.json")
    else:
        return f"can't mark {path} ({entry['kind']}), add the instances to its file by hand"
    data = read_json(meta) if os.path.exists(meta) else {}
    data["keepUnknowns"] = True
    with open(meta, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    return f"keepUnknowns -> {rel(meta)}"


def handle_report(index: dict, report: dict, fix: bool) -> str:
    lines = []
    changed, missing, new_scripts, removals = [], report["missing"], [], report["removals"]

    for path, source in report["sources"].items():
        file = index[path]["file"]
        with open(file, encoding="utf-8", errors="replace") as f:
            if norm(f.read()) != norm(source):
                changed.append((path, file, source))

    # an empty luau table encodes as [] rather than {}
    new_scripts = list((report["new_scripts"] or {}).items())

    lines.append(f"scripts differing (Studio wins): {len(changed)}")
    for path, file, source in changed:
        lines.append(f"  {rel(file)}")
        if fix:
            write_source(file, source)

    lines.append(f"scripts only in Studio, Argon would delete: {len(new_scripts)}")
    for path, info in new_scripts:
        parent = index[path.rsplit(SEP, 1)[0]]
        name = path.rsplit(SEP, 1)[1]
        where = parent.get("dir")
        bad = any(c in name for c in '<>:"/\\|?*') or name.endswith((" ", "."))
        if not where or info["children"] or bad:
            why = "has children" if info["children"] else "bad file name" if bad else "parent is a file"
            lines.append(f"  {path}  ({why}, move it to disk by hand)")
            continue
        file = os.path.join(where, name + SCRIPT_SUFFIX[info["class"]])
        lines.append(f"  {rel(file)}")
        if fix:
            write_source(file, info["source"])

    parents = sorted({p.rsplit(SEP, 1)[0] for p, _ in removals})
    lines.append(f"Studio instances Argon would delete: {len(removals)} under {len(parents)} parents")
    for p in parents:
        count = sum(1 for r, _ in removals if r.rsplit(SEP, 1)[0] == p)
        msg = add_keep(index[p], p) if fix else "needs keepUnknowns"
        lines.append(f"  {p} ({count})  {msg}")

    lines.append(f"on disk but not in Studio, Argon will add: {len(missing)}")
    for p in missing[:30]:
        lines.append(f"  {p}")
    if len(missing) > 30:
        lines.append(f"  ... {len(missing) - 30} more")

    clean = not changed and not new_scripts and not removals
    lines.append("CLEAN: safe to connect Argon" if clean else ("fixed, click Sync Check again to confirm" if fix else "run with --fix to pull Studio's side onto disk"))
    return "\n".join(lines)


# server -------------------------


class Handler(BaseHTTPRequestHandler):
    fix = False
    index = {}
    sources = {}  # roblox caps a post at 1 MB, so sources arrive in /part chunks before the report

    def log_message(self, *_):
        pass

    def send(self, body: str, kind="text/plain"):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Handler.index = build_index()
        Handler.sources = {}
        slim = {p: [e["kind"], e["keep"]] for p, e in Handler.index.items()}
        self.send(json.dumps({"sep": SEP, "index": slim}), "application/json")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/part":
            Handler.sources.update(body)
            self.send("ok")
            return
        body["sources"] = Handler.sources
        text = handle_report(Handler.index, body, Handler.fix)
        print("\n" + text + "\n", flush=True)
        self.send(text)


def install_plugin():
    here = os.path.dirname(os.path.abspath(__file__))
    plugins = os.path.join(os.environ["LOCALAPPDATA"], "Roblox", "Plugins")
    os.makedirs(plugins, exist_ok=True)
    # rebuild so the .rbxm always matches SyncCheck.server.luau
    subprocess.run(["argon", "build", "plugin.project.json", "-o", "SyncCheck.rbxm"], cwd=here, check=True)
    shutil.copy(os.path.join(here, "SyncCheck.rbxm"), os.path.join(plugins, "SyncCheck.rbxm"))
    old = os.path.join(plugins, "SyncCheck.lua")
    if os.path.exists(old):
        os.remove(old)
    print(f"installed to {plugins}, restart Studio (or reload plugins) to see the Sync Check button")


if __name__ == "__main__":
    if "install" in sys.argv[1:]:
        install_plugin()
        sys.exit()
    Handler.fix = "--fix" in sys.argv[1:]
    print(f"sync_check on 127.0.0.1:{PORT} ({'fix' if Handler.fix else 'report only'}), click Sync Check in Studio")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
