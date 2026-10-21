"""MCP tools through an in-process client (real protocol, fake use cases)."""
import base64
from dataclasses import replace

import pytest
from mcp import Client

from mammo_application.common import Result
from mammo_mcp import McpOptions, create_mcp_server

from .fakes import NOT_MAMMOGRAM, OVERLAY, PNG_BYTES, analysis, handlers

B64 = base64.b64encode(PNG_BYTES).decode()
pytestmark = pytest.mark.anyio


def server(h=None, local=False):
    return create_mcp_server(h or handlers(), McpOptions(allow_local_paths=local, max_image_bytes=1_000_000))


async def test_lists_three_read_only_tools():
    async with Client(server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"mammo_analyze_mammogram", "mammo_check_is_mammogram", "mammo_mask_personal_data"}
    for tool in tools.values():
        assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
        assert "image_base64" in tool.input_schema["properties"]


async def test_analyze_returns_summary_structured_content_and_heatmap():
    h = handlers()
    async with Client(server(h)) as client:
        result = await client.call_tool("mammo_analyze_mammogram",
                                        {"image_base64": "data:image/png;base64," + B64, "sides": ["R"],
                                         "views": ["MLO"]})
    assert not result.is_error
    assert "HIGH PRIORITY" in result.content[0].text
    assert result.content[1].type == "image" and result.content[1].mime_type == "image/png"
    breast = result.structured_content["breasts"][0]
    assert breast["level"] == "high_priority" and breast["views"][0]["has_heatmap"] is True
    assert "heatmap" not in breast["views"][0] and "heatmap_overlay" not in breast["views"][0]
    assert result.structured_content["heatmap_format"] == "combined" and len(result.content) == 2
    command = h.analyze.calls[0]
    assert command.image.data == PNG_BYTES and command.sides == ("R",) and command.views == ("MLO",)
    assert command.heatmap_format == "combined"


async def test_analyze_with_heatmap_layers_returns_the_breast_then_the_red_areas():
    h = handlers(analyze=Result.ok(analysis(layers=True)))
    async with Client(server(h)) as client:
        result = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64, "heatmap_format": "layers"})
    assert h.analyze.calls[0].heatmap_format == "layers"
    assert result.structured_content["heatmap_format"] == "layers"
    assert [c.type for c in result.content] == ["text", "image", "image"]
    assert base64.b64decode(result.content[2].data) == OVERLAY.data


async def test_a_view_without_its_red_layer_sends_no_image_rather_than_shifting_the_others():
    view = analysis(layers=True).breasts[0].views[0]
    alone = replace(view, panel=1, heatmap_overlay=None)
    breast = replace(analysis().breasts[0], views=[alone, replace(view, panel=2)])
    h = handlers(analyze=Result.ok(replace(analysis(), breasts=[breast])))
    async with Client(server(h)) as client:
        result = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64, "heatmap_format": "layers"})
    views = result.structured_content["breasts"][0]["views"]
    assert [v["has_heatmap"] for v in views] == [False, True]
    assert [c.type for c in result.content] == ["text", "image", "image"]


async def test_analyze_json_format():
    async with Client(server()) as client:
        result = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64, "response_format": "json"})
    assert '"overall_level": "high_priority"' in result.content[0].text


async def test_not_a_mammogram_is_a_tool_error_with_code():
    async with Client(server(handlers(analyze=NOT_MAMMOGRAM))) as client:
        result = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64})
    assert result.is_error
    assert result.structured_content["error"] == "not_a_mammogram"


@pytest.mark.parametrize("args, fragment", [
    ({}, "exactly one"),
    ({"image_base64": B64, "image_path": "x.png"}, "exactly one"),
    ({"image_base64": "%%%not-base64%%%"}, "not valid base64"),
    ({"image_base64": base64.b64encode(b"hello world").decode()}, "not a supported image"),
    ({"image_path": "C:/x.png"}, "only available"),
])
async def test_invalid_inputs_are_actionable_errors(args, fragment):
    h = handlers()
    async with Client(server(h)) as client:
        result = await client.call_tool("mammo_analyze_mammogram", args)
    assert result.is_error and fragment in result.content[0].text
    assert h.analyze.calls == []


