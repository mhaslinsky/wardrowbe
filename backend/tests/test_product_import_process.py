import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import product_parser as parser
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
    state = {
        "block": True,
        "program": "import signal; signal.pause()",
        "launch_entered": asyncio.Event(),
        "creation_gate": None,
        "launch_gate": None,
        "reap_gate": None,
        "reap_entered": asyncio.Queue(),
        "reaped": asyncio.Queue(),
    }

    async def launch(*command, **options):
        state["launch_entered"].set()
        if state["creation_gate"] is not None:
            await state["creation_gate"].wait()
        if state["block"]:
            process = await original(sys.executable, "-c", state["program"], URL, **options)
        else:
            process = await original(*command, **options)
        peers.append(process)
        kill = process.kill
        wait = process.wait
        killed = False

        def controlled_kill():
            nonlocal killed
            killed = True
            if state["reap_gate"] is None:
                kill()

        async def controlled_wait():
            state["reap_entered"].put_nowait(process)
            if state["reap_gate"] is not None:
                await state["reap_gate"].wait()
                if killed and process.returncode is None:
                    kill()
            result = await wait()
            state["reaped"].put_nowait(process)
            return result

        process.kill = controlled_kill
        process.wait = controlled_wait
        started.put_nowait(process)
        if state["launch_gate"] is not None:
            await state["launch_gate"].wait()
        return process

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
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            assert all(process.returncode == -signal.SIGKILL for process in peers)
            for process in peers:
                with pytest.raises(ChildProcessError):
                    os.waitpid(process.pid, os.WNOHANG)
            state["block"] = False
            response = await client.post(
                "/api/v1/considering/extract", headers=auth_headers, json={"url": URL}
            )
            assert response.status_code == 200 and response.json()["name"] == "Shirt"
            assert child_pids() == baseline
        finally:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            for process in peers:
                if process.returncode is None:
                    process.kill()
                await process.wait()


async def test_child_resource_limits_are_real_and_set_before_parsing(tmp_path, parser_peers):
    record = tmp_path / "limits.json"
    _peers, _started, state = parser_peers
    code = f"""import json,resource,runpy
original=resource.setrlimit
def observe(kind,limits):
 original(kind,limits)
 if kind==resource.RLIMIT_AS:
  with open({str(record)!r},'w') as target:json.dump({{'cpu':resource.getrlimit(resource.RLIMIT_CPU),'memory':resource.getrlimit(resource.RLIMIT_AS)}},target)
resource.setrlimit=observe
runpy.run_module('app.product_parser',run_name='__main__')
"""

    state["program"] = code
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        assert (await service.fetch_product(URL))["name"] == "Shirt"
    assert record.exists(), "Parser never entered a resource-limited child"
    limits = json.loads(record.read_text())
    assert limits["cpu"] == [5, 5]
    assert limits["memory"] == [384 * 1024 * 1024, 384 * 1024 * 1024]


async def test_nonzero_child_exit_is_explicit_422_and_reaped(client, auth_headers, parser_peers):
    peers, _started, state = parser_peers
    state["program"] = "raise SystemExit(2)"
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
    original = parser.Element.text

    def counted(element, *args, **kwargs):
        nonlocal visited
        visited += 1
        assert visited < 200000, "Nested selects revisited subtrees"
        return original(element, *args, **kwargs)

    monkeypatch.setattr(parser.Element, "text", counted)
    html = (
        PRODUCT.decode()
        + '<select name="sizecolor">' * 140
        + '<option selected value="L">L</option>'
        + '<option value="M">M</option>' * 19000
        + "</select>" * 140
    )
    capture = parser.extract_product(html, URL)
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
    assert parser.extract_product(html, URL)["name"] == "Actual product"


def test_raw_and_visible_cache_lengths_remain_bounded(monkeypatch):
    original = parser.Element.cache_text
    caches = []

    def observed(element):
        original(element)
        caches.append((element.tag, len(element.raw_text), len(element.visible_text)))

    monkeypatch.setattr(parser.Element, "cache_text", observed)
    html = (
        PRODUCT.decode()
        + "<p>"
        + "x" * 25000
        + '</p><script type="application/ld+json">'
        + json.dumps({"padding": "x" * 210000})
        + "</script>"
    )
    assert parser.extract_product(html, URL)["name"] == "Shirt"
    assert caches
    for tag, raw, visible in caches:
        assert raw <= (200001 if tag == "script" else 6000)
        assert visible <= 6000
    assert ("script", 200001, 0) in caches


