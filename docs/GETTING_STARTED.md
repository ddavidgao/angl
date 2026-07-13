# Getting Started

This guide goes from an empty terminal to a verified generated edition.

## Prerequisites

Install Node.js 18 or newer and Python 3.9 or newer. Python is required by the
current Angl CLI implementation. It does not determine the language generated
for a chapter.

Choose one compiler provider:

- [Codex CLI](https://developers.openai.com/codex/cli/) authenticated with your
  OpenAI account.
- [Claude Code](https://code.claude.com/docs/en/quickstart) authenticated with
  your Anthropic account.
- [Ollama](https://ollama.com/) running a local model.

## 1. Install Angl

```bash
npm install -g angl-cli
angl --version
```

Equivalent global installation commands:

```bash
pnpm add -g angl-cli
bun add -g angl-cli
```

The npm package installs the Angl Python toolchain into a private directory
under `~/.angl` the first time it runs. No repository clone is required.

## 2. Choose A Compiler Once

Save the provider once, then verify its local wiring and authentication:

```bash
angl setup codex
angl doctor --provider-smoke
```

For Claude Code:

```bash
angl setup claude-code
angl doctor --provider-smoke
```

For Ollama:

```bash
ollama pull qwen2.5-coder:14b
ollama serve
angl setup ollama --model qwen2.5-coder:14b --url http://127.0.0.1:11434
angl doctor --provider-smoke
```

Provider configuration is local toolchain configuration. It is not part of the
`.angl` language or a project commit. Use `angl config` to inspect it later.

## 3. Create A Project

```bash
angl new hello-angl
cd hello-angl
```

The starter project contains:

```text
hello-angl/
  angl.project
  specs/
    greet.angl
  .vscode/
    tasks.json
  .gitignore
```

`specs/greet.angl` is the source you maintain. It has a typed boundary,
natural-language behavior, and executable JSON examples.

## 4. Check, Build, Verify

```bash
angl check
angl build
angl verify
```

`angl check` parses and lints every chapter below `specs/`; it makes no model
call. `angl build` compiles dependencies first, writes generated output into
`build/`, and runs the judge against all examples. `angl verify` judges the
existing output again without generating new code.

To target one chapter or a directory explicitly:

```bash
angl check specs/greet.angl
angl build specs/greet.angl
angl verify specs
```

## 5. Change Behavior

Edit the English behavior and its examples together, then repeat the loop:

```text
edit .angl
angl check
angl build
angl verify
```

Examples are the acceptance contract. If a behavior matters, write an example
for it. Untested behavior is unspecified.

## Inspect Generated Output

Generated code lives in `build/`. It is disposable build output, not the source
you commit. Look there when you need to review or debug an edition:

```bash
ls build
```

Each artifact has a manifest beside it with provider, target, attempt, and judge
metadata.

To render the maintained chapter as a readable local view:

```bash
angl preview --serve --view chapter
```

## Targets

The current Angl toolchain is implemented in Python, but chapter output does not
have to be Python. Add an optional target anchor to a chapter:

````angl
> Runs as: `node`
````

Supported targets are `python`, `node`, `typescript`, `ruby`, `go`, `rust`,
`assembly`, and `bundle`. See [the source schema](ANGL_SCHEMA.md) for format
details and fixtures.

## Safety

Use Angl on chapters you trust. The local judge runs generated code in a
subprocess with an allowlisted environment, but it is not a complete OS sandbox.
Do not compile untrusted `.angl` files.
