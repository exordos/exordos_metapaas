#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Run with the supervisor-enabled gcl_looper installed (see README)."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

APPLICATION = """
from gcl_looper.services.bjoern_service import BjoernService

VERSION = {version!r}

def application(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [VERSION.encode()]

def build(argv):
    return BjoernService(application, host="127.0.0.1", port=int(argv[0]),
                         bjoern_kwargs={{"reuse_port": True}})
"""

LAUNCHER = """
import sys
from types import SimpleNamespace
from exordos_metapaas.common import api_service
from application import build

if __name__ == "__main__":
    options = SimpleNamespace(workers=int(sys.argv[2]), autoreload=sys.argv[3] == "True",
                              reload_dirs=[])
    api_service.start(build, [sys.argv[1]], options)
"""


@pytest.fixture
def server(tmp_path, tmp_path_factory):
    processes = []

    def launch(workers=1, reload=True, plugin=False):
        source = tmp_path / "application.py"
        application = APPLICATION.format(version="one")
        plugin_source = None
        extra_paths = []
        if plugin:
            plugin_root = tmp_path_factory.mktemp("editable_plugin")
            plugin_source = plugin_root / "editable_plugin.py"
            plugin_source.write_text("VERSION = 'one'\n")
            metadata_root = tmp_path / ".venv"
            distribution = metadata_root / "editable_plugin-1.0.dist-info"
            distribution.mkdir(parents=True)
            (distribution / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: editable-plugin\nVersion: 1.0\n"
            )
            (distribution / "direct_url.json").write_text(
                json.dumps(
                    {"url": plugin_root.as_uri(), "dir_info": {"editable": True}}
                )
            )
            extra_paths = [str(metadata_root), str(plugin_root)]
            application = application.replace(
                "return [VERSION.encode()]",
                "import editable_plugin\n    return [(VERSION + ':' + editable_plugin.VERSION).encode()]",
            )
        source.write_text(application)
        launcher = tmp_path / "launcher.py"
        launcher.write_text(LAUNCHER)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log_path = tmp_path / "server.log"
        environment = os.environ.copy()
        paths = [str(Path(__file__).resolve().parents[3])] + extra_paths
        paths.extend(
            str(Path(path).resolve())
            for path in environment.get("PYTHONPATH", "").split(os.pathsep)
            if path
        )
        environment["PYTHONPATH"] = os.pathsep.join(paths)
        with log_path.open("w") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(launcher),
                    str(port),
                    str(workers),
                    str(reload),
                    str(tmp_path),
                ],
                env=environment,
                stdout=output,
                stderr=output,
            )
        processes.append(process)
        url = f"http://127.0.0.1:{port}/"

        def wait_for(body):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text()
                try:
                    with urlopen(url, timeout=0.5) as response:
                        if response.read().decode() == body:
                            return
                except (URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(0.05)
            pytest.fail(f"Response never became {body!r}: {log_path.read_text()}")

        wait_for("one:one" if plugin else "one")
        return process, source, url, wait_for, log_path, plugin_source

    yield launch
    for process in processes:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pytest.fail("API supervisor did not stop promptly")
        assert process.returncode == 0


@pytest.mark.parametrize("workers", [1, 2])
def test_source_reload_and_failed_generation(server, workers):
    process, source, url, wait_for, log_path, _ = server(workers=workers)
    # Keep the timestamp in the same second and size unchanged (stale .pyc risk).
    timestamp = source.stat().st_mtime_ns
    source.write_text(APPLICATION.format(version="two"))
    os.utime(source, ns=(timestamp + 1000, timestamp + 1000))
    wait_for("two")

    source.write_text("def invalid syntax\n")
    deadline = time.monotonic() + 15
    while "SyntaxError" not in log_path.read_text():
        assert process.poll() is None
        assert time.monotonic() < deadline, log_path.read_text()
        time.sleep(0.05)
    with urlopen(url, timeout=1) as response:
        assert response.read() == b"two"
    source.write_text(APPLICATION.format(version="three"))
    wait_for("three")


def test_production_reloads_only_on_sighup(server):
    process, source, url, wait_for, _, _ = server(reload=False)
    source.write_text(APPLICATION.format(version="changed"))
    with urlopen(url, timeout=1) as response:
        assert response.read() == b"one"
    process.send_signal(signal.SIGHUP)
    wait_for("changed")


def test_autoreload_discovers_lazy_editable_plugin(server):
    _, _, _, wait_for, _, source = server(workers=2, plugin=True)
    timestamp = source.stat().st_mtime_ns
    source.write_text("VERSION = 'two'\n")
    os.utime(source, ns=(timestamp + 1000, timestamp + 1000))
    wait_for("one:two")
