# DGX Spark Firmware Emulator

Run DGX Spark SoC boot firmware in QEMU, with original models for the platform
interfaces it needs. The unchanged SoC **2.158.15** UEFI and Standalone MM images
boot to interactive AMI setup and execute an EFI application over native IPv4
PXE. Keyboard navigation, all 13 architectural protocols and persistent NVRAM
are verified. See [measured boot status](docs/boot-status.md) for the evidence.

This is an independent project, unaffiliated with NVIDIA, MediaTek, AMI
or QEMU. Firmware is downloaded from its official source into ignored
local storage and is never redistributed through this repository.

## Requirements

Use macOS or Linux with Python 3.10+, Git, 7-Zip (`7zz` or `7z`), Clang, LLD,
LLVM objcopy and the [QEMU build dependencies](qemu/README.md). Homebrew's
`llvm` and `lld` provide the monitor/probe toolchain on macOS; Ubuntu packages
are `clang lld llvm`.

## First use

From a clean clone, in this order. Steps 1 and 2 need no vendor bytes; step 3 is the
first that does.

```sh
python3 -m unittest discover -s tests          # fast tests, no firmware needed
python3 tools/build_qemu.py                    # applies qemu/patches/, builds qemu-system-aarch64
python3 tools/prepare_mm.py --version 2.158.15 # downloads the pinned release, verifies its hashes
python3 tools/boot_spark.py --smoke-test       # boots to setup and verifies navigation
```

`boot_spark.py` prepares the release on its own if it is missing, so step 3 is only
needed to see the prerequisite fail early or to prepare an offline run. Two kinds of
test skip rather than fail: the boot cases need the prepared release *and* the lab's
own store captures under `firmware/state/`, which no download creates. Their skip
messages name the path that is missing.

## Build and boot

Run from the repository root:

```sh
python3 tools/build_qemu.py
python3 tools/boot_spark.py
```

The runner downloads the pinned official LVFS SoC update, verifies its cabinet,
capsule, UEFI and MM hashes, then builds the original lab monitor and boots one
AArch64 CPU under TCG. AMI setup opens over the serial terminal. Arrow keys
navigate; **Ctrl-C** ends the session and saves the emulated variable store.
A warm reset is modelled: the watchdog resets the guest, the flash keeps what it programmed, and the boot image is re-read from the flash at each start.

## Verify setup and PXE

```sh
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --pxe-test --state-file firmware/state/pxe-nor.bin
```

| Check | Success requires |
| --- | --- |
| Setup | Actual AMI setup, right-arrow navigation to Advanced, all 13 architectural protocols and a saved NOR snapshot |
| IPv4 PXE | Matching DHCP discover/offer/request/ACK, an acknowledged TFTP download matching the exact EFI probe, a fresh execution token and all 13 protocols |

Both checks stop automatically and save NVRAM. Run them again with the same
state file to check a subsequent cold boot. A fresh NOR store uses the vendor
firmware's default IPv4 PXE settings; an existing store must have network boot
enabled. The modes are mutually exclusive. Increase `--timeout` on a slower host;
`python3 tools/boot_spark.py --help` lists all options.

PXE uses Spark's bundled Realtek UNDI/SNP/DHCP/PXE drivers and an original
RTL8168C compatibility NIC. QEMU's restricted user network provides DHCP/TFTP;
only the generated original EFI probe is served. No additional EFI network
driver or iPXE is loaded.

## What is next

In the order it is taken. Each row says what is measured today, not what is hoped for.

