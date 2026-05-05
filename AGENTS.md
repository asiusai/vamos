# AGENTS

## Dragon cleanup constraints

- Keep cleaning the vamos Dragon image until the remaining added files are justified.
- Do not touch `kernel/patches/*`; previous patch deletion broke boot/NCM.
- Do not remove `kernel/configs/vamos.config` unless explicitly requested.
- Preserve Dragon boot, NCM, SSH, WiFi, Bluetooth, camera snapshots, and camera FPS.
- After cleanup changes, run `./vamos build all`, flash with `./vamos flash all`, then run `./dragon.py health`.
- Model replay must not regress from the current baseline: keep `modelV2` around `0.05s` max and `0.04s` average. Treat worse timing as a cleanup regression unless clearly explained.
- MAKE SURE THAT THE OUTPUTS ARE ALSO CORRECT, VERIFY IT MULTIPLE TIMES, BAD OUTPUT IS MUCH WORSE THAN SLOW MODEL.
- Keep a progress/log file with what was tried, what worked, what failed, and any measured performance impact.
