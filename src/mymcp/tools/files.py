import asyncio
import glob as _glob_module
import os
import shutil

from mymcp import config


def check_protected_path(file_path: str) -> str | None:
    """Returns error message if path is protected, None if allowed."""
    real = os.path.realpath(file_path)
    for protected in config.PROTECTED_PATHS:
        protected_real = os.path.realpath(protected)
        if real == protected_real or real.startswith(protected_real + os.sep):
            return f"Access denied: path is within protected directory {protected}"
    return None


def _filter_protected(paths: list[str]) -> list[str]:
    """Filter out paths that fall within protected directories."""
    return [p for p in paths if check_protected_path(p) is None]


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


async def read_file(
    file_path: str,
    offset: int = 1,
    limit: int = config.READ_FILE_DEFAULT_LIMIT,
) -> dict:
    limit = min(max(1, limit), config.READ_FILE_MAX_LIMIT)
    offset = max(1, offset)

    err = check_protected_path(file_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}

    try:
        with open(file_path, "rb") as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"File not found: {file_path}",
            "suggestion": "Check the file path",
        }
    except IsADirectoryError:
        return {
            "success": False,
            "error": "IsADirectoryError",
            "message": f"{file_path} is a directory",
            "suggestion": "Use glob to list directory contents",
        }
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check file read permissions",
        }

    total_lines = len(raw_lines)
    selected = raw_lines[offset - 1 : offset - 1 + limit]
    output_lines = []

    for i, raw_line in enumerate(selected, start=offset):
        line = raw_line.rstrip(b"\n").rstrip(b"\r")
        if len(line) > config.READ_FILE_MAX_LINE_BYTES:
            line = line[: config.READ_FILE_MAX_LINE_BYTES]
            decoded = line.decode("utf-8", errors="replace") + " [LINE TRUNCATED]"
        else:
            decoded = line.decode("utf-8", errors="replace")
        output_lines.append(f"{i:4}\t{decoded}")

    return {
        "content": "\n".join(output_lines),
        "total_lines": total_lines,
        "truncated": (offset - 1 + limit) < total_lines,
    }


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


async def write_file(file_path: str, content: str) -> dict:
    err = check_protected_path(file_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > config.WRITE_FILE_MAX_BYTES:
        return {
            "success": False,
            "error": "FileTooLarge",
            "message": (
                f"Content is {len(content_bytes)} bytes, "
                f"max is {config.WRITE_FILE_MAX_BYTES} (10MB)"
            ),
            "suggestion": "Use the /files/upload endpoint for large files",
        }
    try:
        parent = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(parent, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "bytes_written": len(content_bytes)}
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check write permissions",
        }


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    err = check_protected_path(file_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}

    if len(old_string.encode("utf-8")) > config.EDIT_STRING_MAX_BYTES:
        return {
            "success": False,
            "error": "FileTooLarge",
            "message": "old_string exceeds 1MB limit",
        }
    if len(new_string.encode("utf-8")) > config.EDIT_STRING_MAX_BYTES:
        return {
            "success": False,
            "error": "FileTooLarge",
            "message": "new_string exceeds 1MB limit",
        }

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"File not found: {file_path}",
        }
    except PermissionError as e:
        return {"success": False, "error": "PermissionError", "message": str(e)}

    count = content.count(old_string)
    if count == 0:
        return {
            "success": False,
            "error": "StringNotFound",
            "message": "old_string not found in file",
        }
    if count > 1 and not replace_all:
        return {
            "success": False,
            "error": "AmbiguousMatch",
            "message": (
                f"old_string appears {count} times. "
                "Set replace_all=true to replace all occurrences."
            ),
        }

    if replace_all:
        new_content = content.replace(old_string, new_string)
        replacements = count
    else:
        new_content = content.replace(old_string, new_string, 1)
        replacements = 1

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"success": True, "replacements": replacements}
    except PermissionError as e:
        return {"success": False, "error": "PermissionError", "message": str(e)}


