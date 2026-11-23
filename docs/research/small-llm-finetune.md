# Fine-tuning a small LLM for Fortify Assist: viability assessment

Date of research: 2026-10-24. Method: web search plus page fetches. Every claim carries a URL. Tags used:
**[V]** = read on the cited page. **[S]** = secondary or aggregator source. **[U]** = unverified or inferred, or sources conflict.
Nothing in this document was benchmarked by us. Every throughput figure is someone else's, on different configs.

## 0. Bottom line

1. **Recommend (c), staged.** Ship (a) first: deterministic pipeline plus a generic small LLM that sees top-K retrieved candidates, with grammar or schema-constrained output. Build the eval harness at the same time. Fine-tune only if the eval shows a residual error rate that prompting, retrieval and constraints cannot close. Do not make a fine-tuned model the main path (b).
2. **The user's suspicion is right, with a refinement.** Fine-tuning does not fix scaling with entity count. Scaling is solved by not putting all entities in the prompt (retrieval and shortlisting), and by constraining output to a closed vocabulary. A fine-tune bakes in output format and tool-schema habits, which is useful. It cannot bake in a user's changing entity list. Home-LLM's own design puts the device list in the prompt, so it inherits the context growth problem (see section 3).
3. **Licensing is now much less of a blocker.** Gemma 4 (April 2026) and Qwen3.5 ship under Apache 2.0, so a redistributable fine-tune is straightforward. The Gemma 1 to 3 era licence and the Llama 3.2 community licence are not OSI-style open source. Home-LLM's current best models (Home-Llama-3.2-3B, Home-FunctionGemma-270m) inherit those restrictive licences. Do not build on them for an Apache-2.0 repo.
4. **Top-3 things to measure first:** (i) the resolver's coverage, meaning what fraction of real utterances needs the LLM at all; (ii) end-to-end tool-call accuracy of generic Qwen3.5-4B and Gemma 4 E2B with top-K=8 to 12 candidates and a grammar, on our own eval set; (iii) real `llama-bench` prefill and decode numbers on the actual Orin Nano at the prompt lengths we will use.

## 1. Candidate base models (about 4 GB budget at 4-bit)

Budget arithmetic: 8 GB unified minus roughly 4 GB for Home Assistant leaves about 4 GB. Weights, KV cache, CUDA context and any vision projector must all fit. Practically that means about 2.5 to 3 GB of weights at most, with a 2 to 4k context.

