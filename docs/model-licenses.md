# Third-Party Model Terms

TransPodcast orchestrates several speech models. Model weights and outputs are
not licensed by the TransPodcast application license. Operators must review the
exact terms attached to every model revision they deploy.

The current pipeline code references these model families. This inventory was
last verified on 2026-08-10:

| Model family | Pipeline role | Upstream terms to verify |
| --- | --- | --- |
| NVIDIA Streaming Sortformer | Speaker diarization | [NVIDIA Open Model License](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1) |
| Qwen3-ASR | Speech recognition | [Apache License 2.0](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) |
| Qwen3 ForcedAligner | Word alignment | [Apache License 2.0](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) |
| NVIDIA TitaNet | Speaker embeddings | [CC BY 4.0](https://huggingface.co/nvidia/speakerverification_en_titanet_large) |
| IndexTTS2 | Mandarin speech synthesis | [Bilibili Model Use License Agreement](https://github.com/index-tts/index-tts/blob/main/LICENSE) |

This table is an inventory, not legal advice and not a substitute for the
upstream license files. In particular, custom model-use terms may impose use,
distribution, attribution, organization-size, or commercial restrictions that
differ from an OSI-approved software license.

The IndexTTS2 agreement deserves specific review. At the verification date it
requires a separate upstream license for certain organizations above its
monthly-active-user or annual-revenue thresholds, imposes downstream and notice
obligations, and restricts several uses. A TransPodcast commercial license does
not grant or replace any required IndexTTS2 permission.

## Operator checklist

1. Resolve the exact model ID and revision from the Modal application.
2. Save the upstream license and model card that applied on the deployment date.
3. Confirm the upstream terms cover the intended deployment and output use.
4. Include required attribution in the delivered product.
5. Repeat the review before changing a model or revision.

Coordinate model identifier changes with the maintainer so code, documentation,
quality baselines, and license review stay synchronized. Do not commit
downloaded model weights to this repository.
