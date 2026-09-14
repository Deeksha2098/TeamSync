# TeamSync — easiest online sharing

This project is prepared for:
- Backend: Render
- Frontend: Vercel

After deployment you can share the Vercel HTTPS link directly on WhatsApp. Your laptop does NOT need to stay on.

## 1. Deploy backend on Render

1. Put this project in a GitHub repository.
2. Open Render and choose **New → Blueprint**.
3. Select the repository and use the included `render.yaml`.
4. Wait for deployment.
5. Copy the backend URL, for example:
   `https://teamsync-backend.onrender.com`

## 2. Deploy frontend on Vercel

1. Open Vercel and import the same GitHub repository.
2. Set **Root Directory** to `frontend`.
3. Build command: `npm run build`
4. Output directory: `dist`
5. Add environment variable:
   `VITE_API_URL=https://YOUR-BACKEND-URL.onrender.com/api`
6. Deploy.

## 3. Allow the Vercel URL in the backend

In Render → backend → Environment Variables, set:
`CORS_ORIGINS=https://YOUR-APP.vercel.app`

Also set:
`PUBLIC_APP_URL=https://YOUR-APP.vercel.app`

Redeploy the backend.

## 4. Share

Open the Vercel URL on your phone and send that HTTPS link on WhatsApp.

### Important
The included backend currently uses SQLite. Render's normal filesystem is not persistent across service replacement/redeploys. For a college demo this is fine; for permanent production data, move the database to PostgreSQL.
