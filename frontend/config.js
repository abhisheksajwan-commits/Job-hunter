// Job Scout frontend config.
//
// After you deploy the backend to Render, copy its URL into API_BASE_URL
// below (no trailing slash), e.g. "https://job-scout.onrender.com".
// Locally, it already points at a backend running on your machine.

const API_BASE_URL = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
  ? "http://localhost:8000"
  : "https://YOUR-BACKEND-NAME.onrender.com";
