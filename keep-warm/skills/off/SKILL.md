---
name: off
description: Stop keeping this conversation's prompt cache warm. Use when the user says "stop warm", "keep warm off", "stop keeping this warm" or similar.
---

The scripts are in the plugin's `scripts` folder, two levels up from this skill's base directory (named above
as "Base directory for this skill"). Below, `SCRIPTS` stands for that folder: write the real path in each command.

```bash
bash "SCRIPTS/ctl.sh" off
```

That also stops a watcher that was started as a background task; its task then reports exit code 4, which is the
stop, so do not start it again. Then `CronList`, and `CronDelete` any job whose prompt begins `Cache keep-alive` or
`Keep-warm stop`. Say in one line that it is off.
