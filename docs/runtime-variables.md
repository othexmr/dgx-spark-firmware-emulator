# The EFI runtime variable service

Why `efibootmgr` and `efivars` fail on this profile, what already works, and the order in which the
remainder can be implemented without guessing.

## The chain, and which links are measured

```
Linux efivars / efibootmgr
  → firmware runtime GetVariable / SetVariable in EFI runtime memory
    → MM communication buffer at 0x933e0000 (128 KiB), FF-A direct request to endpoint 0x8004
      → STMM, running as a secure image this monitor boots ("MON: boot STMM" in every run)
        → the flash service at endpoint 0x8002, which this monitor serves
          → the variable partition of the NOR model (32 MiB at flash 0x03000000)
```

| link | state |
| --- | --- |
| firmware runtime → `0x8004` | **measured**: `monitor.c` saves the non-secure context and enters the secure one with the frame (`DIRECT_REQ`, NS, endpoint `0x8004`) |
| STMM running | **measured**: it reports `MON: STMM waiting` and answers `DIRECT_RESP` at level 2 |
| STMM → flash service `0x8002` | **measured**: the monitor serves reads, program and erase through the same model the flash lane uses, with the latch policy at `0x1e3a4` |
| the variable store itself | **modelled**: 32 MiB variable partition, seeded, with a live overlay |
| ROT services `0x12`, `0x29` | **refused**: the opt-in `spark-rot-error-replies` transport answers status `1` (unavailable) to every request |

So the transport and the store exist. What is missing is the secure world's *cryptographic* dependency:
STMM asks the ROT for two services and gets a refusal.

## Why the refusal is not simply "return success"

Two facts from earlier work close that shortcut:

- Answering the `0x8004` pair with flash semantics — the obvious one-line fix — makes the firmware take
  a synchronous exception immediately (`Synchronous Exception at 0x0000000084…`). Endpoint `0x8004` is a
  different service with a different protocol, not the flash service renumbered.
- The `spark-rot-error-replies` transport exists to prove a *failure* scenario. Its own test asserts it
  never returns service success (`tests/test_qemu_profile.py`,
  `test_rot_failure_transport_never_returns_service_success`), and the comment in the model says it
  "must never be presented as a successful security service". Answering `0` there would be a lie about
  authentication, not an implementation.

## What is implemented now

The transport records the **whole** refused request, not just its service id:

```
spark ROT: service 0x29 refused; request[2] = 0x00000129 0x…
```

That is the only sound starting point: a service implemented from its own refused requests is
reconstructed from evidence, while a service implemented from a guess about what a ROT "probably" does
is how the firmware was crashed before.

## Order of work

1. **Capture both services.** Run the firmware boot with the model's log enabled and record every
   `service 0x12` and `service 0x29` request with its full payload: which words are constants, which
   are addresses, which change between boots, and what the caller does with the reply it gets back.
   Two runs are the minimum, because one run cannot distinguish a constant from a value that happened
   to be constant.
2. **Name the request shape.** From the payload and the caller's subsequent behaviour, write down the
   request and reply layout before implementing anything. If the shape cannot be named from two
   captures, capture more — do not interpolate.
3. **Implement only what the capture shows**, one service at a time, each with a probe that exercises
   the exchange through the transport rather than asserting on source text. `0x29` is the smaller
   request (one payload word observed) and is the first one to attempt.
4. **Then remove `efi=noruntime` from the profile command line** and assert the milestone that has never
   held: the guest mounts `efivars` and `efibootmgr` lists and creates an entry. Until that asserts, the
   profile keeps the flag and the limitation is stated rather than papered over — which is what
   `docs/linux-under-the-profile.md` already records.
5. **Re-run the installer** afterwards: its last failing step is `efibootmgr --create`, so a working
   variable service should turn `Error: Failed to find the newly created Ubuntu entry` into a success,
   and that is a far better proof than any unit test.

## What this will not do

It will not make the profile authenticate anything. A ROT service that returns a well-formed answer to
a firmware caller is a functional model, not a security boundary, and nothing here should be described
as secure boot or measured boot. The value is that the firmware's own code paths stop failing for want
of an answer, which is what a fidelity emulator is for.

## What the first functional answer changed, measured

With `--rot-functional`, the two services the capture shows the firmware calling are answered with
status 0 and no payload, everything else still refused:

```
spark ROT: service 0x29 refused; request[2] = 0x00000129 0x90000000
spark ROT: service 0x29 answered functionally
spark ROT: service 0x12 refused; request[4] = 0x00000312 0x90000000 0x00000001 0xfcc40d97
spark ROT: service 0x12 answered functionally
spark ROT: service 0x12 refused; request[4] = 0x00000312 0x90000000 0x00000001 0xb6db6764
```

Three observations, none of them inference:

- **The fourth word changes between calls** — `0xfcc40d97` then `0xb6db6764` — so `0x12` is asked about a
  value that varies, which is what a digest or checksum of an address range looks like.
- **The communication buffer's status byte moves** (`0x28`, `0x8d`, `0x44`) where it was frozen while the
  services were refused. The earlier claim that the buffer was byte-identical on every retry holds only
  under the refusing transport, and is corrected here.
- **The firmware passes the checkpoint it used to hang at** (`A9`, `AB`) and reaches the setup UI.

What that does *not* establish is that a boot completes: the setup UI appeared in a run where this
harness also typed ESC at the firmware's `Press <DEL>` prompt, and ESC is how AMI enters Setup. The
harness therefore grew `--no-setup-escape`, so our own keypress cannot be mistaken for the firmware's
behaviour, and the disk-boot question is being re-asked with it suppressed. A functional ROT answer is
still not an authentication boundary and nothing here claims one.

## The request shape, as far as the captures support it

Both services carry a leading word whose low byte is the service id and whose upper bits are a length
or count field; the words after it are the arguments. Measured, not inferred:

| service | request words | repeats |
| --- | --- | --- |
| `0x29` | `0x00000129 0x90000000` | once per boot |
| `0x12` | `0x00000312 0x90000000 0x00000001 0xfcc40d97` then `… 0xb6db6764` | twice, with a different fourth word |

What that supports: `0x29` is asked once, with one argument (`0x90000000`, an address in the secure
region); `0x12` is asked about a 32-bit value that differs between calls at the same address with the same
second word, which is the shape of a digest or checksum request rather than a read. What it does **not**
support is a reply format: the functional transport answers status 0 with no payload words, and the
firmware's own subsequent behaviour is the only evidence for whether that was enough — which is what the
checkpoint advance and the moving buffer status showed.

Naming a reply layout from these captures alone would be a guess, and guessing at this interface is what
took the firmware down once before. The honest state is: request shape measured, reply shape still
unknown beyond "status 0 is accepted".

