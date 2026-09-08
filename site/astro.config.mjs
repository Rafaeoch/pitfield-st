import { defineConfig } from 'astro/config';

// Static output only. Nothing renders on request — every page the site serves
// is a file, and every number on it was computed by the pipeline before deploy.
export default defineConfig({
  // Required for canonical URLs, Open Graph tags and the sitemap. Without it
  // Astro cannot build an absolute URL, and a shared link previews as nothing.
  site: 'https://pitfieldstresearch.com',
  output: 'static',
  build: { format: 'directory' },
  server: { port: 4321 },
});
