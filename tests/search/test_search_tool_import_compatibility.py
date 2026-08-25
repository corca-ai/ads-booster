from __future__ import annotations

from trace_capture.tools.image_search import ImageSearchTool as RenamedImageSearchTool
from trace_capture.tools.text_search import WebSearchTool as RenamedWebSearchTool
from trace_capture.tools.web_image_search import ImageSearchTool
from trace_capture.tools.web_search import WebSearchTool


def test_canonical_search_tool_modules_reexport_renamed_implementations() -> None:
    # Given canonical module paths retained by existing tool contracts
    # When the search tools are imported through those paths
    # Then they resolve to the current implementations without duplication
    assert ImageSearchTool is RenamedImageSearchTool
    assert WebSearchTool is RenamedWebSearchTool
