# TeamSync - Phone + Computer + WhatsApp Deployment

This package is prepared for Render. It creates two public HTTPS services:
- teamsync-backend (FastAPI)
- teamsync-frontend (React/Vite static site)

## 1. Upload to GitHub
Create a GitHub repository named TeamSync and upload this entire folder.

## 2. Create Render Blueprint
Open Render Dashboard -> New -> Blueprint, select the GitHub repository and use render.yaml.

During the first setup, Render asks for these values:

### Backend CORS_ORIGINS
Temporarily enter:
*

### Backend PUBLIC_APP_URL
Temporarily enter:
https://teamsync-frontend.onrender.com

### Frontend VITE_API_URL
Enter your actual backend URL plus /api:
https://teamsync-backend.onrender.com/api

### Frontend VITE_PUBLIC_APP_URL
Enter your actual frontend URL:
https://teamsync-frontend.onrender.com

If Render generates different service names/URLs, use the URLs shown in the Render dashboard.

## 3. After both services deploy
Open the frontend URL on your computer and phone.

Example:
https://teamsync-frontend.onrender.com

Meeting links will look like:
https://teamsync-frontend.onrender.com/meeting/join/1

Those links can be sent through WhatsApp and opened from phones, laptops, desktops and tablets.

## 4. Important: update CORS
For a quick college demo, `*` works for basic browser access. For a cleaner production setup, change the backend CORS_ORIGINS value to your exact frontend URL:
https://teamsync-frontend.onrender.com

Then redeploy the backend.

## 5. Important: database
This project uses SQLite by default. Render service files are not intended as permanent database storage. For a college demo, it is fine for testing, but for permanent production data use a persistent database such as PostgreSQL.

## 6. Camera/microphone
The deployed frontend is HTTPS, which is required by browsers for camera/microphone access on normal devices. The meeting signaling backend remains available through the deployed API/WebSocket endpoints.
