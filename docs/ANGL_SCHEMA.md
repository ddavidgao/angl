# Angl Source Schema

An `.angl` file is a structured Markdown document. Markdown is the carrier
format; Angl only treats a few lines as schema. The goal is that source reads
like a chapter in a product book while still giving the compiler and judge
stable machine anchors.

## Required

1. A chapter title or name.
   - Canonical: `# Normalize Event`
   - Legacy: `name normalize_event`

2. A typed boundary.
   - Canonical: ``> Boundary: `normalize_event(raw: object) -> object` ``
   - Legacy: `interface normalize_event(raw: object) -> object`

3. Compiler behavior.
   - Canonical: `## Behavior`
   - Legacy: `INTENT`
   - This is natural-language product behavior. It guides generation but never
     decides correctness.

4. At least one executable example.
   - Canonical: examples under `## Examples`
   - Legacy: `case:` lines under `CONTRACT`
   - These are the expectations the judge enforces. They decide correctness.

## Optional

1. Purpose.
   - `## Purpose`
   - Human documentation only. Not parsed as compiler intent.

2. Target language.
   - Canonical: ``> Runs as: `go` ``
   - Legacy: `target go`
   - Defaults to `python`.
   - Supported: `python`, `node`, `ruby`, `go`, `rust`, `typescript`,
     `bundle`, `assembly`.
   - `bundle` means the compiler agent emits a generated file bundle with a
     manifest, optional build commands, and a Python judge adapter. This is the
     generic path for native code, assembly, or multi-file outputs. The compiler
     does not know the domain or ABI.
   - `assembly` is a stricter bundle target. The compiler agent must emit a
     real `.s` file, compile it with local `clang` into a shared library, and
     expose a Python host adapter that calls that library with `ctypes`.

3. Dependencies.
   - Canonical: ``> Uses: `normalize_event`, `classify_severity` ``
   - Legacy: `uses normalize_event, classify_severity`

## Canonical Form

~~~md
# Normalize Event

> Boundary: `normalize_event(raw: object) -> object`
> Runs as: `python`

## Purpose

Explain why this unit exists for humans.

## Behavior

Explain what the compiler should try to implement.

## Examples

### A full raw event is provided

When a full raw event is provided, it returns canonical fields.

Input `raw`:
```json
{
  "event_id": "E-1",
  "service": " Payments "
}
```

Returns:
```json
{
  "event_id": "E-1",
  "service": "payments"
}
```
~~~

## Example Rules

Inputs are positional. Two `Input` blocks produce two function arguments in
order.

~~~md
Input `event`:
```json
{"fingerprint": "pay-500"}
```

Input `open_incidents`:
```json
[]
```
~~~

Return examples must contain JSON:

~~~md
Returns:
```json
{"ok": true}
```
~~~

Error examples use substring matching:

```md
Fails with error containing: service
```

## Fixtures

Fixtures create runtime dependencies for an example. The source still declares
only data; the toolchain owns setup and teardown.

`http_fixture` serves JSON from a temporary local HTTP server and contributes one
URL argument.

~~~md
Fixture `http_fixture`:
```json
{"price": 19.99}
```
~~~

`postgres_fixture` starts an isolated Postgres container, runs setup SQL, and
contributes one connection-info object.

~~~md
Fixture `postgres_fixture`:
```json
{
  "setup_sql": [
    "create table incidents (id text primary key, severity text not null)",
    "insert into incidents (id, severity) values ('INC-1', 'sev1')"
  ]
}
```
~~~

## Where Expectations Live

Product expectations live in the `.angl` file. Each executable example under
`## Examples` becomes a judge case for the generated edition.

Toolchain tests live in `tests/`. They protect Angl itself: parsing, linting,
formatting, compiling, repair prompts, generated adapters, black-box
verification, generated book views, and contract-strength regressions.

Generated editions should not grow their own hand-written test suites. They are
disposable outputs. If a behavior matters after regeneration, write it as an
`.angl` example so every future edition has to pass it.

Generated build output has three roles:

- `implementation`: the generated edition that executes the chapter behavior.
- `host_adapter`: optional glue for the host app's language.
- `judge_adapter`: hidden glue used by the verifier's black-box protocol.

Adapters are build plumbing, not source truth. The IDE should show them only
when the user explicitly asks to inspect generated output.

Angl can validate a generated edition completely against the written
expectations. It cannot prove behavior that the expectations never mention.
Treat behavior outside the examples as unspecified until a new example pins it.

## Compiler-Agent Workflow

Angl does not ask the author to fill the source with loops, helper names, or
language-specific APIs. The author writes the chapter. The compiler agent reads
the chapter plus dependency context, chooses an implementation strategy, and
produces a generated edition. Verification then decides whether that edition is
acceptable.

For composed programs, dependencies compile first and dependents compile after
them. This is the practical version of trickle-down coding: verified lower
chapters become stable context for higher chapters.

## Legacy Case Form

The compact form is still accepted:

```angl
name add
interface add(a: number, b: number) -> number

INTENT
Add two numbers.

CONTRACT
case: 1, 2 -> 3
case: 1, "x" -> !error contains "number"
```

## What Is Not Schema

Normal Markdown prose is not schema. Only these anchors are machine schema:

- `> Boundary:`
- `> Runs as:`
- `> Uses:`
- `## Behavior`
- `## Examples`
- `Input ...:`
- `Fixture ...:`
- `Returns:`
- `Fails with error containing:`
- legacy `case:`
