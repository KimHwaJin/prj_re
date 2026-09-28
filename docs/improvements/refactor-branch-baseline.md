# Independent refactor baseline

Created from the working files of feature/runtime-hardening, whose HEAD was `dad1d6c27e32e2aeb0a616bfeb8368cab1fd6e6b`.

- `feature/refactor-base` is an orphan branch: its initial commit has no parent.
- The original checkout and all its uncommitted changes remain in place.
- Current source, implementation/test scripts, and design/review Markdown are imported as the starting point, not counted as new refactor fixes.
- Private `.env`, machine-local `config.dev.yml`, workspace data, and raw load-test outputs are excluded. Original copies remain in the source checkout.
- Work branches derive from this root branch. First: `feature/refactor-bootstrap-config`.
- No remote branch has been pushed.

Tracked local LangGraph checkpoint pickles, Finder metadata, and notebook outputs from the old repository are also excluded from this new source baseline; originals remain untouched.