async def test_deadline_during_launch_still_kills_and_reaps(
    client, auth_headers, monkeypatch, parser_peers
):
    peers, started, state = parser_peers
    release_launch = asyncio.Event()
    state["launch_gate"] = release_launch
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
            elapsed = 6
            loop.call_soon(lambda: None)
            response = await request
            assert response.status_code == 422
            assert process.returncode is None
            assert service._parse_slots._value == 1
            release_launch.set()
            await state["reaped"].get()
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


async def event_loop_turn():
    ready = asyncio.Event()
    asyncio.get_running_loop().call_soon(ready.set)
    await ready.wait()


@pytest.mark.parametrize("trigger", ["cancel", "deadline"])
async def test_repeated_cancellation_keeps_slot_until_reap(parser_peers, monkeypatch, trigger):
    peers, started, state = parser_peers
    state["reap_gate"] = asyncio.Event()
    loop = asyncio.get_running_loop()
    actual_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: actual_time() + elapsed)
    task = asyncio.create_task(service.parse_product(PRODUCT, URL))
    try:
        process = await started_child(started, task)
        if trigger == "cancel":
            task.cancel("original cancellation")
        else:
            elapsed = 6
            loop.call_soon(lambda: None)
        await started_child(state["reap_entered"], task)
        await service._parse_slots.acquire()
        task.cancel("repeated cancellation")
        await event_loop_turn()
        assert service._parse_slots.locked(), "Cleanup released an unreaped child's slot"
        assert not task.done(), "Request escaped cleanup before reap"
        os.kill(process.pid, 0)
        task.cancel("another cancellation")
        await event_loop_turn()
        assert service._parse_slots.locked()
        state["reap_gate"].set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await task
        if trigger == "cancel":
            assert failure.value.args == ("original cancellation",)
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
        assert service._parse_slots._value == 1
        service._parse_slots.release()
        assert service._parse_slots._value == 2
    finally:
        monkeypatch.setattr(loop, "time", actual_time)
        state["reap_gate"].set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for process in peers:
            if process.returncode is None:
                process.kill()
            await process.wait()


async def test_stalled_launch_returns_422_and_retains_slot_for_late_child(
    parser_peers, monkeypatch
):
    peers, started, state = parser_peers
    state["creation_gate"] = asyncio.Event()
    loop = asyncio.get_running_loop()
    actual_time = loop.time
    original_timeout = asyncio.timeout
    elapsed = 0
    deadlines = []

    def timeout(delay):
        deadlines.append(delay)
        return original_timeout(delay)

    monkeypatch.setattr(loop, "time", lambda: actual_time() + elapsed)
    monkeypatch.setattr(asyncio, "timeout", timeout)
    task = None
    with patch.object(
        service, "download_public", AsyncMock(return_value=(PRODUCT, "text/html", URL))
    ):
        try:
            task = asyncio.create_task(service.fetch_product(URL))
            await state["launch_entered"].wait()
            assert 1 in deadlines, "Launch has no independent bounded deadline"
            elapsed = 2
            loop.call_soon(lambda: None)
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as failure:
                await task
            assert failure.value.status_code == 422
            assert not peers
            await service._parse_slots.acquire()
            assert service._parse_slots.locked(), "Late launch lost its cleanup slot"
            state["creation_gate"].set()
            process = await started.get()
            await state["reaped"].get()
            await event_loop_turn()
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
            assert process.returncode == -signal.SIGKILL
            assert service._parse_slots._value == 1
            service._parse_slots.release()
        finally:
            monkeypatch.setattr(loop, "time", actual_time)
            state["creation_gate"].set()
            if task and not task.done():
                task.cancel()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            for process in peers:
                if process.returncode is None:
                    process.kill()
                await process.wait()


async def test_busy_admission_does_not_download():
    await service._parse_slots.acquire()
    await service._parse_slots.acquire()
    from fastapi import HTTPException

    with patch.object(service, "download_public", AsyncMock()) as download:
        with pytest.raises(HTTPException) as failure:
            await service.fetch_product(URL)
        assert failure.value.status_code == 429
        download.assert_not_awaited()


async def test_download_race_still_checks_real_parse_admission():
    from fastapi import HTTPException

    async def download(*args, **kwargs):
        await service._parse_slots.acquire()
        await service._parse_slots.acquire()
        return PRODUCT, "text/html", URL

    with patch.object(service, "download_public", download):
        with pytest.raises(HTTPException) as failure:
            await service.fetch_product(URL)
        assert failure.value.status_code == 429
