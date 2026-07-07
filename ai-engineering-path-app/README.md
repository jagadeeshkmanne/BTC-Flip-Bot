# Production AI Engineering Roadmap

Static React app for GitHub Pages.

## Run Locally

Open `index.html` directly, or serve the folder:

```bash
python3 -m http.server 8080
```

Then open `http://localhost:8080`.

## Deploy To GitHub Pages

1. Push this folder to a GitHub repository.
2. In GitHub, go to `Settings -> Pages`.
3. Choose `Deploy from a branch`.
4. Select the branch and this folder as the Pages source, or copy these files into `/docs` and select `/docs`.

The app uses React from a CDN and does not require a build step.
