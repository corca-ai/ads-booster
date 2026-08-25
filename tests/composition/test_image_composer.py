from __future__ import annotations

# pyright: reportUnknownMemberType=false
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from trace_capture.composition.image_composer import (
    CanvasSize,
    CompositionLayers,
    LayerCompositionError,
    LayerRegion,
    compose_marketing_image,
    extract_component_regions,
    normalize_ai_ui_layer,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_normalize_ui_layer_when_background_is_opaque_black(tmp_path: Path) -> None:
    # Given an AI-generated system UI image baked onto black pixels
    source = tmp_path / "ui-source.png"
    destination = tmp_path / "ui-normalized.png"
    image = Image.new("RGB", (2, 2), (0, 0, 0))
    image.putpixel((1, 0), (255, 255, 255))
    image.putpixel((0, 1), (48, 48, 48))
    image.save(source)

    # When the layer is normalized for a transparent compositor
    normalize_ai_ui_layer(
        source=source,
        destination=destination,
        canvas=CanvasSize(width=2, height=2),
    )

    # Then pure background becomes transparent while UI pixels remain visible
    with Image.open(destination) as normalized:
        assert normalized.mode == "RGBA"
        alpha = normalized.getchannel("A")
        assert alpha.crop((0, 0, 1, 1)).getextrema() == (0, 0)
        assert alpha.crop((1, 0, 2, 1)).getextrema() == (255, 255)
        assert alpha.crop((0, 1, 1, 2)).getextrema() == (255, 255)


def test_compose_marketing_image_when_all_layers_overlap(tmp_path: Path) -> None:
    # Given independent background, Trace component, and iPhone UI layers
    background = tmp_path / "background.png"
    components = tmp_path / "components.png"
    iphone_ui = tmp_path / "iphone-ui.png"
    output = tmp_path / "final.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(background)
    component_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    component_image.putpixel((0, 0), (0, 255, 0, 255))
    component_image.save(components)
    ui_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    ui_image.putpixel((0, 0), (0, 0, 255, 255))
    ui_image.save(iphone_ui)

    # When the compositor renders the marketing image
    compose_marketing_image(
        layers=CompositionLayers(
            background=background,
            trace_components=components,
            iphone_ui=iphone_ui,
        ),
        destination=output,
        canvas=CanvasSize(width=2, height=2),
    )

    # Then the order is background, Trace components, and iPhone UI on top
    with Image.open(output) as final:
        assert final.crop((0, 0, 1, 1)).convert("RGB").getextrema() == (
            (0, 0),
            (0, 0),
            (255, 255),
        )
        assert final.crop((1, 1, 2, 2)).convert("RGB").getextrema() == (
            (255, 255),
            (0, 0),
            (0, 0),
        )


def test_compose_marketing_image_when_component_layer_is_nearly_whole_image(
    tmp_path: Path,
) -> None:
    # Given a near-opaque full-canvas image masquerading as Trace components
    background = tmp_path / "background.png"
    components = tmp_path / "components.png"
    iphone_ui = tmp_path / "iphone-ui.png"
    output = tmp_path / "final.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(background)
    component_image = Image.new("RGBA", (10, 10), (0, 255, 0, 255))
    component_image.putpixel((0, 0), (0, 0, 0, 0))
    component_image.save(components)
    ui_image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    ui_image.putpixel((0, 0), (0, 0, 255, 255))
    ui_image.save(iphone_ui)

    # When the compositor validates the component-only boundary
    with pytest.raises(LayerCompositionError) as raised:
        compose_marketing_image(
            layers=CompositionLayers(
                background=background,
                trace_components=components,
                iphone_ui=iphone_ui,
            ),
            destination=output,
            canvas=CanvasSize(width=10, height=10),
        )

    # Then it rejects the whole-screen substitute before composition
    assert raised.value.path == components


def test_compose_marketing_image_when_no_iphone_ui_layer_is_supplied(
    tmp_path: Path,
) -> None:
    # Given a valid background and native Trace layer without system UI
    background = tmp_path / "background.png"
    components = tmp_path / "components.png"
    output = tmp_path / "final.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(background)
    component_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    component_image.putpixel((0, 0), (0, 255, 0, 255))
    component_image.save(components)
    # When the compositor renders only product-owned layers
    compose_marketing_image(
        layers=CompositionLayers(
            background=background,
            trace_components=components,
            iphone_ui=None,
        ),
        destination=output,
        canvas=CanvasSize(width=2, height=2),
    )

    # Then Trace content overlays the searched background without system icons
    with Image.open(output) as final:
        assert final.getpixel((0, 0))[:3] == (0, 255, 0)


def test_compose_marketing_image_when_fit_removes_component_transparency(
    tmp_path: Path,
) -> None:
    # Given transparency that exists only outside the fitted center crop
    background = tmp_path / "background.png"
    components = tmp_path / "components.png"
    iphone_ui = tmp_path / "iphone-ui.png"
    output = tmp_path / "final.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(background)
    component_image = Image.new("RGBA", (4, 2), (0, 255, 0, 255))
    for x in (0, 3):
        for y in range(2):
            component_image.putpixel((x, y), (0, 0, 0, 0))
    component_image.save(components)
    ui_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    ui_image.putpixel((0, 0), (0, 0, 255, 255))
    ui_image.save(iphone_ui)

    # When the compositor fits the component layer to the target canvas
    with pytest.raises(LayerCompositionError) as raised:
        compose_marketing_image(
            layers=CompositionLayers(
                background=background,
                trace_components=components,
                iphone_ui=iphone_ui,
            ),
            destination=output,
            canvas=CanvasSize(width=2, height=2),
        )

    # Then it validates the fitted pixels and rejects the opaque crop
    assert raised.value.path == components


def test_extract_component_regions_when_source_contains_surrounding_pixels(
    tmp_path: Path,
) -> None:
    # Given an opaque screenshot with one bounded Trace component
    source = tmp_path / "preview.png"
    destination = tmp_path / "components.png"
    Image.new("RGB", (4, 4), (255, 0, 0)).save(source)

    # When only the configured component rectangle is extracted
    extract_component_regions(
        source=source,
        destination=destination,
        regions=(LayerRegion(left=1, top=1, width=2, height=2, corner_radius=0),),
    )

    # Then the surrounding screenshot becomes transparent
    with Image.open(destination) as extracted:
        alpha = extracted.getchannel("A")
        assert alpha.crop((0, 0, 1, 1)).getextrema() == (0, 0)
        assert alpha.crop((1, 1, 2, 2)).getextrema() == (255, 255)
