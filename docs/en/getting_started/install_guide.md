# msAgent Installation Guide

## 1. Installation Instructions

This guide is intended for users who are new to MindStudio-Agent and helps you complete the msAgent installation.

Two installation methods are currently supported: [Online Installation](#31-online-installation) and [Source Installation](#32-source-installation).

## 2. Environment Requirements

- `Python >= 3.11`

- `glibc >= 2.34`

   Required to satisfy the binary dependency of `trace_processor` in `msprof-mcp`. Recommended operating systems: `Ubuntu >= 21.10` and `openEuler >= 21.09`. For other operating systems, verify the requirements yourself.

- Before using this tool, you must install CANN. For details, see [CANN Quick Installation](https://www.hiascend.com/cann/download) guide to install the Ascend NPU driver and CANN software (including the Toolkit and ops packages), and configure the environment variables.

## 3. Installation Methods

### 3.1 Online Installation

Your device must have internet access. You can download and install the tool with the following command:

```shell
pip install mindstudio-agent
```

After the installation, run the following command. If the msAgent version is displayed, the installation is successful.

```shell
msagent --version
```

### 3.2 Source Installation

#### 3.2.1 Environment Preparation

Source compilation consistently uses the MindStudio standard build environment.

- For daily development or use of the released image, refer to [MindStudio Tool Development Environment Installation Guide](https://gitcode.com/Ascend/msot/blob/master/docs/en/common/dev_env_setup.md).
- When you need to reproduce the environment from a base operating system, run source build verification, or run unit test verification, you must refer to [MindStudio Unified Build Image Guide](https://gitcode.com/Ascend/msot/blob/master/docs/en/common/docker_image_build_guide.md) and build an environment image on site from the openEuler base image.

The source compilation and unit test commands in the remainder of this guide are executed in the container of the aforementioned specified image or the environment image built on site. For the CANN package, GCC, and Python versions, the unified image building guide prevails. This repository does not maintain them separately.

After the image is built, you must use the `ctr_in.py` command provided in Chapter 7 of the unified image building guide to start and enter the container in an interactive terminal. Do not use a regular `docker run` command to create the container, and do not use `docker exec <container name> bash -c '<command>'` to replace the interactive environment. Otherwise, the Python, GCC, and CANN environment initialization may be skipped.

#### 3.2.2 Compiling and Installing

1. After you enter the interactive container shell opened by `ctr_in.py`, run the following command to clone this repository:

   ```bash
   cd ~
   git clone https://gitcode.com/Ascend/msagent.git
   ```

2. Stay in the same interactive container shell opened by `ctr_in.py` and run the following command in the repository root directory. The command downloads the dependencies and completes the build automatically:

   ```bash
   cd ~/msagent
   python3 build.py
   ```

3. After the build succeeds, the installation package is generated in the `artifacts/` directory. Install it with the following command:

   ```shell
   pip install artifacts/mindstudio_agent-{version}-py3-none-any.whl
   ```

   After the installation, if the following message is displayed, the software is installed successfully.

   ```ColdFusion
   Successfully installed mindstudio-agent-{version}
   ```

   The default build installs `uv`. If `uv` is already installed locally, run `python3 build.py local` to skip the installation.

#### 3.2.3 Running Unit Tests (Optional)

This step is not required for installation. To verify the basic functions of the code, run the following command in the repository root directory:

```shell
python3 build.py test
```

This command installs `uv` and runs the test cases in `tests/ut` and `tests/skills`. The `uv run` command in `scripts/run_ut.sh` prepares the test dependencies automatically. If `uv` is already installed locally, run `python3 build.py test local` to skip the installation. If the command returns exit code 0 and no test case fails, the unit tests pass.

## 4. Verifying the Installation

After the installation, run the following commands to verify that the tool is available:

```shell
msagent --version
msagent --help
```

If the commands output the version information and help information, the installation is successful. If a message indicates that the command does not exist, confirm that the current terminal uses the Python environment where `mindstudio-agent` is installed.

## 5. Upgrading and Uninstalling

`msagent` creates a `.msagent/` local directory in the current working directory to store caches, session history, logs, runtime configuration, and other content.

- Before upgrading, delete the `.msagent/` folder in the current working directory to prevent old caches from affecting the behavior of the new version.
- When uninstalling, if you will no longer use `msagent`, you are also advised to delete the `.msagent/` folder.

Common operation examples:

- Upgrade

  ```shell
  rm -rf .msagent
  pip install mindstudio-agent
  ```

  Starting from **26.1.0-alpha.2**, the Web UI dependency `langgraph-cli[inmem]` has been changed to the optional extra `[web]`. When upgrading with `pip install -U`, the web-related packages installed with the previous version are **not uninstalled automatically**. If you no longer use the Web UI, you can run the following command manually:

  ```shell
  pip uninstall -y langgraph-cli langgraph-api langgraph-runtime-inmem
  ```

- Uninstall

  ```shell
  rm -rf .msagent
  pip uninstall mindstudio-agent
  ```
