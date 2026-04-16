"""Load testing with Locust.

Usage:
    1. Start the MCP server: python3 main.py
    2. Set environment variables:
       export MCP_ADMIN_TOKEN=<your-admin-token>
       export MCP_TEST_TOKEN=<a-valid-rw-token>
    3. Run locust:
       locust -f tests/loadtest/locustfile.py --host http://localhost:8765
    4. Open http://localhost:8089 in your browser to configure and start the test.

    For headless mode:
       locust -f tests/loadtest/locustfile.py --host http://localhost:8765 \
              --headless -u 10 -r 2 --run-time 60s
"""

import json
import os
import tempfile

from locust import HttpUser, task, between


TEST_TOKEN = os.environ.get("MCP_TEST_TOKEN", "")
ADMIN_TOKEN = os.environ.get("MCP_ADMIN_TOKEN", "")


def _mcp_headers():
    return {
        "Authorization": f"Bearer {TEST_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _mcp_request(tool_name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _mcp_list_tools() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }


class HealthCheckUser(HttpUser):
    """Baseline: high-frequency health checks."""

    weight = 1
    wait_time = between(0.1, 0.5)

    @task
    def health(self):
        self.client.get("/health")


class ReadUser(HttpUser):
    """Read-heavy workload: read_file, glob, grep."""

    weight = 7
    wait_time = between(0.5, 2)

    def on_start(self):
        self._tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        )
        self._tmpfile.write("\n".join(f"line {i}" for i in range(100)))
        self._tmpfile.close()
        self._tmpdir = os.path.dirname(self._tmpfile.name)

    def on_stop(self):
        try:
            os.unlink(self._tmpfile.name)
        except OSError:
            pass

    @task(5)
    def read_file(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("read_file", {"file_path": self._tmpfile.name}),
            headers=_mcp_headers(),
            name="/mcp [read_file]",
        )

    @task(2)
    def glob_files(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("glob", {"pattern": "*.txt", "path": self._tmpdir}),
            headers=_mcp_headers(),
            name="/mcp [glob]",
        )

    @task(2)
    def grep_files(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("grep", {"pattern": "line", "path": self._tmpdir}),
            headers=_mcp_headers(),
            name="/mcp [grep]",
        )

    @task(1)
    def list_tools(self):
        self.client.post(
            "/mcp",
            json=_mcp_list_tools(),
            headers=_mcp_headers(),
            name="/mcp [list_tools]",
        )


class WriteUser(HttpUser):
    """Write workload: write_file, edit_file, bash_execute."""

    weight = 3
    wait_time = between(1, 3)

    def on_start(self):
        self._tmpdir = tempfile.mkdtemp()
        self._counter = 0

    @task(3)
    def write_file(self):
        self._counter += 1
        path = os.path.join(self._tmpdir, f"write_{self._counter}.txt")
        self.client.post(
            "/mcp",
            json=_mcp_request("write_file", {
                "file_path": path,
                "content": f"content written by locust iteration {self._counter}",
            }),
            headers=_mcp_headers(),
            name="/mcp [write_file]",
        )

    @task(2)
    def edit_file(self):
        path = os.path.join(self._tmpdir, "editable.txt")
        self.client.post(
            "/mcp",
            json=_mcp_request("write_file", {
                "file_path": path,
                "content": "original_value in the file",
            }),
            headers=_mcp_headers(),
            name="/mcp [write_file setup]",
        )
        self.client.post(
            "/mcp",
            json=_mcp_request("edit_file", {
                "file_path": path,
                "old_string": "original_value",
                "new_string": "edited_value",
            }),
            headers=_mcp_headers(),
            name="/mcp [edit_file]",
        )

    @task(1)
    def bash_execute(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("bash_execute", {"command": "echo locust_test"}),
            headers=_mcp_headers(),
            name="/mcp [bash_execute]",
        )
