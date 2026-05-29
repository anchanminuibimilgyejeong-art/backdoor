# Mobile Data Mirror Hosted Relay

This is the cloud relay server for Mobile Data Mirror.

## Render Deploy

1. Upload this `hosted-relay` folder to a GitHub repository.
2. Open Render.
3. New > Web Service.
4. Connect the repository.
5. Use these settings:

```text
Runtime: Python
Build Command: python --version
Start Command: python server.py
Health Check Path: /health
```

6. Add an environment variable:

```text
MIRROR_PASSWORD=your-password-here
```

After deploy, Render gives a URL like:

```text
https://mobile-data-mirror.onrender.com
```

Use that as the Android app server address.

Viewer URL:

```text
https://mobile-data-mirror.onrender.com/viewer?room=test
```

The Android app and viewer must use the same room and password.
If `MIRROR_PASSWORD` is set on the server, the Android app and viewer password must match that environment variable.
