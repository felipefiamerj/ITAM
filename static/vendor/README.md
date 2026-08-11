# Vendored frontend assets

These assets are committed so production pages do not depend on public CDNs at runtime.

- Bootstrap 5.3.3: `bootstrap/5.3.3/`
- Font Awesome Free 6.5.2: `fontawesome/6.5.2/`
- Chart.js 4.4.3: `chart.js/4.4.3/`
- Google Fonts CSS/fonts for Inter and Sora: `google-fonts/inter-sora/`

If any vendored file is updated, update the SRI hashes in the templates that reference it.
