from __future__ import annotations

from ads_booster.contracts.native_export import ImagegenIosUiManifest


def test_manifest_when_trace_png_and_imagegen_ui_layer_are_bound_then_tracks_both_digests() -> None:
    # Given a final ImageGen image built from one Trace PNG and one generated iOS UI layer
    manifest = ImagegenIosUiManifest(
        schema_version="trace.imagen-ios-ui.v1",
        request_sha256="a" * 64,
        export_nonce="b" * 64,
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        source_trace_artifact_sha256="c" * 64,
        imagegen_prompt_sha256="d" * 64,
        imagegen_ui_layer_sha256="e" * 64,
        artifact_sha256="f" * 64,
        width=1206,
        height=2622,
    )

    # When the worker serializes its final-artifact provenance
    payload = manifest.model_dump()

    # Then the final bytes and both inputs remain independently identifiable
    assert payload["source_trace_artifact_sha256"] == "c" * 64
    assert payload["imagegen_prompt_sha256"] == "d" * 64
    assert payload["imagegen_ui_layer_sha256"] == "e" * 64
    assert payload["artifact_sha256"] == "f" * 64