| Model | Params | Q4 size | Licence | Tool calling | Vision | Notes / source |
|---|---|---|---|---|---|---|
| **Gemma 4 E2B** | 2.3B effective, 5.1B with embeddings | Not found [U]. Embeddings may be heavy on disk. NVIDIA's Orin Nano guide uses `unsloth/gemma-4-E2B-it-GGUF:Q4_K_S` | **Apache 2.0** [V] | Native function calling, structured JSON, system prompts [V] | Image and video in, audio in on E2B/E4B [V] | Released 2026-04-02 (https://the-decoder.com/?p=33933). NVIDIA names E2B as the variant that "fits best" on Orin Nano and says llama.cpp works, Ollama does not currently work on Orin Nano (https://www.jetson-ai-lab.com/tutorials/gemma4-on-jetson/). E4B hit CUDA OOM on GPU on an Orin Nano (https://forums.developer.nvidia.com/t/gemma4-e4b-on-jetson-orin-nano-fails-due-to-cuda-out-of-memory-issue/365641) [S via search snippet]. NVIDIA warns of an audio issue for E2B in llama.cpp on Orin. |
| **Qwen3.5-4B** | 4B | about 2.7 to 3.0 GB Q4_K_M (https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF) [S] | Apache 2.0 [S] (https://artificialanalysis.ai/articles/qwen3-5-small-models) | Qwen XML function-call format [S] (mittwald page found via search) | Natively multimodal per Artificial Analysis [S]. One host page says no vision on 0.8B, so conflict [U] | March 2026 family: 0.8B, 2B, 4B, 9B. 4B thinks by default per KDnuggets [S], so disable thinking for latency. NVIDIA lists Qwen3.5-4B INT4 AWQ as fitting 8 GB (https://www.jetson-ai-lab.com/tutorials/tensorrt-edge-llm) [V]. |
| **Qwen3.5-2B / 0.8B** | 2B / 0.8B | about 1.3 / 0.6 GB [U, estimated] | Apache 2.0 [S] | Yes [S] | Yes [S, conflicting for 0.8B] | Best candidates for the "fits alongside everything" role. |
| **Qwen3-4B-Instruct-2507** | 4B | about 2.5 GB [U, estimated] | Apache 2.0 [U, from memory] | Good tool use in practice; BFCL numbers conflict (35.5% vs 61.9%, different versions or metrics) [U] | No (text only) | Worked example in NVIDIA's TensorRT Edge-LLM page [V]. Forum users call Qwen2.5 the best small model for HA tool use (https://forum.level1techs.com/t/home-assistant-exploration-of-fully-local-voice-assist/229801) [S]. |
| **Qwen3-VL-2B** | 2B | about 1.5 GB [U] | Apache 2.0 [S] | Limited [U] | Yes | Cosmos-Reason2-2B is Qwen3-VL-2B based and is the one measured on Orin Nano (section 2). |
| **FunctionGemma 270M** | 270M | about 0.3 GB at Q8/BF16-ish [U] | **Gemma Terms of Use** (custom; not OSI) [V] | Purpose-built, intended to be fine-tuned. Google reports 58% to 85% on its "Mobile Actions" eval after fine-tuning (https://blog.google/technology/developers/functiongemma/) [S] | No | Google says it is not a dialogue model (https://ai.google.dev/gemma/docs/functiongemma) [V]. |
| **Home-FunctionGemma-270m** (acon96) | 0.3B | small | Gemma terms (https://huggingface.co/acon96/Home-FunctionGemma-270m) [V] | Yes, HA specific | No | Trained on Home-Assistant-Requests-V2 (240k rows), 597 steps, 2 GPUs, Axolotl. No accuracy published on the card [V]. |
| **Home-Llama-3.2-3B** (acon96) | 3B | about 2 GB at Q4_K_M [U] | Llama 3.2 Community Licence (https://huggingface.co/acon96/Home-Llama-3.2-3B) [V] | Yes | No | No accuracy on card [V]. Licence is not OSI-open. |
| **Home-3B-v3 / Home-1B-v3** (acon96) | 3B / 1.1B | n/a | **Non-commercial only** (StableLM) for 3B-v3 [V] (https://huggingface.co/acon96/Home-3B-v3-GGUF) | Yes | No | Self-reported 97.11% JSON function-calling accuracy on its own test split [V]. Obsolete and unusable for us licence-wise. |
| **Llama 3.2 1B / 3B** | 1.2B / 3.2B | about 0.8 / 2 GB [U] | Llama 3.2 Community Licence, not OSI, with an acceptable use policy [V via Home-Llama card] | Yes, moderate | Text only (vision is the 11B+) | Avoid as a base for an Apache-2.0 project. |
| **SmolLM3-3B** | 3B | about 1.8 GB [U] | Apache 2.0 [U, from memory; the blog fetch did not state it] | Yes (`xml_tools`, `python_tools`); 128k context via YaRN (https://huggingface.co/blog/smollm3) [V] | No | |
| **Phi-4-mini** | 3.8B | about 2.3 GB [U] | MIT [U, from memory] | Yes | No | Not researched here. |
| **Gemma 3 / 3n** | 1B to 4B / E2B | n/a | Gemma Terms | Gemma 3n E2B: about 16.6 tok/s on Orin Nano Super via Ollama; Gemma 3 4B about 10.1 tok/s (https://forums.developer.nvidia.com/t/gemma-3-and-gemma-3n-on-jetson-orin-nano-super/337513) [S] | 3n yes | Superseded by Gemma 4 and carries the restrictive licence. |
| **SmolVLM2 256M / 500M** | 0.26B / 0.5B | about 0.3 to 0.7 GB [S, estimates vary 0.3 to 1.8 GB] | Apache 2.0 [S] | No | Yes, small | https://www.promptlayer.com/models/smolvlm2-500m-video-instruct |
| **Moondream 2** | about 1.9B | about 1 to 1.5 GB [U] | Apache 2.0 per Roboflow [S] (https://playground.roboflow.com/models/compare/moondream-2-vs-smolvlm2) | No | Yes | Photon runtime claims Jetson Orin Nano support (https://moondream.ai/photon) [S], but no Orin latency numbers were found. |

### Licensing note on Gemma 3 / FunctionGemma
The Gemma Terms of Use treat fine-tuned weights as "Model Derivatives", and distribution requires passing on the use restrictions and including the terms and a notice (https://ai.google.dev/gemma/terms) [V]. Google may also restrict usage "remotely". That is a custom licence, not OSI-open, and it conflicts with plain Apache-2.0 redistribution of weights. This is my reading, not legal advice. **Gemma 4 is Apache 2.0, which removes the problem for Gemma 4 based work.** Confirm on the official model card [U], since the page we fetched only said Gemma 4 has "a separate license".

### Which models are credible
- **Primary fallback candidates:** Gemma 4 E2B and Qwen3.5-4B (or Qwen3.5-2B for low-memory customers). Both are Apache 2.0, both do native tool calling, and both have vision.
- **Fine-tune-for-routing candidates:** Qwen3.5-0.8B and 2B, or Gemma 4 E2B, as bases that can be redistributed.
- **Avoid:** the Home-LLM models as base or dependency (licences), and FunctionGemma (Gemma terms, 270M is too small to reason about ambiguity, though it is fine for pure format mapping).

## 2. Runtime on Jetson Orin Nano (8 GB)

### Measured numbers that exist
| Source | Runtime | Model | Prefill | Decode | Memory |
|---|---|---|---|---|---|
| https://github.com/hokwangchoi/jetson-orin-nano-benchmarks [V] (MAXN_SUPER, JetPack 6.2.2) | llama.cpp Q4_K_M, ctx 4096 | Cosmos-Reason2-2B (Qwen3-VL-2B based) | TTFT 33 ms text (short prompt), 114 ms image (steady state) | 38 tok/s (TPOT 26 ms) | not reported |
| same | vLLM W4A16 AWQ, ctx 1024 | same | TTFT 61 ms text, 75 ms image | 56 tok/s | **6.9 GB peak** (too large for us) |
| same | TensorRT Edge-LLM W4A16 AWQ, ctx 1024 | same | TTFT 29 ms text, **420 ms image** | 60 tok/s | **4.3 GB** |
| https://forums.developer.nvidia.com/t/jetson-orin-nano-super-dev-kit-performance/318719 [V] (MLC, MAXN, user report, lower than NVIDIA's published numbers) | MLC q4f16 | Llama-3.2-3B | 339 tok/s | 27 tok/s (40 after `jetson_clocks`) | about 1.2 (units unclear) |
| same | MLC | Phi-3.5-mini | 281 | 25 (29) | |
| same | MLC | Gemma-2-2B | 111 | 25 (35) | |
| same | MLC | SmolLM2-1.7B | 439 | 44 (64) | |
| same | MLC | SmolLM2-360M | 478 | 112 | |
| https://forums.developer.nvidia.com/t/gemma-3-and-gemma-3n-on-jetson-orin-nano-super/337513 [S] | Ollama | Gemma 3n E2B / Gemma 3 4B | n/a | 16.6 / 10.1 tok/s | |

Caveats: the TTFT figures above are for short prompts (under about 100 tokens). They do not tell us anything about 1500 to 4000-token prompts. No source gave Orin Nano prefill throughput for Qwen3.5-4B or Gemma 4 E2B at long context. **We must measure with `llama-bench`.** Rule of thumb [U, extrapolated from the MLC table]: 3B-class prefill is a few hundred tok/s, so a 4k-token prompt costs on the order of 10 s or more with no prefix cache, and 1k tokens roughly 3 s. That is the core scaling cost (section 4).

### Runtime comparison
| Runtime | Orin Nano fit | Constrained decoding | Prefix / prompt caching | Notes |
|---|---|---|---|---|
| **llama.cpp (CUDA)** | Works; official NVIDIA container `ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin` [V] | **Yes: GBNF and JSON-schema to grammar** (https://www.mintlify.com/ggml-org/llama.cpp/advanced/grammars) [S]. Caveat: one user report of grammar construction failing on very large cumulative tool schemas (96 tools) (https://github.com/ggml-org/llama.cpp/issues/25895) [S]. That is irrelevant if we expose few tools. | Server has slot prompt caching and `--cache-reuse` [U, not verified in this research; check docs] | **Best recommendation for the reference platform.** Lowest memory, GGUF quants, broad model support. Vision via mmproj works for Qwen-VL and Gemma 4 [S/U]. |
| **Ollama** | Works for many models, but Gemma 4 currently does not work on Orin Nano per NVIDIA [V] | Structured outputs via JSON schema [U] | Yes (KV reuse) [U] | Convenient for HA users. Wraps llama.cpp. Acceptable as the "easy path" for varied hardware. |
| **MLC-LLM** | Works (NVIDIA's benchmark path) | Grammar support exists [U] | [U] | Fast decode, model coverage lags new releases. |
| **TensorRT Edge-LLM** | Official Orin support (JetPack 7.2 / R39.2, CUDA 13.2); INT4 AWQ; engines must be built on the device; export needs an x86 host (https://www.jetson-ai-lab.com/tutorials/tensorrt-edge-llm) [V] | Not mentioned in docs [V] | Not mentioned | Fastest decode (60 tok/s on 2B), but 420 ms image TTFT, tight build limits (`maxInputLen 512`, `maxKVCacheCapacity 1024` in the worked example) and a heavy toolchain. **Poor fit for variable-length HA prompts and for open-source users who bring their own hardware.** |
| **vLLM** | NVIDIA does not document vLLM on Orin Nano for Gemma 4 [V]; 6.9 GB peak on a 2B VLM [V] | Yes (xgrammar / guided decoding) [U] | Yes | **Does not fit the 4 GB budget.** |
| **jetson-containers** (dusty-nv) | Convenient prebuilt images for llama.cpp, MLC, etc. | n/a | n/a | Use it for build and deploy, not as a separate runtime. |

**Recommendation:** llama.cpp server as the reference runtime (works the same on Jetson, x86 CUDA, Apple, CPU), with an OpenAI-compatible endpoint so the conversation agent is runtime-agnostic. Treat Ollama and any OpenAI-compatible server as supported alternatives. Do not make TensorRT Edge-LLM a dependency.

Memory budget sketch [U, our estimate]: Qwen3.5-4B Q4_K_M weights 2.7 to 3.0 GB, KV cache at 4k context a few hundred MB, CUDA context plus compute buffers about 0.3 to 0.5 GB. That is about 3.5 to 4 GB, already at the ceiling, so a vision projector or a second model does not fit alongside it. A 2B model (about 1.5 GB) leaves room. This is the main argument for sizing by customer hardware tier.

## 3. Fine-tuning feasibility

### What Home-LLM (acon96) did
- **Repo:** https://github.com/acon96/home-llm [V]. Models progressed from Phi-1.5/Phi-2 (v1, v2), through TinyLlama-1.1B and StableLM-Zephyr-3B (v3), to the latest Home-Llama-3.2-3B and Home-FunctionGemma-270m. The repo says v0.4.6 added a new tool-calling dataset, v0.4.8 added JSON auto-repair for malformed tool calls, and v0.5.0 moved to the HA chat API. The JSON auto-repair is a tell: even fine-tuned models emitted malformed calls.
- **Dataset:** Home-Assistant-Requests-V2 (https://huggingface.co/datasets/acon96/Home-Assistant-Requests-V2/blob/main/README.md) [V]. **MIT licence.** English, German, French, Spanish, Polish. It is synthetic: CSV "piles" of device names, templated actions and status requests, combined by `generate_data.py` into several sizes (small, medium, large, xl). The FunctionGemma card lists it as 240k rows [V]. Responses come in several personalities.
- **Method (Home-3B-v3):** LoRA rank 64, alpha 128, targets `q,v,o,up,down_proj`, on a **single RTX 3090 (24 GB)**, merged at the end [V] (https://huggingface.co/acon96/Home-3B-v3-GGUF). The Home-FunctionGemma-270m run was 597 steps at an effective batch of 32, lr 2e-4, 2 GPUs [V].
- **Results:** The only number found is the self-reported **97.11% JSON function-calling accuracy on its own held-out synthetic test split** for Home-3B-v3 [V]. That split comes from the same templates as the training data, so it overstates real-world accuracy. The newer cards publish no accuracy [V]. Treat it as an in-distribution score, not a real-home score.
- **Known weaknesses [V/S]:** multilingual models are labelled experimental and were only spot-checked with Google-Translate-derived data; the format needed a JSON repair layer; the prompt contains the full device list, so cost scales with house size. An independent benchmark, HomeBench (https://arxiv.org/abs/2505.19628) [V for abstract], reports that even with in-context learning, RAG and fine-tuning, strong models still do poorly on invalid and multi-device instructions (GPT-4o 0.0% on invalid multi-device). I could not extract its per-method tables [U], so how fine-tuning compares to RAG there is not established here.

### Compute and cost estimate (our plan) [U, estimates from the above recipe, not quotes]
- QLoRA/LoRA on a 2B to 4B model, 50k to 250k short examples (about 150 to 400 tokens each, since the prompt only carries K candidates), 1 to 3 epochs. On one rented 24 GB GPU (RTX 3090/4090/A10-class) that is about 2 to 12 GPU-hours. At roughly US$0.3 to 1 per hour that is **about US$5 to 50 per run**, so US$200 to 500 covers 10 or more iterations including failed ones. Gemma-4/Qwen3.5 tooling (Unsloth, Axolotl, TRL) support is assumed but should be confirmed [U].
- Real cost is human effort: building the data generator and, above all, the eval set. Estimate 2 to 4 engineer-weeks for the first useful model; ongoing cost for each base-model generation (Qwen3.5 to Qwen4 etc.) because the adapter is not portable across bases.
- Export path: merge LoRA, convert to GGUF, quantize to Q4_K_M. Re-run the eval on the quantized artifact, since quantization can undo a small fine-tune gain.

### Data sources and licensing
- **Home Assistant intents repo** (https://github.com/OHF-Voice/intents, formerly home-assistant/intents) holds sentence templates across many languages. Licence: Creative Commons (CC BY 4.0 per my memory) [U, not verified here; verify before use]. Good for utterance diversity and multilingual coverage.
- **Home-Assistant-Requests-V2:** MIT [V]; usable as seed material.
- **Entity-graph generation:** sample synthetic homes (areas, floors, device types, names, aliases, duplicates, near-collisions) and generate utterances from templates and an LLM paraphraser. The risk is licence on the paraphraser: do not distil from a model whose terms forbid training competitors. Gemma terms explicitly include "synthetic data generated by Gemma" as a derivative [V], so prefer an Apache or MIT teacher.
- **Never train on real user homes** without consent. Fortify's own telemetry would need opt-in.
- **Base-model licence is inherited by the weights.** Apache 2.0 bases (Gemma 4, Qwen3.5) mean we can publish the adapter and merged weights under Apache 2.0.

### Building the eval
Fixed, versioned set, never trained on:
1. **Resolution accuracy:** correct entity (and correct "ask to clarify" when ambiguous), over homes of 20, 100, 300, 1000 entities.
2. **Call accuracy:** domain, service, arguments (brightness, temperature, position), validated against the HA service schema.
3. **Refusal and clarification:** nonexistent device, ambiguous "the light", unsafe action (locks, alarm) should not be executed without confirmation.
4. **Held-out phrasing and held-out homes:** this catches the in-distribution inflation seen in Home-LLM's 97%.
5. **Multi-language slices**, and **noisy ASR** variants (homophones, dropped words).
6. Metrics: exact tool-call match, executed-state match (state-verifying executor), p50/p95 latency on the Orin Nano, tokens in the prompt.
The existing harness in the repo (`src/fortify_eval`, Milestone 0 commit 67ea94a) is the natural home for this.

## 4. The scaling problem

### How cost grows with exposed entities
- Home-LLM-style and stock HA LLM prompts list every exposed entity. Community guidance: at least ~10k context, and limit exposed entities (https://github.com/skye-harris/hass_local_openai_llm) [S]. One user reported ~5,400 tokens for 80 exposed entities, so **about 65 tokens per entity** including the tool boilerplate [S, anecdote; https://forum.level1techs.com/t/home-assistant-exploration-of-fully-local-voice-assist/229801]. Another found that Ollama's default 2048 context silently truncated the HA prompt [S].
- Extrapolating [U]: 300 entities is about 20k tokens, and 1000 entities is about 65k tokens. At a few hundred tokens/s prefill on an Orin Nano 3B-class model, 20k tokens is on the order of **a minute** of prefill, and the KV cache for 20k+ tokens would not fit in the 4 GB budget with a 4B model. So "all entities in the prompt" is infeasible on the reference hardware beyond roughly 50 to 100 entities, for any model, fine-tuned or not.
- Accuracy also degrades with distractors in small models (lost-in-the-middle effects, near-duplicate names) [U, general literature; not verified for HA specifically].

### Mitigations and why they work
1. **Retrieval of top-K candidates** (K of about 5 to 12) by lexical and embedding match on name, aliases, area, floor and domain: prompt size becomes **constant in house size**, about 300 to 800 tokens including the tool schema. Prefill falls to about 1 to 3 s at Orin Nano speeds [U, extrapolated], and a static system prefix can be cached.
2. **Closed-vocabulary, schema-constrained output:** the tool schema's `entity_id` becomes an `enum` of the K candidates, and the grammar makes any other entity id impossible. llama.cpp's JSON-schema to GBNF conversion handles enums [S]. Hallucinated ids become impossible; the failure mode becomes "picks the wrong candidate", which the validator and state-verifying executor can catch for the cases where the user's intent is checkable.
3. **Deterministic first pass** removes the bulk of traffic from the LLM entirely. Our expectation (to be measured) is that most home-control utterances are exact or near-exact name matches.
4. **Prefix caching** for the fixed system prompt and tool schema, so only the K candidates and the utterance need prefill [U: verify llama.cpp behavior with grammars].

### Does fine-tuning help versus prompting + retrieval + grammar?
- **What fine-tuning improves:** (a) format reliability at small sizes (FunctionGemma's 58% to 85% on Mobile Actions is the clearest public number, although it is for a 270M model on a different task [S]); (b) shorter prompts, because the schema and instructions are in the weights, which saves prefill tokens; (c) behaviours such as asking a clarification or refusing; (d) non-English phrasing.
- **What it does not improve:** knowledge of the user's entities, which change constantly. A model that memorised entity names would be stale after any rename. It also does not remove the need for retrieval.
- **With grammar-constrained decoding, format reliability is mostly solved without training.** The remaining error is semantic: choosing the right candidate and arguments. A generic 4B instruct model with 5 to 10 candidates in the prompt is plausibly already adequate on that task [U, a hypothesis to test, not a result]. Fine-tuning's marginal gain after retrieval and grammar is probably small for 3 to 4B models and larger for 0.5 to 2B models.
- **Where fine-tuning could genuinely matter:** a 1B-class model that must fit when a customer has little RAM; very low latency (shorter prompts); and multilingual use.
- **Risk to name:** fine-tuned small models can over-fit to the training distribution (the Home-LLM score above is in-distribution) and regress general chat. Mixing in general chat data (Home-3B-v3 mixed in Cleaned Alpaca [S]) mitigates but adds a second objective to maintain.

**Conclusion:** if the LLM only sees a handful of retrieved candidates and its output is grammar-constrained, fine-tuning is not needed for correctness at the 3 to 4B level and is optional at the 0.5 to 2B level. Prove or disprove that with the eval before spending effort.

## 5. Vision (doorbell / camera snapshot description)

- **It is feasible, but not "free" alongside the text model.** The only Orin Nano numbers found are for a 2B VLM (Cosmos-Reason2-2B, Qwen3-VL-2B based): llama.cpp image TTFT about **114 ms steady state, first image request 0.5 to 3 s**; TensorRT Edge-LLM 420 ms image TTFT at 4.3 GB; vLLM 6.9 GB peak [V] (https://github.com/hokwangchoi/jetson-orin-nano-benchmarks). Those use a stock test image at small resolution with only 64 output tokens. A real 1080p snapshot at higher image-token counts, plus a 60 to 100 token caption at ~38 tok/s, would take roughly **3 to 6 s** [U, extrapolated]. Acceptable for "who is at the door" notifications; not for conversational turn latency.
- **Models:** Gemma 4 E2B (image in, Apache 2.0; one model for text and vision, NVIDIA names it the Orin Nano variant [V]); Qwen3.5-2B or 4B (multimodal, Apache 2.0 [S]); Qwen3-VL-2B (Apache 2.0 [S]); SmolVLM2-256M/500M (Apache 2.0, about 0.3 to 1 GB, weakest quality) [S]; Moondream 2 (Apache 2.0 [S], no Orin numbers found). Quality for security-style descriptions (person, package, vehicle, animal) at 2B is plausible but unmeasured by us [U].
- **Architecture advice:**
  - Use a **single multimodal model if the chosen text model has vision** (Gemma 4 E2B or Qwen3.5-2B/4B in one llama.cpp process with mmproj). That avoids a second resident model. Check actual added RSS for the vision projector and image tokens [U].
  - Otherwise run vision as a **separate on-demand process** (load, describe, unload), serialised with the text model, so the 4 GB budget is never exceeded. Cold load costs seconds, so keep it for event-driven snapshots, not chat.
  - **Do not fine-tune for vision.** There is no good HA-specific caption dataset and generic captioners are good enough. Prompt it (and constrain output to a short JSON of `person/package/vehicle/animal`) instead.
  - Cheaper alternative worth evaluating: a YOLOv8n-class detector on the Jetson (about 4 ms at INT8 TensorRT [V], same GitHub benchmark) for "person / package / car" detection, with the VLM called only when a richer description is wanted.

## 6. Verdict and plan

### Comparison
| Option | Accuracy / safety | Latency on Orin Nano | Scales with entity count | Effort / maintenance | Licence risk | Verdict |
|---|---|---|---|---|---|---|
| **(a) Deterministic + generic small LLM fallback** (Apache base, top-K, grammar) | High for resolved commands (deterministic); LLM residue depends on eval | Best: LLM seldom called; short prompts when it is | Yes, K is constant | Lowest | None with Apache bases | **Start here.** |
| **(b) Fine-tuned LLM as main path** | Self-reported high accuracy in-distribution only; no independent evidence of real-home robustness; no state verification of intent unless we add it anyway | Every request pays LLM cost (seconds) | Only with retrieval, i.e. the same machinery as (a), so the fine-tune adds little | Highest: data generator, retrains per base model, regressions, multilingual | Home-LLM bases carry Gemma / Llama / non-commercial terms | **Reject.** |
| **(c) Fine-tuned LLM as fallback inside the pipeline** | At most a few points over (a) if the eval shows a format/semantic gap; mainly valuable for 0.5 to 2B models | Same as (a), possibly shorter prompts | Yes | Medium | None if Apache base and MIT/CC data | **Adopt later, gated by evidence.** |

### Concrete plan
1. **Weeks 1 to 2: eval first.** Extend `src/fortify_eval` with synthetic homes (20/100/300/1000 entities), utterance sets (from HA intents templates, Home-Assistant-Requests-V2 seeds, hand-written adversarial cases), and metrics from section 3. Record the resolver-only baseline.
2. **Weeks 2 to 3: hardware baseline.** On a real Orin Nano (MAXN_SUPER, NVMe): `llama-bench` for Gemma 4 E2B Q4_K_S, Qwen3.5-4B Q4_K_M, Qwen3.5-2B Q4_K_M at prompt sizes 256 / 1k / 2k / 4k; record prefill tok/s, decode tok/s, RSS, KV size, grammar overhead, and whether prefix caching survives grammar-constrained requests. Include a 20-image camera test.
3. **Weeks 3 to 4: ship (a).** Retrieval top-K with enum-constrained tool schema, validator, state-verifying executor, per-tier model default (2B for low RAM, 4B for >=6 GB free).
4. **Decision gate:** if generic Apache models reach the target (e.g. at or above 95% exact-call match and 0% invalid entity on the held-out set with K=8, p95 under 3 s on Orin Nano), do **not** fine-tune; spend the effort on resolver coverage instead. If they miss, proceed to 5.
5. **Weeks 5 to 8 (conditional): fine-tune.** QLoRA on Qwen3.5-2B or Gemma 4 E2B with 50k to 100k synthetic examples generated from entity graphs, the prompt format used in production (K candidates), hard negatives (near-duplicate names), clarifications and refusals, plus a slice of general chat. Cost about US$50 to 300 compute plus engineer time. Publish adapter and eval results under Apache 2.0.
6. **Vision (parallel, low priority):** prototype on-demand snapshot description with the same multimodal model; fall back to a separate SmolVLM2/Qwen3-VL-2B process if memory is tight.

### Risks
- Memory: at 4B plus a vision projector the 4 GB budget is exceeded; the 8 GB board is also shared with HA, whose real usage varies [U]. Mitigation: tiered default models and an unload policy.
- JetPack fragmentation: TensorRT Edge-LLM needs JetPack 7.2 while many users are on 6.x [V]; llama.cpp sidesteps this.
- Gemma 4 and Qwen3.5 are new; llama.cpp support and chat-template/tool-parser bugs may exist on Orin (NVIDIA already notes audio and Ollama problems for Gemma 4 [V]).
- Model churn: a fine-tune must be redone for each base model.
- Overfitting to synthetic data (Home-LLM's 97% is in-distribution [V]) and quantization eroding gains.
- Safety: any model-initiated action on locks, garage doors, alarms must go through confirmation; the deterministic validator should enforce this regardless of model.
- Training-data licences: HA intents data licence not verified here [U]; avoid distilling from restricted teacher outputs (Gemma-terms synthetic-data clause [V]).

### What to measure first (priority order)
1. Share of real utterances the deterministic resolver fully handles (sets the LLM's workload).
2. Generic Gemma 4 E2B vs Qwen3.5-2B/4B tool-call accuracy at K=5, 8, 12, with and without grammar, on the eval set.
3. Orin Nano prefill/decode/RSS at 256 to 4k tokens, and the cache-hit behaviour.
4. Error taxonomy of the failures: wrong candidate vs bad args vs format vs should-have-asked. Only format and "should have asked" errors justify fine-tuning.
5. Vision snapshot latency and RSS delta on Orin Nano.

## Source list
- Home-LLM repo: https://github.com/acon96/home-llm
- Dataset: https://huggingface.co/datasets/acon96/Home-Assistant-Requests-V2/blob/main/README.md
- Model cards: https://huggingface.co/acon96/Home-3B-v3-GGUF , https://huggingface.co/acon96/Home-Llama-3.2-3B , https://huggingface.co/acon96/Home-FunctionGemma-270m
- FunctionGemma: https://ai.google.dev/gemma/docs/functiongemma , https://blog.google/technology/developers/functiongemma/
- Gemma terms: https://ai.google.dev/gemma/terms
- Gemma 4: https://the-decoder.com/?p=33933 , https://www.jetson-ai-lab.com/tutorials/gemma4-on-jetson/ , https://forums.developer.nvidia.com/t/gemma4-e4b-on-jetson-orin-nano-fails-due-to-cuda-out-of-memory-issue/365641
- Qwen3.5: https://artificialanalysis.ai/articles/qwen3-5-small-models , https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF
- TensorRT Edge-LLM: https://www.jetson-ai-lab.com/tutorials/tensorrt-edge-llm
- Jetson benchmarks: https://github.com/hokwangchoi/jetson-orin-nano-benchmarks , https://forums.developer.nvidia.com/t/jetson-orin-nano-super-dev-kit-performance/318719 , https://forums.developer.nvidia.com/t/gemma-3-and-gemma-3n-on-jetson-orin-nano-super/337513
- HomeBench: https://arxiv.org/abs/2505.19628
- llama.cpp grammars: https://www.mintlify.com/ggml-org/llama.cpp/advanced/grammars , https://github.com/ggml-org/llama.cpp/issues/25895
- HA local-LLM context experience: https://github.com/skye-harris/hass_local_openai_llm , https://forum.level1techs.com/t/home-assistant-exploration-of-fully-local-voice-assist/229801
- SmolLM3: https://huggingface.co/blog/smollm3
- SmolVLM2 / Moondream: https://www.promptlayer.com/models/smolvlm2-500m-video-instruct , https://playground.roboflow.com/models/compare/moondream-2-vs-smolvlm2 , https://moondream.ai/photon
