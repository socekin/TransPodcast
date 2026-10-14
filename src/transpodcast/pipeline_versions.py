from __future__ import annotations

SPEECH_PIPELINE_V11 = "transpodcast-modal-pipeline-v11"
SPEECH_PIPELINE_V12 = "transpodcast-modal-pipeline-v12"
SPEECH_PIPELINE_V13 = "transpodcast-modal-pipeline-v13"
SPEECH_PIPELINE_V14 = "transpodcast-modal-pipeline-v14"
SPEECH_PIPELINE_V15 = "transpodcast-modal-pipeline-v15"
SPEECH_PIPELINE_V16 = "transpodcast-modal-pipeline-v16"
SPEECH_PIPELINE_V17 = "transpodcast-modal-pipeline-v17"
SPEECH_PIPELINE_V18 = "transpodcast-modal-pipeline-v18"
# V19 (IndexTTS 2.0) and V20 (IndexTTS 2.5) keep V17 Source Analysis and add
# the Rhythm Policy, the Unit Clip Store, and the TTS-only render cache
# identity. No new Run selects them since V21/V22 were released.
SPEECH_PIPELINE_V19 = "transpodcast-modal-pipeline-v19"
SPEECH_PIPELINE_V20 = "transpodcast-modal-pipeline-v20"
# V21 (IndexTTS 2.0) and V22 (IndexTTS 2.5) keep everything V19/V20 have and
# use V21 Source Analysis, which owns words with the deterministic ownership
# policy before the V17 continuity regions are built. New Runs select them.
SPEECH_PIPELINE_V21 = "transpodcast-modal-pipeline-v21"
SPEECH_PIPELINE_V22 = "transpodcast-modal-pipeline-v22"
DETERMINISTIC_OWNERSHIP_PIPELINES = frozenset(
    {SPEECH_PIPELINE_V21, SPEECH_PIPELINE_V22}
)
RHYTHM_PIPELINES = frozenset(
    {SPEECH_PIPELINE_V19, SPEECH_PIPELINE_V20, *DETERMINISTIC_OWNERSHIP_PIPELINES}
)
CONTINUITY_PIPELINES = frozenset(
    {SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V18, *RHYTHM_PIPELINES}
)
WORD_OWNERSHIP_PIPELINES = frozenset(
    {SPEECH_PIPELINE_V15, SPEECH_PIPELINE_V16, *CONTINUITY_PIPELINES}
)
INDEXTTS25_PIPELINES = frozenset(
    {
        SPEECH_PIPELINE_V16,
        SPEECH_PIPELINE_V18,
        SPEECH_PIPELINE_V20,
        SPEECH_PIPELINE_V22,
    }
)

SPEECH_MODEL_INDEXTTS2 = "indextts2"
SPEECH_MODEL_INDEXTTS25 = "indextts25"
SUPPORTED_SPEECH_MODELS = frozenset(
    {SPEECH_MODEL_INDEXTTS2, SPEECH_MODEL_INDEXTTS25}
)

CURRENT_SPEECH_PIPELINE_VERSION = SPEECH_PIPELINE_V21


def pipeline_version_for_speech_model(speech_model: str) -> str:
    # New Runs add deterministic ownership, the Span Identity Check,
    # sparse-label merge, and listening-oriented translation to the
    # V19/V20 rendering features.
    if speech_model == SPEECH_MODEL_INDEXTTS2:
        return SPEECH_PIPELINE_V21
    if speech_model == SPEECH_MODEL_INDEXTTS25:
        return SPEECH_PIPELINE_V22
    raise ValueError(f"Unsupported speech model: {speech_model}")


def speech_model_for_pipeline_version(pipeline_version: str) -> str:
    if pipeline_version in INDEXTTS25_PIPELINES:
        return SPEECH_MODEL_INDEXTTS25
    return SPEECH_MODEL_INDEXTTS2


def analysis_version_for_pipeline(pipeline_version: str) -> str:
    if pipeline_version in DETERMINISTIC_OWNERSHIP_PIPELINES:
        return SPEECH_PIPELINE_V21
    if pipeline_version in CONTINUITY_PIPELINES:
        return SPEECH_PIPELINE_V17
    return SPEECH_PIPELINE_V15
