# DevPilot frontend

DevPilot is a React and TypeScript dashboard for debugging GitHub Actions. It uses Tailwind CSS, React Router, and the DevPilot FastAPI service.

## Run locally

Use Node.js 20.19+ in the 20.x series, or Node.js 22.12 or newer. From `devpilot/frontend`, run:

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev
```

On macOS or Linux, use `cp .env.example .env.local` for the copy step.

Start the separate FastAPI backend and set `VITE_API_BASE_URL` in `.env.local` to its origin (defaults to `http://localhost:8000`). Leave `/api/v1` out of the value; the API client adds it. If frontend and backend use different origins, configure the backend to allow the frontend origin (typically `http://localhost:5173`) via CORS. GitHub and AI provider credentials belong on the backend. Vite environment values are public in the browser.

Open the URL printed by Vite. Add a repository with its GitHub owner and name, open a failed workflow, inspect its logs, and select **Analyze Failure**. Queued and running analyses update automatically while their detail page is open. The Analyses page keeps a browser-local index of requested analysis IDs and workflow labels so you can reopen reports. The backend serves report contents each time.

The dashboard counts the runs returned by the API for registered repositories. If one repository’s runs fail to load, the dashboard shows the error and counts the runs it could retrieve.

## Build

```text
npm run typecheck
npm run build
npm run preview
```

Deploy the contents of `dist/` to a static host and configure it to serve `index.html` for frontend routes such as `/repositories/1`. Set `VITE_API_BASE_URL` before building for that environment. Use an HTTPS backend origin when hosting the frontend over HTTPS.
