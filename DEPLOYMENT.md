# TeamSync Deployment / Demo Notes

## Local demo

Backend: `http://127.0.0.1:8000`
Frontend: `http://localhost:5173`
Health endpoint: `http://127.0.0.1:8000/api/health`

The SQLite database is created automatically as `teamsync.db` inside the backend working directory.

## Production notes

For a real deployment, set a long random `SECRET_KEY`, use a production database, configure HTTPS/WSS, and configure a real SMTP provider. The browser live transcription feature requires a supported browser and microphone permission.


## Required production environment

Frontend (`frontend/.env`):
```env
VITE_API_URL=https://YOUR-API-DOMAIN/api
VITE_PUBLIC_APP_URL=https://YOUR-FRONTEND-DOMAIN
```

Backend (`backend/.env`):
```env
SECRET_KEY=<long-random-secret>
DATABASE_URL=sqlite:///./teamsync.db
CORS_ORIGINS=https://YOUR-FRONTEND-DOMAIN
PUBLIC_APP_URL=https://YOUR-FRONTEND-DOMAIN
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
TURN_URL=
TURN_USERNAME=
TURN_CREDENTIAL=
```

Use a real HTTPS domain for both frontend and API. `localhost` must not be used in invitations sent to other devices.
