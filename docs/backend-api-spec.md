# Backend API Specification

This document defines the backend interface that the frontend and platform backends must follow.

---

## 1. FilteredFSBackend Interface

### 1.1 Contract

```
FilteredFSBackend
  mount(session_id) -> MountResult
  unmount(session_id?) -> void
  update_policy() -> void
  status() -> FilteredFSStatus
  mount_root -> Path | null
```

### 1.2 Protection Model

Protected files are **visible but access-denied**. This is the core behavior every backend must implement.

| Operation | Allowed file | Protected file |
|-----------|-------------|----------------|
| `readdir` (ls, find) | Listed | **Listed** (visible) |
| `getattr` (stat) | Normal metadata | **Zero permissions** (----------) |
| `access` | Returns OK | **Returns EACCES** |
| `open` (read) | OK | **EACCES** (Permission Denied) |
| `open` (write) | OK | **EACCES** |
| `read` | OK | N/A (open blocked) |
| `write` | OK → original updated | N/A (open blocked) |
| `create` | OK | **EACCES** (in protected dir) |
| `mkdir` | OK | **EACCES** (in protected dir) |
| `chmod` | OK | **EACCES** (cannot change) |
| `chown` | OK | **EACCES** |
| `unlink` (rm) | OK | **EACCES** |
| `rmdir` | OK | **EACCES** |
| `rename` | OK | **EACCES** (if source or target protected) |
| `symlink` | OK | **EACCES** (if target protected) |
| `link` (hardlink) | OK | **EACCES** (if target protected) |
| `readlink` | OK | **EACCES** |

### 1.3 What is protected

- Paths matching deny rules in PolicyEngine (e.g., `secrets/**`, `.env`)
- Internal metadata directories (`.ai-ide/`, `.ai_ide_runtime/`)

### 1.4 What is NOT protected

- All other files in the project directory → read/write through to original
- Paths outside the project (e.g., `~/.claude/`, system tools) → normal access

### 1.5 FilteredFSStatus

```json
{
  "backend": "dokan",
  "driver_installed": true,
  "mounted": true,
  "mount_point": "Z:",
  "degraded": false,
  "detail": "Dokan filesystem active at Z:"
}
```

---

## 2. Desktop API (Frontend ↔ Backend)

### 2.1 Filtered FS Status

```
Method: filtered_fs.status
Returns: FilteredFSStatus
```

### 2.2 Policy Toggle (Protect/Unprotect)

```
Method: policy.deny
Params: { rule: string, target?: string }
Returns: { change: { changed, rule, version }, panel: PanelSnapshot }

Method: policy.allow
Params: { rule: string, target?: string }
Returns: (same shape)
```

Effect: updates PolicyEngine → backend applies protection live.

### 2.3 Terminal

```
Method: terminal.create
Params: { transport: "runner" | "host", io_mode: "pty" | "command" }
Returns: { terminal: TerminalInspection }
```

- `transport: "runner"` → managed terminal, CWD = filtered mount, protected files access-denied
- `transport: "host"` → unfiltered terminal, CWD = original project

### 2.4 Editor

```
Method: editor.file    ← open file for viewing
Params: { target: string }

Method: editor.save    ← default (direct save to original)
Params: { target: string, content: string }

Method: editor.stage   ← optional (staged review)
Method: editor.apply
Method: editor.reject
```

Note: editor reads/writes the **original** project, not the filtered view. Protected files can be viewed in the editor since the user controls protection.

### 2.5 Shell Snapshots

```
Method: desktop_shell.snapshot
Params: { target?: string }
Returns: { workspace, terminals, policy, session info }

Method: workspace_panel.snapshot
Params: { target?: string }
Returns: { workspace entries, policy, index state }
```

### 2.6 PTY

```
Method: pty.write
Params: { terminal_id: string, data: string }

Method: pty.resize
Params: { terminal_id: string, cols: number, rows: number }

Event stream: pty.stream
Params: { terminal_id: string }
Yields: { event: "terminal.pty.data", data_b64: string }
        { event: "terminal.pty.close", reason: string }
```

