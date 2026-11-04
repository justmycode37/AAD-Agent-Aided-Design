# aiad-render

Owned by S3. Two jobs:

- `make_scene()`: tessellates a built model into the `tcv-5` scene the viewer loads (`CONTRACTS.md` section 7).
- `render()`: images for the agent itself (`CONTRACTS.md` section 9).

Both run inside kernel workers. `import aiad_render` stays cheap; kernel and renderer imports happen on first use.
