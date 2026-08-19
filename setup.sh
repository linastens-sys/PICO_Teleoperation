#!/usr/bin/env bash
# One-time setup for a fresh dev machine. Run from this repo's root.
#
# Fetches only what's actually used at runtime:
#   - TWIST2 (deploy_real/ scripts + G1 assets/checkpoints) -- shallow clone,
#     it's already small (~107MB).
#   - GMR (the retargeting engine) -- partial + sparse clone, since its own
#     repo bundles ~1.2GB of OTHER robots' assets we don't need; this pulls
#     down only the engine code + the G1-specific assets (~54MB total).
#   - The Pico pybind SDK -- shallow clone + local build against the PC
#     service you install separately (see README.md, one-time §1a).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Cloning TWIST2 (shallow)..."
if [ ! -d TWIST2 ]; then
  git clone --depth 1 https://github.com/amazon-far/TWIST2.git
fi

echo "==> Cloning GMR (partial + sparse: engine + G1 assets only)..."
if [ ! -d GMR ]; then
  git clone --filter=blob:none --sparse --depth 1 https://github.com/YanjieZe/GMR.git
  (
    cd GMR
    git sparse-checkout set general_motion_retargeting scripts setup.py assets/unitree_g1
  )
fi

echo "==> Cloning the Pico pybind SDK (shallow)..."
if [ ! -d XRoboToolkit-PC-Service-Pybind ]; then
  git clone --depth 1 https://github.com/YanjieZe/XRoboToolkit-PC-Service-Pybind.git
fi

echo "==> Installing Python deps (uv)..."
uv sync

echo "==> Installing GMR (--no-deps: its own setup.py pulls a smplx git dep"
echo "    we don't need for the xrobot->g1 path; leaf deps already in pyproject.toml)..."
uv pip install --no-deps -e GMR/

echo "==> Building the Pico pybind SDK..."
echo "    Requires the roboticsservice .deb already installed (README.md §1a)"
echo "    -- it ships the libPXREARobotSDK.so this build links against."
export pybind11_DIR="$(uv run python -c 'import pybind11; print(pybind11.get_cmake_dir())')"
uv pip install --no-build-isolation --force-reinstall --no-deps ./XRoboToolkit-PC-Service-Pybind

echo "==> Done. Verify with:"
echo "    uv run python XRoboToolkit-PC-Service-Pybind/examples/example.py"