---

## 3. Platform Backend Implementation Guide

### 3.1 Required Operations

Every backend must implement these filesystem operations:

```python
class FilteredPassthrough:
    def readdir(path):
        # List ALL entries including protected ones
        # Protected entries ARE included in the listing

    def getattr(path):
        # For protected paths: return metadata with st_mode = 0
        # For allowed paths: return real metadata

    def open(path, flags):
        # For protected paths: raise EACCES
        # For allowed paths: open original file

    def read(path, size, offset, fh):
        # Only called for allowed paths (open already blocked protected)

    def write(path, data, offset, fh):
        # Only called for allowed paths

    def create(path, mode):
        # For paths in protected directories: raise EACCES
        # For allowed paths: create in original directory

    def chmod(path, mode):
        # For protected paths: raise EACCES (cannot bypass protection)
        # For allowed paths: pass through

    def unlink(path):
        # For protected paths: raise EACCES
        # For allowed paths: delete from original

    def rename(old, new):
        # If either path is protected: raise EACCES
        # Otherwise: rename in original
```

### 3.2 Protection Check Logic

```python
def is_protected(path: Path) -> bool:
    # 1. Is it internal metadata? (.ai-ide/, .ai_ide_runtime/)
    if is_internal_path(project_root, path):
        return True
    # 2. Does it match a PolicyEngine deny rule?
    if not policy_engine.is_allowed(path):
        return True
    return False
```

### 3.3 Contract Tests

Every backend must pass:

| Test | Assertion |
|------|-----------|
| readdir root | Protected entries ARE listed |
| getattr protected | Returns metadata with zero permissions |
| open protected | Returns EACCES |
| chmod protected | Returns EACCES |
| unlink protected | Returns EACCES |
| read allowed | Returns correct content from original |
| write allowed | Original file updated immediately |
| create new file | Original directory gains new file |
| policy update | Newly protected files immediately access-denied |

---

## 4. Lifecycle

### 4.1 Policy Change

```
1. User clicks protect on "secrets/" in file tree
2. Frontend calls: policy.deny({ rule: "secrets/**" })
3. Backend:
   a. PolicyEngine.add_deny_rule("secrets/**")
   b. FilteredFSBackend.update_policy()
   c. In filtered view: secrets/ still visible, but read → EACCES
4. Returns updated panel snapshot
5. Existing terminals: secrets/ files immediately become access-denied
   - No terminal restart
   - No session rotation
   - No CLI notification
   - Simply the next filesystem access sees the new protection
```

### 4.2 Terminal Creation

```
1. Frontend calls: terminal.create({ transport: "runner", io_mode: "pty" })
2. Backend:
   a. Get mount_root (e.g., Z:\)
   b. Spawn PTY with CWD = mount_root
3. In that terminal:
   $ ls           → sees secrets/ (visible)
   $ cat secrets/token.txt  → Permission Denied
   $ cat src/main.py        → shows content (allowed)
```

---

## 5. Lifecycle

### 5.1 App Startup

```
1. Create PolicyEngine (load protect rules from .ai-ide/policy.json)
2. Create FilteredFSBackend (auto-select: Dokan > FUSE > macFUSE > mirror fallback)
3. Log backend status
4. If degraded: show warning in UI
5. Mount filtered view (on first terminal create, or eagerly)
```

### 5.2 App Shutdown

```
1. Destroy all PTY sessions
2. FilteredFSBackend.unmount()
3. Cleanup temporary mount points
```

---

## 6. Error Handling

| Situation | Backend behavior | Frontend behavior |
|-----------|------------------|-------------------|
| Driver not installed | `degraded = true` | Show install prompt |
| Mount failed | `mounted = false` | Show error banner |
| Read protected file | EACCES | Terminal shows "Permission denied" |
| chmod protected file | EACCES | Terminal shows "Permission denied" |
| Policy update on unmounted FS | Rules stored, applied on next mount | Normal |
