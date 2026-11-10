/* SPDX-License-Identifier: Apache-2.0
 * A guest-side probe used as `/init`: it mounts what it needs, prints a few answers, and stops.
 *
 * Injection: the kernel unpacks the external initrd *after* the embedded one, so appending this
 * binary as `/init` to the vendor's INITRD shadows the vendor init — measured, and with a
 * consequence worth knowing: the vendor init is what loads the kernel modules, so a probe that
 * replaces it also removes the NVMe driver from the boot. Use it to read state that exists without
 * userspace, or hand control back once you know where the vendor init lives.
 *
 * The vendor kernel's own `/init` lives in an embedded initramfs with no shell on any path this
 * image can reach, so a diagnostic cannot be a script — it has to be a static ELF that speaks to the
 * kernel directly. This is that: raw syscalls only, no libc, no assumptions about what the rest of
 * the rootfs contains.
 *
 * It answers one question, the one the emulator could not: why does a PCIe function's driver return
 * -EPROBE_DEFER after the guest has enumerated it? `/sys/kernel/debug/devices_deferred` names the
 * device and what it is waiting for.
 */
#define SYS_openat   56
#define SYS_close    57
#define SYS_read     63
#define SYS_write    64
#define SYS_mount    40
#define SYS_mkdirat  34
#define SYS_getdents64 61
#define SYS_nanosleep 101

static long sys(long number, long a, long b, long c, long d, long e, long f)
{
    register long x8 __asm__("x8") = number;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    register long x3 __asm__("x3") = d;
    register long x4 __asm__("x4") = e;
    register long x5 __asm__("x5") = f;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
                     : "memory");
    return x0;
}

static unsigned long length(const char *s)
{
    unsigned long n = 0;
    while (s[n]) n++;
    return n;
}

static void puts_(const char *s)
{
    sys(SYS_write, 1, (long)s, (long)length(s), 0, 0, 0);
}

/* List a directory. The vendor init and its shell are somewhere in this rootfs, and which paths
 * exist decides whether an injected init can hand work back to them. */
static void list(const char *path)
{
    char buffer[2048];
    puts_("\n--- ls ");
    puts_(path);
    puts_("\n");
    /* O_RDONLY|O_DIRECTORY */
    long fd = sys(SYS_openat, -100, (long)path, 0x10000, 0, 0, 0);
    if (fd < 0) {
        puts_("(cannot open)\n");
        return;
    }
    long got;
    while ((got = sys(SYS_getdents64, fd, (long)buffer, sizeof buffer, 0, 0, 0)) > 0) {
        for (long i = 0; i < got;) {
            unsigned short reclen = *(unsigned short *)(buffer + i + 16);
            const char *name = buffer + i + 19;
            if (name[0] != '.') {
                puts_(name);
                puts_(" ");
            }
            i += reclen;
        }
    }
    sys(SYS_close, fd, 0, 0, 0, 0, 0);
    puts_("\n");
}

static void show(const char *path)
{
    char buffer[4096];
    long fd = sys(SYS_openat, -100 /* AT_FDCWD */, (long)path, 0, 0, 0, 0);
    puts_("\n--- ");
    puts_(path);
    puts_("\n");
    if (fd < 0) {
        puts_("(cannot open)\n");
        return;
    }
    long got;
    while ((got = sys(SYS_read, fd, (long)buffer, sizeof buffer, 0, 0, 0)) > 0)
        sys(SYS_write, 1, (long)buffer, got, 0, 0, 0);
    sys(SYS_close, fd, 0, 0, 0, 0, 0);
}

static void show_number(const char *label, long value)
{
    char digits[24];
    int i = 23;
    digits[i] = 0;
    int negative = value < 0;
    unsigned long v = negative ? -(unsigned long)value : (unsigned long)value;
    if (!v) digits[--i] = '0';
    while (v) { digits[--i] = (char)('0' + v % 10); v /= 10; }
    puts_(label);
    if (negative) puts_("-");
    puts_(&digits[i]);
    puts_("\n");
}

static void mount_(const char *type, const char *target)
{
    sys(SYS_mkdirat, -100, (long)target, 0755, 0, 0, 0);   /* EEXIST is fine */
    show_number("mount ", sys(SYS_mount, (long)type, (long)target, (long)type, 0, 0, 0));
    puts_(target);
    puts_("\n");
}

void start_c(void)
{
    puts_("\n=== GUEST-PROBE-BEGIN\n");
    mount_("proc", "/proc");
    mount_("sysfs", "/sys");
    mount_("debugfs", "/sys/kernel/debug");
    show("/proc/cmdline");
    list("/");
    list("/bin");
    list("/sbin");
    list("/usr/bin");
    /* The question is what the drivers found, so the probe has to be alive when they run: the NVMe
     * module registers ~12.7 s into this boot, and asking at 4 s finds an empty deferred list simply
     * because nothing has probed yet. Wait past it. */
    puts_("\n--- waiting 45 s for the drivers to probe\n");
    { struct { long sec; long nsec; } nap = {45, 0};
      sys(SYS_nanosleep, (long)&nap, 0, 0, 0, 0, 0); }
    show("/sys/kernel/debug/devices_deferred");
    show("/sys/bus/pci/devices/000f:00:02.0/driver");
    show("/sys/bus/pci/devices/000f:00:02.0/modalias");
    show("/sys/bus/pci/devices/000f:00:02.0/irq");
    show("/sys/bus/pci/devices/000f:00:03.0/driver");
    show("/sys/bus/pci/devices/000f:00:02.0/vendor");
    show("/sys/bus/pci/devices/000f:00:02.0/class");
    show("/sys/kernel/iommu_groups");
    puts_("\n=== GUEST-PROBE-END\n");
    for (;;) {
        struct { long seconds; long nanos; } pause = {3600, 0};
        sys(SYS_nanosleep, (long)&pause, 0, 0, 0, 0, 0);
    }
}

__attribute__((naked)) void _start(void)
{
    __asm__ volatile("bl start_c\n"
                     "1: b 1b\n");
}
