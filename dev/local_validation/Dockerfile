# Local CPU/ROS validation only. This image does not reproduce the PPU runtime.
# Official OSRF-maintained ROS image, Ubuntu 20.04 (Focal), pinned by digest.
FROM ros:noetic-ros-base-focal@sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d AS upstream

FROM upstream AS base
ARG UBUNTU_APT_MIRROR=""

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GAZEBO_MODEL_DATABASE_URI="" \
    LIBGL_ALWAYS_SOFTWARE=1 \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    HYDRONE_LOCAL_VALIDATION=1

# Dependencies of the vendored catkin packages, including the original server
# installer's MAVROS dependency. Do not disable source-defined build targets.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    python3 -c 'import os; from pathlib import Path; from urllib.parse import urlsplit; mirror=os.environ.get("UBUNTU_APT_MIRROR", "").rstrip("/"); assert not mirror or (urlsplit(mirror).scheme == "https" and urlsplit(mirror).netloc and not urlsplit(mirror).query and not urlsplit(mirror).fragment); source=Path("/etc/apt/sources.list"); content=source.read_text(); source.write_text(content.replace("http://archive.ubuntu.com/ubuntu", mirror).replace("http://security.ubuntu.com/ubuntu", mirror) if mirror else content)' \
    && rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get -o Acquire::Retries=3 update \
    && for attempt in 1 2 3; do \
      apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
      build-essential ca-certificates cmake git git-lfs pkg-config \
      libboost-all-dev libeigen3-dev libgoogle-glog-dev libopencv-dev \
      libprotobuf-dev protobuf-compiler libyaml-cpp-dev liboctomap-dev \
      gazebo11 libgazebo11-dev xvfb xauth mesa-utils libgl1-mesa-dri \
      python3-dev python3-venv python3-pip python3-catkin-tools \
      python3-empy python3-numpy python3-scipy python3-matplotlib \
      python3-yaml python3-pil python3-pytest python3-rospkg python3-rosdep \
      ros-noetic-catkin ros-noetic-cmake-modules ros-noetic-cv-bridge \
      ros-noetic-gazebo-ros-pkgs ros-noetic-gazebo-ros-control \
      ros-noetic-image-transport ros-noetic-laser-geometry \
      ros-noetic-joint-state-publisher ros-noetic-robot-state-publisher \
      ros-noetic-joy ros-noetic-mavros ros-noetic-mavros-msgs ros-noetic-geographic-msgs \
      ros-noetic-octomap-msgs ros-noetic-octomap-ros \
      ros-noetic-rqt-gui ros-noetic-rqt-gui-py \
      ros-noetic-rostest ros-noetic-rosunit ros-noetic-tf ros-noetic-tf2-ros \
      ros-noetic-topic-tools ros-noetic-xacro && break; \
      test "$attempt" -lt 3 || exit 1; sleep 5; \
    done \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/local-validation \
    && /opt/local-validation/bin/python -m pip install --no-cache-dir \
      pip==24.3.1 numpy==1.24.4 scipy==1.10.1 Pillow==10.4.0 \
      PyYAML==6.0.1 pytest==7.4.4 \
    && /opt/local-validation/bin/python -c 'import importlib.util, sys; assert sys.version_info[:2] == (3, 8); assert importlib.util.find_spec("torch") is None' \
    && mkdir -p /opt/local-validation-info \
    && dpkg-query -W > /opt/local-validation-info/dpkg.tsv \
    && /opt/local-validation/bin/python -m pip freeze > /opt/local-validation-info/pip-base.txt

ENV PATH="/opt/local-validation/bin:${PATH}"
COPY entrypoint.bash /opt/hydrone-validation/entrypoint.bash
COPY ros_smoke.py /opt/hydrone-validation/ros_smoke.py
RUN chmod 755 /opt/hydrone-validation/entrypoint.bash
WORKDIR /workspace
ENTRYPOINT ["/opt/hydrone-validation/entrypoint.bash"]
CMD ["python3", "--version"]

# Explicit opt-in target for CPU network/unit tests. Never use this image or
# requirements file to provision a PPU server. No CUDA toolkit or drivers.
FROM base AS cpu-torch
COPY requirements-cpu.txt /opt/hydrone-validation/requirements-cpu.txt
RUN python3 -c 'import platform, sys; assert sys.version_info[:2] == (3, 8) and platform.machine() == "x86_64"' \
    && /usr/bin/python3 -m pip download --no-cache-dir --no-deps \
      --dest /tmp/cpu-wheels -r /opt/hydrone-validation/requirements-cpu.txt \
    && sha256sum /tmp/cpu-wheels/*.whl > /opt/local-validation-info/cpu-wheels.sha256 \
    && python3 -m pip install --no-index /tmp/cpu-wheels/*.whl \
    && python3 -c 'import torch; assert torch.__version__ == "2.4.1+cpu"; assert torch.version.cuda is None; assert not torch.cuda.is_available()' \
    && python3 -m pip freeze > /opt/local-validation-info/pip-cpu.txt \
    && rm -rf /tmp/cpu-wheels
ENV HYDRONE_LOCAL_CPU_TORCH=1

# A plain docker build remains torch-free; CPU torch requires --target cpu-torch.
FROM base AS default
