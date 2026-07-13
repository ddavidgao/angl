<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/angl-mark-dark.svg">
    <img alt="Angl" src="assets/angl-mark-light.svg" width="88">
  </picture>
</p>

# Angl

**Behavior is the source code.**

Angl is an experiment in contract-checked code regeneration. You keep a
readable chapter of product behavior and executable examples. A configured
compiler agent produces ordinary code. Angl accepts that generated edition only
when a black-box judge passes every example.

```text
contract pins, intent guides
```

The English guides implementation. The executable examples decide correctness.

## Install

```bash
npm install -g angl-cli
angl setup codex
angl doctor --provider-smoke
```

`angl setup` saves the provider once for the machine. Available providers are
`codex`, `claude-code`, and `ollama`.

The CLI is also available through pnpm and Bun:

```bash
pnpm add -g angl-cli
bun add -g angl-cli
```

See [Getting started](docs/GETTING_STARTED.md) for the full first-project flow.

## First Project

```bash
angl new hello-angl
cd hello-angl
angl check
angl build
```

`check` parses and lints chapters without calling a model. `build` compiles
dependencies first, generates an edition into `build/`, and judges every
example. `verify` re-runs the judge against an existing build without a model
call.

## Source

An Angl chapter is structured Markdown that reads like a short product
specification:

````angl
# Fetch Price

> Boundary: `fetch_price(url: string) -> number`

## Behavior

Fetch JSON from the URL and return its `price` field. If the field is absent,
fail with an error that mentions `price`.

## Examples

### The service returns a price

Fixture `http_fixture`:
```json
{"price": 19.99}
```

Returns:
```json
19.99
```

### The service omits the price

Fixture `http_fixture`:
```json
{}
```

Fails with error containing: price
````

The required surface is a title, typed boundary, `## Behavior`, and at least
one executable example. Purpose and dependencies are optional. See the full
[source schema](docs/ANGL_SCHEMA.md).

## Targets

`.angl` is not Python source. The current toolchain is implemented in Python,
but a chapter can ask the compiler for a supported target:

````angl
> Runs as: `node`
````

Supported targets today are `python`, `node`, `typescript`, `ruby`, `go`,
`rust`, `assembly`, and `bundle`. Each chapter has one target. A program can
combine chapters with different targets through declared `> Uses:` boundaries.
The judge evaluates their observable JSON behavior through generated adapters,
not by importing the generated implementation into the toolchain.

## Workflow

```text
edit a .angl chapter
angl check
angl build
angl verify
inspect build/ only when needed
commit the chapter and its examples
```

Generated editions are build output. They are not source of truth and should
not be committed. Each build writes a manifest beside the artifact with the
provider, target, attempts, and judge result.

## Scope And Safety

Angl is alpha software for local, trusted projects. The judge runs generated
code as a subprocess with an allowlisted environment, but it is not a complete
operating-system sandbox. Do not compile untrusted chapters.

Angl proves only the behavior its examples describe. Add an example whenever a
behavior must remain true after regeneration. Behavior outside the contract is
unspecified.

## Develop Angl

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
python3 tests/test_parse.py
python3 tests/test_verify.py
python3 tests/test_compile.py
```

The product CLI is installable without cloning this repository. The local
commands above are only for changing the Angl toolchain itself.

## Documentation

- [Getting started](docs/GETTING_STARTED.md)
- [Source schema](docs/ANGL_SCHEMA.md)
