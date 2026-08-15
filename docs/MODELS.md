# Models

Companion to the sibling [poster-metrics-pipeline](https://github.com/pulp-analytics/poster-metrics-pipeline)'s
`docs/MODELS.md`, which pins CLIP/SigLIP/YuNet by hash or Hub revision.
This repo only has one model dependency, and it's a different kind of
problem: it can't be pinned the same way.

## Amazon Bedrock (`us.amazon.nova-pro-v1:0`) -- 04_bedrock_ocr.py

Not pinnable from the caller's side. `us.amazon.nova-pro-v1:0` looks like
a version (the `v1:0` suffix), but it names a managed, hosted model --
AWS can update what that id actually serves server-side, with no
changelog visible to callers and no way to request "the exact weights
that answered this call last month." This is structurally different from
the Hugging Face / open_clip pins in the sibling repo, where the caller
controls (and can freeze) exactly which artifact loads.

What's captured instead, as the closest available substitute for a real
pin:

- **`04_bedrock_ocr.py`'s output CSV records `model` per row** (the
  `--model` value used for that call, e.g. `us.amazon.nova-pro-v1:0` vs.
  `us.amazon.nova-lite-v1:0` -- see `fields` in that script). Necessary
  but not sufficient: two rows can carry the identical `model` string
  while AWS quietly served different underlying weights for each, and
  this column can't tell you that happened.
- If you need a tighter provenance signal than the model id string alone,
  the practical option is a **timestamp per row** (not currently written)
  as a rough proxy for "which AWS-side model revision was live" --
  something to add if a future re-run's numbers ever need explaining
  against an older run's.

## Not applicable

- **TMDB API**, **Amazon Comprehend**, **Amazon Translate**: managed
  API calls, not model artifacts this repo selects or loads -- there's no
  model_id knob exposed to pin in the first place.
