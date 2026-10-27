import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services import product_import as service

URL = "https://shop.example/product"
PRODUCT = b'<h1>Shirt</h1><img itemprop="image" src="/shirt.png">'


def child_pids():
    children = set()
    for task in Path("/proc/self/task").iterdir():
        try:
            content = (task / "children").read_text()
        except FileNotFoundError:
            continue
        children.update(int(pid) for pid in content.split())
    return children


@pytest.fixture(autouse=True)
def isolated_parse_slots(monkeypatch):
    monkeypatch.setattr(service, "_parse_slots", asyncio.Semaphore(2))


async def started_child(queue, request):
    started = asyncio.create_task(queue.get())
    try:
        done, _pending = await asyncio.wait([started, request], return_when=asyncio.FIRST_COMPLETED)
        if request in done and started not in done:
            await request
            pytest.fail("Parsing completed without a child process")
        return await started
    finally:
        started.cancel()


@pytest.fixture
def parser_peers(monkeypatch):
    original = asyncio.create_subprocess_exec
    peers = []
    started = asyncio.Queue()
    state = {"block": True}

    async def launch(*command, **options):
        if state["block"]:
            process = await original(
                sys.executable, "-c", "import signal; signal.pause()", **options
            )
            peers.append(process)
            started.put_nowait(process)
            return process
        return await original(*command, **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    return peers, started, state


async def test_deadline_kills_and_reaps_child_before_api_422(
    client, auth_headers, parser_peers, monkeypatch
):
    peers, started, _state = parser_peers
    baseline = child_pids()
    loop = asyncio.get_running_loop()
    actual_time = loop.time
    elapsed = 0
    request = None
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        monkeypatch.setattr(loop, "time", lambda: actual_time() + elapsed)
        try:
            request = asyncio.create_task(
                client.post("/api/v1/considering/extract", headers=auth_headers, json={"url": URL})
            )
            process = await started_child(started, request)
            assert process.returncode is None
            elapsed = 6
            loop.call_soon(lambda: None)
            response = await request
            assert response.status_code == 422
            assert process.returncode == -signal.SIGKILL
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
            assert child_pids() == baseline
        finally:
            monkeypatch.setattr(loop, "time", actual_time)
            if request and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            for process in peers:
                if process.returncode is None:
                    process.kill()
                await process.wait()


async def test_two_children_reject_third_without_queue_and_release_capacity(
    client, auth_headers, parser_peers
):
    peers, started, state = parser_peers
    baseline = child_pids()
    requests = []
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        try:
            for _slot in range(2):
                request = asyncio.create_task(
                    client.post(
                        "/api/v1/considering/extract", headers=auth_headers, json={"url": URL}
                    )
                )
                requests.append(request)
                await started_child(started, request)
            response = await client.post(
                "/api/v1/considering/extract", headers=auth_headers, json={"url": URL}
            )
            assert response.status_code == 429
            assert len(peers) == 2 and all(process.returncode is None for process in peers)
        finally:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            for process in peers:
                if process.returncode is None:
                    process.kill()
                await process.wait()
        assert all(process.returncode == -signal.SIGKILL for process in peers)
        state["block"] = False
        response = await client.post(
            "/api/v1/considering/extract", headers=auth_headers, json={"url": URL}
        )
        assert response.status_code == 200 and response.json()["name"] == "Shirt"
        assert child_pids() == baseline


async def test_child_resource_limits_are_real_and_set_before_parsing(tmp_path, monkeypatch):
    record = tmp_path / "limits.json"
    original = asyncio.create_subprocess_exec
    code = f"""import json,resource,runpy
original=resource.setrlimit
def observe(kind,limits):
 original(kind,limits)
 if kind==resource.RLIMIT_AS:
  with open({str(record)!r},'w') as target:json.dump({{'cpu':resource.getrlimit(resource.RLIMIT_CPU),'memory':resource.getrlimit(resource.RLIMIT_AS)}},target)
resource.setrlimit=observe
runpy.run_module('app.product_parser',run_name='__main__')
"""

    async def launch(*command, **options):
        return await original(sys.executable, "-c", code, URL, **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        assert (await service.fetch_product(URL))["name"] == "Shirt"
    assert record.exists(), "Parser never entered a resource-limited child"
    limits = json.loads(record.read_text())
    assert limits["cpu"] == [5, 5]
    assert limits["memory"] == [384 * 1024 * 1024, 384 * 1024 * 1024]


async def test_nonzero_child_exit_is_explicit_422_and_reaped(client, auth_headers, monkeypatch):
    original = asyncio.create_subprocess_exec
    peers = []

    async def launch(*command, **options):
        process = await original(sys.executable, "-c", "raise SystemExit(2)", **options)
        peers.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        response = await client.post(
            "/api/v1/considering/extract", headers=auth_headers, json={"url": URL}
        )
    assert response.status_code == 422
    assert len(peers) == 1 and peers[0].returncode == 2
    with pytest.raises(ChildProcessError):
        os.waitpid(peers[0].pid, os.WNOHANG)


def test_nested_select_options_are_processed_once(monkeypatch):
    visited = 0
    original = service.Element.text

    def counted(element, *args, **kwargs):
        nonlocal visited
        visited += 1
        assert visited < 200000, "Nested selects revisited subtrees"
        return original(element, *args, **kwargs)

    monkeypatch.setattr(service.Element, "text", counted)
    html = (
        PRODUCT.decode()
        + '<select name="sizecolor">' * 140
        + '<option selected value="L">L</option>'
        + '<option value="M">M</option>' * 19000
        + "</select>" * 140
    )
    capture = service.extract_product(html, URL)
    assert capture["sizes"] == ["L", "M"]
    assert capture["colors"] == ["L", "M"]
    assert capture["selected_size"] == "L" and visited < 200000


@pytest.mark.parametrize(
    "html",
    [
        '<main><h1>Generic heading</h1><meta itemprop="name" content="Actual product"><img itemprop="image" src="/image.png"></main>',
        '<head><meta itemprop="name" content="Actual product"></head><main><h1>Generic heading</h1><img itemprop="image" src="/image.png"></main>',
    ],
)
def test_meta_itemprop_name_reads_content_before_h1(html):
    assert service.extract_product(html, URL)["name"] == "Actual product"


def test_raw_and_visible_cache_lengths_remain_bounded(monkeypatch):
    original = service.Element.cache_text
    caches = []

    def observed(element):
        original(element)
        caches.append((element.tag, len(element.raw_text), len(element.visible_text)))

    monkeypatch.setattr(service.Element, "cache_text", observed)
    html = (
        PRODUCT.decode()
        + "<p>"
        + "x" * 25000
        + '</p><script type="application/ld+json">'
        + json.dumps({"padding": "x" * 210000})
        + "</script>"
    )
    assert service.extract_product(html, URL)["name"] == "Shirt"
    assert caches
    for tag, raw, visible in caches:
        assert raw <= (200001 if tag == "script" else 6000)
        assert visible <= 6000
    assert ("script", 200001, 0) in caches


async def test_deadline_during_launch_still_kills_and_reaps(client, auth_headers, monkeypatch):
    original = asyncio.create_subprocess_exec
    started = asyncio.Queue()
    release_launch = asyncio.Event()
    peers = []
    baseline = child_pids()
    loop = asyncio.get_running_loop()
    actual_time = loop.time
    elapsed = 0
    request = None

    async def launch(*command, **options):
        process = await original(sys.executable, "-c", "import signal; signal.pause()", **options)
        peers.append(process)
        started.put_nowait(process)
        await release_launch.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        monkeypatch.setattr(loop, "time", lambda: actual_time() + elapsed)
        try:
            request = asyncio.create_task(
                client.post("/api/v1/considering/extract", headers=auth_headers, json={"url": URL})
            )
            process = await started_child(started, request)
            elapsed = 6
            loop.call_later(0, release_launch.set)
            response = await request
            assert response.status_code == 422
            assert process.returncode == -signal.SIGKILL
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
            assert child_pids() == baseline
        finally:
            monkeypatch.setattr(loop, "time", actual_time)
            release_launch.set()
            if request and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            for process in peers:
                if process.returncode is None:
                    process.kill()
                await process.wait()


def test_child_inventory_handles_a_thread_exiting_during_scan(monkeypatch):
    original_iterdir = Path.iterdir
    original_read_text = Path.read_text
    exited = Path("/proc/self/task/exited")
    current = Path("/proc/self/task/current")

    def listed(path):
        if path == Path("/proc/self/task"):
            return iter([exited, current])
        return original_iterdir(path)

    def read(path, *args, **kwargs):
        if path == exited / "children":
            raise FileNotFoundError(path)
        if path == current / "children":
            return "123 456"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", listed)
    monkeypatch.setattr(Path, "read_text", read)
    assert child_pids() == {123, 456}


def test_child_inventory_reports_unreadable_children(monkeypatch):
    def denied(path, *args, **kwargs):
        raise PermissionError(path)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        child_pids()
