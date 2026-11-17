# Contributing to Snap Ninja

Use Python 3.10 or later. Create a virtual environment, install `requirements.txt`,
and install FFmpeg and FFprobe on PATH. NVIDIA support in `requirements-gpu.txt`
is optional. Run `python app.py` and open http://127.0.0.1:7860.

Keep changes focused. Preserve the `clipper` package, job format, browser storage
keys, and desktop singleton identifier unless a migration is included. Those names
are compatibility details; the product name is Snap Ninja.

Before submitting a change:

```text
python -m unittest discover -s tests -v
python -m compileall -q app.py desktop.py cli.py clipper tools tests
node --check static/app.js
node --check static/setup.js
node --check static/output.js
node --check static/youtube-guide.js
node --test tests/youtube-guide.test.js
```

Check all four modes, keyboard navigation, first-run setup, and a narrow screen.
Use disposable test media. Never run deletion tests against real jobs. Never
include `.env`, OAuth files, tokens, logs, feedback, recordings or private media.
Browser writes to the local API require `X-Snap-Ninja: 1` and the same origin.

Snap Ninja source is licensed under MIT. Preserve third-party notices listed in
THIRD_PARTY_NOTICES.md. Contributions should include the relevant documentation
and checks; do not bundle downloaded speech or language models with a change.
