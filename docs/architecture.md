# Architecture

```mermaid
flowchart TD
    A[Target .py file] --> B[Read source]
    B --> C[Python AST syntax validation]
    C --> D[Bounded subprocess execution]
    D -->|exit 0| E[Success; optionally replace target]
    D -->|stderr/traceback| F[Prompt Groq with source and failure]
    F --> G[Extract complete fixed_code block]
    G --> C
    G -->|invalid response| H[Stop safely]
    D --> I{Maximum 3 attempts?}
    I -->|yes| H
    E --> J[.bak backup when --apply]
```

## Safety boundaries

- The target is limited to a Python file.
- The subprocess runs with the current Python interpreter, in the target directory.
- Output is captured and a timeout prevents an infinite loop.
- The default is dry-run: the original file is not replaced.
- `--apply` creates `target.py.bak` before replacing the target after success.
- The model must return a complete source file inside `<fixed_code>` markers.
- Every proposed version is parsed with `ast` before execution.
- The loop stops after at most three attempts.

This is a portfolio learning tool, not a sandbox. Do not run untrusted code or point it at production repositories without isolation, review, and stronger OS-level controls.
