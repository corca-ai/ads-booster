from __future__ import annotations

from PIL import Image

from ads_booster.capture.ios_lock_screen_layer import compose_ios_lock_screen_layer


def test_compose_when_imagegen_layer_is_transparent_then_returns_opaque_trace_png() -> None:
    # Given an opaque Trace wallpaper and one transparent generated UI layer
    wallpaper = Image.new("RGB", (3, 2), (10, 20, 30))
    layer = Image.new("RGBA", (3, 2), (0, 0, 0, 0))
    layer.putpixel((1, 0), (255, 0, 0, 128))

    # When the ImageGen UI layer is composited over the Trace wallpaper
    final = compose_ios_lock_screen_layer(wallpaper, layer)

    # Then the final PNG remains opaque and only the generated UI pixel changes
    assert final.mode == "RGB"
    untouched = final.crop((0, 0, 1, 1))
    assert untouched.getchannel("R").histogram()[10] == 1
    assert untouched.getchannel("G").histogram()[20] == 1
    assert untouched.getchannel("B").histogram()[30] == 1
    composed = final.crop((1, 0, 2, 1))
    assert composed.getchannel("R").histogram()[133] == 1
    assert composed.getchannel("G").histogram()[10] == 1
    assert composed.getchannel("B").histogram()[15] == 1
