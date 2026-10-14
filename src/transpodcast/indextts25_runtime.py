from __future__ import annotations

INDEXTTS25_MODEL_ID = "IndexTeam/IndexTTS-2.5"
# pragma: allowlist nextline secret
INDEXTTS25_MODEL_REVISION = "c39ce5ba981572cb187443877ff559dfb246ce63"
# pragma: allowlist nextline secret
INDEXTTS25_COMMIT = "ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c"
INDEXTTS25_BASE_IMAGE = (
    "docker.io/library/python:3.11.13-slim-bookworm@"
    "sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1"
)
INDEXTTS25_DEBIAN_SNAPSHOT = "20260825T000000Z"
INDEXTTS25_RUNTIME_REVISION = "indextts25-runtime-2026-08-25-v1"
INDEXTTS25_NATIVE_HEADROOM_TARGET = 0.98
INDEXTTS25_LOAD_SETTINGS = {
    "use_bf16": True,
    "use_cuda_kernel": False,
    "use_deepspeed": False,
    "use_accel": False,
    "use_torch_compile": False,
    "use_qwen_emo": False,
}
INDEXTTS25_INFERENCE_SETTINGS = {
    "lang": "zh",
    "use_emo_text": False,
    "use_random": False,
    "interval_silence": 200,
    "verbose": False,
    "max_text_tokens_per_segment": 120,
    "stream_return": False,
    "duration_factor": 1.0,
    "text_normalization": True,
    "top_p": 0.8,
    "top_k": 30,
    "temperature": 0.8,
    "do_sample": True,
    "num_beams": 3,
    "repetition_penalty": 10.0,
    "max_mel_tokens": 1500,
}
# pragma: allowlist nextline secret
NLTK_DATA_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
NLTK_DATA_ARTIFACTS = (
    (
        "taggers",
        "averaged_perceptron_tagger.zip",
        # pragma: allowlist nextline secret
        "e1f13cf2532daadfd6f3bc481a49859f0b8ea6432ccdcd83e6a49a5f19008de9",
    ),
    (
        "taggers",
        "averaged_perceptron_tagger_eng.zip",
        # pragma: allowlist nextline secret
        "6025f530624335c67d6547d44757b357b4e79bae030a0383e9887a92c1718f0b",
    ),
    (
        "corpora",
        "cmudict.zip",
        # pragma: allowlist nextline secret
        "d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6",
    ),
)
