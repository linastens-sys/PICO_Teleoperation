#!/usr/bin/env bash
# One-time setup for a fresh dev machine. Run from this repo's root.
#
# Everything this pipeline needs is already IN this repo (TWIST2/, GMR/,
# Pico-Pybind/ -- the adapted, deployment-only slices of each, with the
# IsaacGym-dependent training code left out entirely). Nothing gets cloned
# from source here; this just installs deps and builds the one native
# extension (the Pico pybind SDK) against your machine's installed PC
# service library.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing Python deps (uv)..."
uv sync

echo "==> Installing GMR (--no-deps: its own setup.py pulls a smplx git dep"
echo "    we don't need for the xrobot->g1 path; leaf deps already in pyproject.toml)..."
uv pip install --no-deps -e GMR/

echo "==> Staging the Pico SDK library from the installed PC service..."
echo "    Requires the roboticsservice .deb already installed (README.md §1a)."
PXREA_LIB=/opt/apps/roboticsservice/SDK/x64/libPXREARobotSDK.so
if [ ! -f "$PXREA_LIB" ]; then
  echo "ERROR: $PXREA_LIB not found -- install the roboticsservice .deb first."
  exit 1
fi
cp "$PXREA_LIB" Pico-Pybind/lib/

echo "==> Building the Pico pybind SDK..."
export pybind11_DIR="$(uv run python -c 'import pybind11; print(pybind11.get_cmake_dir())')"
uv pip install --no-build-isolation --force-reinstall --no-deps ./Pico-Pybind

echo "==> Done. Verify with:"
echo "    uv run python Pico-Pybind/examples/example.py"
