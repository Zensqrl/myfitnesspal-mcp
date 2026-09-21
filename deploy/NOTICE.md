# Chromium seccomp profile

`chromium-seccomp.json` derives from Microsoft's Playwright v1.58.0
[`utils/docker/seccomp_profile.json`](https://github.com/microsoft/playwright/blob/v1.58.0/utils/docker/seccomp_profile.json),
licensed under Apache-2.0. It retains default-deny behavior and permits user
namespace creation for Chromium's sandbox. It is used only on the browser
service. See [Playwright's Docker guidance](https://playwright.dev/python/docs/docker).

The profile additionally returns ENOSYS for clone3 so modern glibc can fall
back to the allowed clone syscall. It allows chroot for Chromium's sandbox
inside its user namespace (the kernel still checks namespace capabilities).
No privileged mode or added host capabilities are needed. Chromium's own
sandbox stays enabled.