async def test_oversized_base64_is_rejected_before_decoding():
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1_100_000).decode()
    async with Client(server()) as client:
        result = await client.call_tool("mammo_check_is_mammogram", {"image_base64": big})
    assert result.is_error and "larger than" in result.content[0].text


async def test_image_path_allowed_in_local_mode(tmp_path):
    image = tmp_path / "mammo.png"
    image.write_bytes(PNG_BYTES)
    h = handlers()
    async with Client(server(h, local=True)) as client:
        result = await client.call_tool("mammo_check_is_mammogram", {"image_path": str(image)})
        rejected = await client.call_tool("mammo_check_is_mammogram", {"image_path": str(tmp_path / "secret.txt")})
    assert not result.is_error and "is a mammogram" in result.content[0].text
    assert rejected.is_error and "extension" in rejected.content[0].text


async def test_mask_returns_image_and_counts_only():
    async with Client(server()) as client:
        result = await client.call_tool("mammo_mask_personal_data", {"image_base64": B64})
    assert result.structured_content == {"masked_regions": 3, "region_categories": {"date": 1}}
    assert result.content[1].type == "image"


@pytest.mark.parametrize("args, code", [
    ({"image_base64": "%%%not-base64%%%"}, "invalid_input"),
    ({"image_base64": base64.b64encode(b"GIF89a....").decode()}, "unsupported_media_type"),
    ({"image_base64": "data:image/png;base64," + base64.b64encode(b"GIF89a....").decode()}, "unsupported_media_type"),
])
async def test_input_errors_carry_a_code_and_never_trust_the_declared_type(args, code):
    async with Client(server()) as client:
        result = await client.call_tool("mammo_check_is_mammogram", args)
    assert result.is_error and result.structured_content["error"] == code


async def test_image_of_exactly_the_limit_is_accepted():
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * (1_000_000 - 8)
    async with Client(server()) as client:
        result = await client.call_tool("mammo_check_is_mammogram", {"image_base64": base64.b64encode(data).decode()})
    assert not result.is_error


async def test_unexpected_exceptions_become_internal_error_without_details():
    class Boom:
        def handle(self, _):
            raise RuntimeError("secret internal detail")

    h = handlers()
    h = type(h)(analyze=Boom(), check=h.check, mask=h.mask)
    async with Client(server(h)) as client:
        result = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64})
    assert result.is_error and result.structured_content["error"] == "internal_error"
    assert "secret" not in result.content[0].text


async def test_server_answers_busy_beyond_the_pending_limit():
    import threading
    release = threading.Event()

    class Slow:
        def handle(self, query):
            release.wait(5)
            return handlers().check.result

    h = handlers()
    h = type(h)(analyze=h.analyze, check=Slow(), mask=h.mask)
    mcp = create_mcp_server(h, McpOptions(allow_local_paths=False, max_image_bytes=1_000_000, max_pending_requests=1))
    import anyio
    results = []
    async with Client(mcp) as client:
        async def call():
            results.append(await client.call_tool("mammo_check_is_mammogram", {"image_base64": B64}))
        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            await anyio.sleep(0.2)
            tg.start_soon(call)
            await anyio.sleep(0.2)
            release.set()
    codes = sorted((r.structured_content or {}).get("error", "ok") for r in results)
    assert codes == ["ok", "server_busy"]


async def test_summary_in_spanish_on_request_structured_content_unchanged():
    async with Client(server()) as client:
        en = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64})
        es = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64, "language": "es"})
    assert "HIGH PRIORITY" in en.content[0].text and "ALTA PRIORIDAD" in es.content[0].text
    assert en.structured_content == es.structured_content


async def test_schema_errors_from_the_sdk_have_no_code():
    """Documented contract: clients must treat errors without structuredContent.error as unexpected."""
    async with Client(server()) as client:
        result = await client.call_tool("mammo_analyze_mammogram", {"image_base64": B64, "panel_count": 9})
    assert result.is_error and result.structured_content is None
