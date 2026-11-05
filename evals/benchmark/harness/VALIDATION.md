# Parent validation

The parent reviewed and ran the provider loop locally. These are deterministic, authored contract controls. They do not measure any agent's CAD quality, manufacturing compliance or false-verified rate.

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --project evals/benchmark/harness python -m pytest -p no:cacheprovider evals/benchmark/tests/test_agent_loop.py evals/benchmark/tests/test_provider_sdk.py -q
```

The initial run exposed incorrect arithmetic in a budget-stop fixture and inconsistent error wording; both are fixed. Further parent controls exercise descriptor-relative artifact copying, a symlink swap, hard links, byte limits, fresh output directories, preserved measurement vectors and retained requested images. SDK tests serialize a multiturn function/image exchange through the exact pinned OpenAI and Anthropic SDKs using in-memory HTTP transports. No network request or paid model call is made.

The result includes the repetition, ablation arm, task-order seed and loop wall time. The returned result and terminal trace event use the same frozen terminal timing value. Loop timing excludes backend provisioning and teardown; a composed benchmark must also record total trial wall time.

Remaining integration work: bind the external preflight evidence to each actual backend/job/policy; compose a whole edit chain with a shared budget and preserved conversation; stage immutable artifacts after stopping solver descendants; run an independent artifact oracle; and retain a complete host manifest. The single-stage CLI alone does not satisfy those requirements. Provider/model availability and real-service image support have not been measured. No real-agent sample or release claim is made.

Independent review found that cooperative timeouts could accept a late finish and self-declared live labels could mark a mock run sample-eligible. Parent regression controls now reject late finishes while retaining observed usage. Sample eligibility always remains false pending authenticated host manifests; local labels produce only provisional eligibility. Custom SDK clients default to custom origin. Hard interruption of blocking code still requires the pending process coordinator.
