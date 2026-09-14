# TeamSync - Easy Public Meeting Links

API-key-free TeamSync mini project.

## One-click public run
Double-click `START.bat`. It starts the backend, frontend, automatically obtains a temporary public HTTPS URL, and opens it.

After you schedule a meeting, Calendar shows a unique meeting URL. Use **Copy link** or **Share invitation / WhatsApp**.

No GitHub, Render, or Vercel is required for a temporary public demo link. Keep the START window running.

## Local run
Backend: `run_backend.bat`
Frontend: `run_frontend.bat`
Local URL: `http://localhost:5173` (or the next free Vite port).

PUBLIC MEETING LINK (FIXED)
---------------------------
START_TEAMSYNC.cmd now starts LocalTunnel automatically, detects the HTTPS loca.lt URL, writes it to backend/public_url.txt, and the Share Meeting screen refreshes it automatically. Keep the launcher window open while using the public URL.
