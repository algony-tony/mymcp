import asyncio
import config


async def run_bash_execute(
    command: str,
    timeout: int = 30,
    working_dir: str = "/",
    max_output_bytes: int = config.BASH_MAX_OUTPUT_BYTES,
) -> dict:
    timeout = min(max(1, timeout), 600)
    max_output_bytes = min(max(1, max_output_bytes), config.BASH_MAX_OUTPUT_BYTES_HARD)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"Working directory not found: {working_dir}",
            "suggestion": "Check that the working_dir path exists",
        }
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check directory permissions",
        }

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=float(timeout)
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "exit_code": -1,
            "timed_out": True,
        }

    def _truncate(data: bytes, limit: int) -> str:
        if len(data) <= limit:
            return data.decode("utf-8", errors="replace")
        shown = data[:limit].decode("utf-8", errors="replace")
        return f"{shown}\n[TRUNCATED: total {len(data)} bytes, showing first {limit} bytes]"

    return {
        "stdout": _truncate(stdout_bytes, max_output_bytes),
        "stderr": _truncate(stderr_bytes, max_output_bytes),
        "exit_code": proc.returncode,
        "timed_out": False,
    }