| Work item | Where it stands | What closes it |
| --- | --- | --- |
| The recovery installer's disk step | The window is in: the profile backs `PCIF`'s ECAM at `0x29000000`, the guest **enumerates both NVMe controllers** (`000f:00:02.0`, `000f:00:03.0`, class `0x010802`, BARs inside the bridge's `0x24000000–0x281fffff` window) and QEMU's GIC ITS is instantiated where the guest's own MADT says. The driver then returns `-EPROBE_DEFER` before printing anything, so no disk appears — excluded already: the second namespace, a modelled root port, MSI (`pci=nomsi`) and the IOMMU (`iommu=off`) | Find what `nvme` is waiting for. `dyndbg=+p` names the driver and the gap: it probes for 2.1 ms and returns `-517` with nothing logged between, so the wait is inside its own probe before its first message; `pci=nomsi` and `iommu=off` change neither, the namespace count is irrelevant, and the same kernel drives the same QEMU device on a plain `virt` machine. The remaining difference is the guest's view: segment `0x0f` behind `PCIF`, not segment 0. The guest will not answer questions on its own — `init=/bin/sh` is ignored because `/init` lives in the kernel's **embedded** initramfs and there is no shell on that path — so the next step is to extract that initramfs and add the diagnostic there (`/sys/kernel/debug/devices_deferred`, a 4 KiB config dump). Typing is solved: `--console-script` drives the guest console over QMP, tested on a guest that reads input |
| The second CPU | The monitor now answers PSCI truthfully — `AFFINITY_INFO` reports the CPUs the build models and `PSCI_FEATURES` advertises only that, while `CPU_ON`/`CPU_OFF` answer `NOT_SUPPORTED` because no release path exists; a two-CPU boot still activates one processor, which is the platform description the guest reads, not a missing PSCI answer | A secondary release path at EL2 with per-CPU stacks, then a profile whose MADT and loader tag agree with `-smp` |
| The eSPI link and the SMMU | The fan service behind eSPI is modelled and was measured on hardware; the bus itself and the SMMU are not, so a guest that probes an unbacked aperture dies on an external abort | A boot that reaches them, and their register protocols, which are not modelled |
| Vendor Linux without `efi=noruntime` | The kernel's `efisubsys_init` stalls in an FF-A direct-request loop the monitor never answers | A direct-request response in the monitor |

A run whose guest left boot services no longer fails for a missing architectural-protocol table: that
memory belongs to the guest once the payload calls `ExitBootServices`, so the record says the table was
not observable instead. A table that is still found is checked exactly as before.

## Local files

| Path | Contents |
| --- | --- |
| `firmware/downloads/`, `firmware/prepared/` | Pinned vendor inputs and unchanged extracted images |
| `firmware/state/` | Persistent emulated NOR; default `spark-nor.bin` |
| `firmware/runs/<UTC timestamp>/` | UART/QEMU logs, memory snapshots, PXE captures, generated probe and `run.json` |
| `firmware/hardware/` | Read-only hardware inventory evidence |
| `build/` | Upstream QEMU sources, dependencies and generated binaries |

These directories are Git-ignored. Select a separate store with
`--state-file firmware/state/another-nor.bin`. Failed runs preserve the previous
store, and concurrent runs cannot share it. Never attach vendor images or
captures to commits, releases, issues or CI artifacts. See
[firmware handling](firmware/README.md).

## Model scope and development

The lab executes real UEFI and MM using a synthetic loader/monitor environment.
Earlier silicon boot stages, GPU and physical NIC firmware, production security
services, secure stage-2 isolation, migration and reset paths beyond the modelled
watchdog are outside the verified result. IPv6/HTTP boot and a manufacturer's
provisioned Secure Boot policy remain untested. The installed Linux image and
[lab-key Secure Boot chain](docs/linux-under-the-profile.md#lab-platform-secure-boot-provisioning)
are exercised separately; that does not authenticate earlier silicon boot
stages. Some board/SMBIOS fields are placeholders.

QEMU is the backend for the SoC/UEFI path. Extending it to isolated controllers
requires identifying their ISA and load contract. A complete Spark model also
needs the platform interfaces exercised by its firmware.

- [Boot status and limitations](docs/boot-status.md)
- [QEMU profile and compatibility NIC](qemu/README.md)
- [Monitor, flash, clock and entropy services](monitor/README.md)
- [FF-A sharing and STMM's communication-buffer contract](docs/mm-sharing.md)
- [The EFI runtime variable service: what fails, and the order to implement it](docs/runtime-variables.md)
- [Development, diagnostics and hardware collection](docs/development.md)
- [What the vendor capsule contains](docs/hardware-findings.md) — the component map's declared base, the FF-A
  manifests inside `STMM`/`FLASH`/`ESPI`, and why fifteen components are encrypted
- [What each lever can and cannot do](docs/development.md#what-the-emulator-can-prove-about-the-levers-in-one-place)
- [Backend decision](docs/architecture.md), [roadmap](docs/roadmap.md) and [primary sources](research/sources.json)

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_publication.py
```

The Apache-2.0 license covers original tools, monitor and documentation. The QEMU
patch is GPL-2.0-or-later; firmware and external projects retain their licenses.
