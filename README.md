# Smart Attendance System (Face Recognition + Blink Liveness)

Frontend: HTML, CSS, JavaScript | Server/Backend: Python (Flask + Waitress) | Database: SQLite | AI: OpenCV + face_recognition (dlib)

## Project structure
```
app.py            Flask app: API, auth, face recognition, reports, alerts
server.py         Server launcher (Waitress HTTP, or HTTPS for camera access on other devices)
static/
  student.html/js   Student portal (own attendance only)
  admin.html/app.js Admin / teacher portal
  style.css
.env.example      Configuration template
Dockerfile        Container build
```

## Setup
1. Install Python 3.9 - 3.11.
2. Install dlib. Windows: install Visual Studio C++ Build Tools, then `pip install cmake dlib`. Easier: with Anaconda run `conda install -c conda-forge dlib`.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and set `ADMIN_PASSWORD` (default is `admin123`).

## Run the server
| Command | Use |
|---|---|
| `python server.py` | Production-style server (Waitress) at http://localhost:5000 |
| `python server.py --https` | HTTPS server, needed when other devices open the site and use the camera |
| `python app.py` | Development mode with auto-reload |

Pages: Student `http://localhost:5000/` | Admin and teacher `http://localhost:5000/admin`

Browsers allow the camera only on `localhost` or HTTPS. For a LAN demo run `python server.py --https` and open `https://<server-ip>:5000` (accept the certificate warning). To use your own certificate put `cert.pem` and `key.pem` next to `server.py`.

Default admin login: `admin` / the `ADMIN_PASSWORD` you set.

## Docker (optional)
```
docker build -t smart-attendance .
docker run -p 5000:5000 -v attendance-data:/data --env-file .env smart-attendance
```
Open http://localhost:5000. The database is kept in the `attendance-data` volume. For other devices put an HTTPS reverse proxy (Nginx or Caddy) in front.

## How to use
1. Admin page, Students tab: enter roll number, name, emails and an optional login password (empty = roll number), then click "Capture face and register".
2. Take attendance tab: enter the subject, click "Scan face" and blink once. Photos and screens are rejected by the blink check.
3. Reports tab: attendance log, percentages, 7-day chart, CSV export, low-attendance email alerts.
4. Student page: students sign in with their roll number to see only their own attendance, subject-wise percentages, log, CSV, and can change their password. Admin can reset a student's password from the Students tab.

## Email alerts
Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` in `.env` (Gmail needs an App Password). Without them the alert button only lists the students below the threshold.

## Settings in app.py
- `TOLERANCE` (0.5): lower is stricter face matching
- `EAR_CLOSED` (0.21): blink sensitivity

## Security notes
- Passwords are stored hashed; a random session secret is generated into `secret.key` (keep it private).
- Change the default admin password before any demo or deployment.

## Limitations and future scope
One person per scan (largest face). Future: multi-face CCTV mode, mobile app, mask-aware recognition, cloud deployment, ERP integration.
