#!/usr/bin/env python3
"""Flag syntax that the Mac accepts and the container rejects.

The image runs Python 3.11; this machine runs 3.14. PEP 701 relaxed f-strings in
3.12, so two things parse here and are SyntaxErrors there:

  · a backslash anywhere inside an f-string expression
  · an f-string expression quoted with the same character as the f-string itself

Both shipped once. The failure only appeared when a client report died at run
time in the container, which is the worst place to find a syntax error.

Run over the staged module set before building. Exits non-zero on a finding.
"""

import ast
import pathlib
import sys


def check(path):
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"{path.name}:{exc.lineno}: does not parse — {exc.msg}"]

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        whole = ast.get_source_segment(src, node) or ""
        quote = '"""' if whole.count('"""') else ("'''" if whole.count("'''") else
                                                  ('"' if '"' in whole[:3] else "'"))
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            expr = ast.get_source_segment(src, part.value) or ""
            if "\\" in expr:
                out.append(f"{path.name}:{part.lineno}: backslash inside an f-string "
                           f"expression — SyntaxError on Python 3.11: {expr.strip()[:70]}")
            elif len(quote) == 1 and quote in expr:
                out.append(f"{path.name}:{part.lineno}: f-string expression reuses the "
                           f"outer {quote} quote — SyntaxError on Python 3.11: "
                           f"{expr.strip()[:70]}")
    return out


def main():
    targets = [pathlib.Path(a) for a in sys.argv[1:]] or \
        sorted(pathlib.Path(".").glob("*.py"))
    files = []
    for t in targets:
        files += sorted(t.glob("*.py")) if t.is_dir() else [t]

    findings = [f for p in files for f in check(p)]
    for f in findings:
        print("FAIL", f)
    print(f"{len(files)} file(s) checked for Python 3.11 compatibility — "
          + ("clean" if not findings else f"{len(findings)} finding(s)"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
