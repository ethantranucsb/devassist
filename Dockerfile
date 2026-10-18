# devassist with its locked dependencies, run as a non-root user (docs/decisions.md D17).
# Build:  docker build -t devassist .
# Run:    docker run --rm -e ANTHROPIC_API_KEY -v "${PWD}:/work:ro" devassist explain /work/path/to/file.py
# The API key is passed at run time and never built into the image.

FROM python:3.13.9-slim

# uv, the same version that wrote uv.lock, from its official image.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv

# Use the image's Python rather than downloading one, and precompile for a faster start.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependencies first, so a change to the source doesn't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

RUN useradd --create-home devassist
USER devassist

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /work
ENTRYPOINT ["devassist"]
CMD ["--help"]
