# Source provenance and notices

This repository vendors locally adapted snapshots. Original copyright and license notices are retained in the source files and package directories. No new license overrides third-party terms.

- Hydrone: https://github.com/ricardoGrando/hydrone_deep_rl_icra — see `src/hydrone_deep_rl_icra/LICENSE` (MIT) and nested package notices.
- UUV Simulator: see the recorded upstream URL in `provenance/source-repositories.json`, `src/uuv_simulator/LICENSE`, and individual file/package notices.
- RotorS and mav_comm: upstream URLs/commits are recorded in `provenance/source-repositories.json`; retain each package's `package.xml` license declarations and source header notices.
- ROS archive key and the small catkin-pkg compatibility Debian package are distributed solely as dependency installation inputs; the installer verifies the Debian package SHA256 and uses signed APT repositories.

Original README files are retained for attribution and historical context. Follow the root README and `tools/server/README.md` for this snapshot's deployment instructions.
