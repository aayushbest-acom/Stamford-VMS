# Patch queues

Patch files are distributed separately in `StamfordVMS-Day2-Patches.zip` to keep this Day 1 baseline reproducible.

The Day 2 starter series is based on the Day 1 repository baseline and is applied in order with:

```powershell
git am .\patches\day2\0001-*.patch
git am .\patches\day2\0002-*.patch
```

The starter series adds the lifecycle contract and a guarded contract test harness; it does **not** claim to implement Genetec check-in/checkout before the supported Security Center 5.11 SDK operations and read-back fields are confirmed. On a repository that already contains these files, do not apply the patch series again.