# ---------------------------------------------------------------------------
# glob_files
# ---------------------------------------------------------------------------


async def glob_files(pattern: str, path: str = "/") -> dict:
    try:
        base = os.path.abspath(path)
        full_pattern = os.path.join(base, pattern)
        matches = _glob_module.glob(full_pattern, recursive=True)
        matches.sort(
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True,
        )
        matches = _filter_protected(matches)
        truncated = len(matches) > config.GLOB_MAX_RESULTS
        return {
            "files": matches[: config.GLOB_MAX_RESULTS],
            "count": len(matches),
            "truncated": truncated,
        }
    except Exception as e:
        return {"success": False, "error": type(e).__name__, "message": str(e)}


# ---------------------------------------------------------------------------
# grep_files
# ---------------------------------------------------------------------------


async def grep_files(
    pattern: str,
    path: str = "/",
    glob: str | None = None,
    output_mode: str = "content",
    context_lines: int = 0,
    max_results: int = config.GREP_DEFAULT_MAX_RESULTS,
    case_insensitive: bool = False,
) -> dict:
    max_results = min(max(1, max_results), config.GREP_MAX_RESULTS)
    if shutil.which("rg"):
        return await _grep_rg(
            pattern, path, glob, output_mode, context_lines, max_results, case_insensitive
        )
    return await _grep_python(
        pattern, path, glob, output_mode, context_lines, max_results, case_insensitive
    )


async def _grep_rg(
    pattern, path, glob_pattern, output_mode, context_lines, max_results, case_insensitive
) -> dict:
    cmd = ["rg", "--no-heading", "-n"]
    if case_insensitive:
        cmd.append("-i")
    if context_lines:
        cmd.extend(["-C", str(context_lines)])
    if glob_pattern:
        cmd.extend(["--glob", glob_pattern])
    if output_mode == "files":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("--count")
    cmd.extend([pattern, path])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        lines = stdout.decode("utf-8", errors="replace").splitlines()
    except (asyncio.TimeoutError, TimeoutError):  # noqa: UP041
        return {"success": False, "error": "TimeoutError", "message": "grep timed out after 60s"}

    filtered = []
    for line in lines:
        parts = line.split(":", 1)
        if parts and check_protected_path(parts[0]) is None:
            filtered.append(line)
    lines = filtered

    total = len(lines)
    truncated = total > max_results
    result_str = "\n".join(lines[:max_results])
    if truncated:
        result_str += f"\n[TRUNCATED: {total - max_results} more matches not shown]"
    return {"results": result_str, "match_count": total}


async def _grep_python(
    pattern, path, glob_pattern, output_mode, context_lines, max_results, case_insensitive
) -> dict:
    import fnmatch
    import re

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {"success": False, "error": "InvalidRegex", "message": str(e)}

    if os.path.isfile(path):
        files_to_search = [path]
    else:
        files_to_search = []
        for root, _dirs, files in os.walk(path):
            for fname in files:
                if glob_pattern and not fnmatch.fnmatch(fname, glob_pattern):
                    continue
                files_to_search.append(os.path.join(root, fname))

    matches: list[str] = []
    for fpath in files_to_search:
        if check_protected_path(fpath) is not None:
            continue
        if len(matches) >= max_results and output_mode == "content":
            break
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except (PermissionError, IsADirectoryError, OSError):
            continue

        if output_mode == "files":
            if any(regex.search(line) for line in lines):
                matches.append(fpath)
        elif output_mode == "count":
            c = sum(1 for line in lines if regex.search(line))
            if c:
                matches.append(f"{fpath}: {c}")
        else:
            for lineno, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(f"{fpath}:{lineno}:{line.rstrip()}")

    total = len(matches)
    truncated = total > max_results
    result_str = "\n".join(matches[:max_results])
    if truncated:
        result_str += f"\n[TRUNCATED: {total - max_results} more matches not shown]"
    return {"results": result_str, "match_count": total}
