from experiments.pipeline.modal_sortformer import (
    GPU_TYPE,
    MODEL_FILENAME,
    MODEL_ID,
    MODEL_REVISION,
    NEMO_VERSION,
    STREAMING_CACHE_UPDATE_FRAMES,
    STREAMING_CHUNK_FRAMES,
    STREAMING_FIFO_FRAMES,
    STREAMING_RIGHT_CONTEXT_FRAMES,
    STREAMING_SPEAKER_CACHE_FRAMES,
    _configure_streaming_model,
)


def test_sortformer_uses_the_long_form_streaming_checkpoint() -> None:
    assert MODEL_ID == "nvidia/diar_streaming_sortformer_4spk-v2.1"
    assert MODEL_REVISION == (
        "fafaab5faa1617a0ca52d38dd3dc4bd636800d3d"  # pragma: allowlist secret
    )
    assert MODEL_FILENAME == "diar_streaming_sortformer_4spk-v2.1.nemo"
    assert NEMO_VERSION == "2.7.3"
    assert GPU_TYPE == "L40S"


def test_sortformer_uses_the_recommended_high_latency_streaming_window() -> None:
    class Modules:
        validated = False

        def _check_streaming_parameters(self) -> None:
            self.validated = True

    class Model:
        sortformer_modules = Modules()

    model = Model()

    _configure_streaming_model(model)

    modules = model.sortformer_modules
    assert modules.chunk_len == STREAMING_CHUNK_FRAMES == 340
    assert modules.chunk_right_context == STREAMING_RIGHT_CONTEXT_FRAMES == 40
    assert modules.fifo_len == STREAMING_FIFO_FRAMES == 40
    assert modules.spkcache_update_period == STREAMING_CACHE_UPDATE_FRAMES == 300
    assert modules.spkcache_len == STREAMING_SPEAKER_CACHE_FRAMES == 188
    assert modules.validated is True
