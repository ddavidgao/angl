"""Angl end-to-end runner: load program -> compile (deps first) -> verify each
unit against its own contract -> report.

Usage:  python -m angl.run specs/checkout.angl

A "program" is the target spec plus its transitive `# uses:` dependencies. Each
unit is compiled and judged independently against its OWN contract. The system
is sound because every interface boundary is contract-verified, so a unit can
call another without knowing how (or in what language) it is implemented.

P2: compile calls a real model (see compile.py / ops.local.md for ANGL_MODEL_URL).
P6: compile_until_green is the repair loop — on a judge failure, the failure
detail is fed back into a fresh attempt (up to max_attempts), instead of a
bare recompile just being a reroll.
"""
import os
import sys

from .compile import ProviderError, compile_spec
from .parse import parse
from .verify import verify_spec

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(REPO_ROOT, "build")


def load_program(spec_path):
    """Load the target spec and every unit it transitively `uses`."""
    specs_dir = os.path.dirname(os.path.abspath(spec_path))
    units = {}

    def load(path, requested_as=None):
        if not os.path.exists(path):
            dep = requested_as or os.path.splitext(os.path.basename(path))[0]
            raise ValueError(f"missing # uses dependency: {dep} ({path})")
        with open(path) as f:
            spec = parse(f.read())
        # Keep source location as runner metadata. The compiler never treats it
        # as language input, but project-level commands need it for linting.
        spec["_source_path"] = os.path.abspath(path)
        units[spec["name"]] = spec
        for dep in spec["uses"]:
            if dep not in units:
                load(_dependency_path(specs_dir, dep), dep)

    load(spec_path)
    return units


def _dependency_path(specs_dir, dependency):
    """Resolve a chapter name to the readable file slug authors naturally use.

    Interfaces remain Python-style identifiers, so dependencies use underscores.
    Chapter files, however, are often titled and saved with hyphens. Support
    both without making authors choose between a readable filename and a valid
    interface name.
    """
    candidates = [
        os.path.join(specs_dir, dependency + ".angl"),
        os.path.join(specs_dir, dependency.replace("_", "-") + ".angl"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def compile_until_green(spec, build_dir, units=None, max_attempts=3):
    """Compile, verify, and if the judge finds real failures, feed exactly
    what failed back into a fresh attempt — up to max_attempts times. A bare
    recompile with no feedback is a reroll; this is the actual repair loop.

    Also retries (with the raised error as feedback) if compile_spec itself
    raises — e.g. the model produced invalid syntax or never bound the
    interface name — rather than crashing the whole run on one bad attempt.

    Returns (build, result, attempts). If every attempt fails at compile time,
    the result is a normal red report rather than an exception, so callers can
    still show which unit failed and continue evaluating the rest of a program.
    """
    repair = None
    build = result = None
    for attempt in range(1, max_attempts + 1):
        try:
            build = compile_spec(spec, build_dir, units, repair=repair)
        except ProviderError as e:
            return _compile_error_result(spec, e), _compile_error_report(spec, e), attempt
        except RuntimeError as e:
            if attempt == max_attempts:
                return _compile_error_result(spec, e), _compile_error_report(spec, e), attempt
            repair = {"prior_code": None, "failures": [f"compile failed: {e}"]}
            continue

        result = verify_spec(spec, build)
        if result["passed"] == result["total"] or attempt == max_attempts:
            return build, result, attempt

        with open(build["artifact"]) as f:
            prior_code = f.read()
        failures = [f"{r['case']} -- {r['detail']}"
                    for r in result["results"] if not r["pass"]]
        repair = {"prior_code": prior_code, "failures": failures}

    return build, result, max_attempts


def _compile_error_result(spec, error):
    return {
        "implementation": None,
        "judge_adapter": None,
        "host_adapter": None,
        "artifact": None,
        "shim": None,
        "func": spec["func"],
        "target": spec.get("target", "python"),
        "proxy": None,
        "compile_error": str(error),
    }


def _compile_error_report(spec, error):
    detail = f"compile failed: {error}"
    return {
        "passed": 0,
        "total": len(spec["cases"]),
        "results": [
            {"pass": False, "case": case["raw"], "detail": detail}
            for case in spec["cases"]
        ],
    }


def topo_order(units):
    """Return unit names with dependencies before the units that use them."""
    order, visiting, visited = [], [], set()

    def visit(name):
        if name not in units:
            raise ValueError(f"missing # uses dependency: {name}")
        if name in visited:
            return
        if name in visiting:
            cycle = visiting[visiting.index(name):] + [name]
            raise ValueError(f"cyclic # uses dependency: {' -> '.join(cycle)}")
        visiting.append(name)
        for dep in units[name]["uses"]:
            visit(dep)
        visiting.pop()
        visited.add(name)
        order.append(name)

    for name in units:
        visit(name)
    return order


def main(argv):
    if not argv:
        print("usage: python -m angl.run <spec.angl>")
        return 2

    units = load_program(argv[0])
    order = topo_order(units)
    max_attempts = int(os.environ.get("ANGL_MAX_ATTEMPTS", "3"))
    print(f"program: {len(units)} unit(s)  [{' -> '.join(order)}]")

    all_green = True
    for name in order:
        spec = units[name]
        # deps compiled first; repair loop feeds judge failures back in
        build, report, attempts = compile_until_green(
            spec, BUILD_DIR, units, max_attempts=max_attempts
        )
        all_green = all_green and report["passed"] == report["total"]

        dep_note = f"  (uses: {', '.join(spec['uses'])})" if spec["uses"] else ""
        attempt_note = f"  [{attempts} attempts]" if attempts > 1 else ""
        print(f"\n[{name}]  {report['passed']}/{report['total']} cases green"
              f"{dep_note}{attempt_note}")
        for r in report["results"]:
            mark = "PASS" if r["pass"] else "FAIL"
            line = f"  [{mark}] {r['case']}"
            if not r["pass"]:
                line += f"  -- {r['detail']}"
            print(line)

    print(f"\n{'ALL GREEN' if all_green else 'FAILURES'}: {len(order)} unit(s)")
    return 0 if all_green else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
